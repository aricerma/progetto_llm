import csv
import json
import time
import random
import sys
import os
import re
from pathlib import Path
from dotenv import load_dotenv

from openai import OpenAI, APIStatusError, APIConnectionError, APITimeoutError

INPUT_CSV = "oscar_miglior_film.csv"
OUTPUT_JSON = "oscar_produzione_genere_llm.json"

BATCH_SIZE = 40
PAUSA_TRA_CHIAMATE = 5
TENTATIVI_MAX = 5
MODELLO = "z-ai/glm-5.3-flash"

load_dotenv()

API_KEY = os.environ.get("OPENROUTER_API_KEY")
if not API_KEY:
    sys.exit("ERRORE: OPENROUTER_API_KEY non trovata (file .env o export).")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=API_KEY,
    timeout=300.0,   # reasoning obbligatorio => risposte piu lente
    max_retries=1,
    default_headers={
        "HTTP-Referer": "http://localhost",
        "X-Title": "oscar-metadata-extractor",
    },
)

PROMPT_SISTEMA = """Sei un esperto di storia del cinema. Ti verrà fornita una lista JSON di film candidati agli Oscar con il relativo anno.
Attingendo alla tua base di conoscenza, determina per ciascun film il 'genere' principale e la 'casa di produzione' principale.
Restituisci ESCLUSIVAMENTE un oggetto JSON con questa struttura esatta, senza aggiungere testo prima o dopo:
{
  "film": [
    {
      "titolo_it": "titolo del film come da input",
      "anno": "anno come da input",
      "genere": "genere cinematografico principale, oppure null se sconosciuto",
      "casa_produzione": "casa di produzione principale, oppure null se sconosciuta"
    }
  ]
}
Non includere ragionamenti, spiegazioni o blocchi markdown: SOLO l'oggetto JSON."""

# Configurazioni da provare in cascata: la prima che il provider accetta viene mantenuta.
# Questo endpoint esige il reasoning: NON tentiamo mai di disattivarlo.
CONFIGS = [
    # 1) JSON mode + reasoning con sforzo ridotto (idealmente escluso dall'output)
    {"response_format": {"type": "json_object"},
     "extra_body": {"reasoning": {"effort": "low", "exclude": True}}},
    # 2) JSON mode, parametri reasoning omessi (uso default del provider)
    {"response_format": {"type": "json_object"}},
    # 3) nessun parametro extra
    {},
]
_config_idx = 0


def dettaglio_errore(e) -> str:
    try:
        return (e.response.text or str(e))[:500]
    except Exception:
        return str(e)[:500]


class RispostaTroncata(Exception):
    pass


def chiama_modello(prompt_utente: str) -> str:
    global _config_idx

    for tentativo in range(1, TENTATIVI_MAX + 1):
        kwargs = dict(
            model=MODELLO,
            messages=[
                {"role": "system", "content": PROMPT_SISTEMA},
                {"role": "user", "content": prompt_utente},
            ],
            temperature=0.0,
        )
        kwargs.update(CONFIGS[_config_idx])

        try:
            risposta = client.chat.completions.create(**kwargs)

            if not risposta.choices:
                raise ValueError("Risposta API senza 'choices'.")

            scelta = risposta.choices[0]
            if scelta.finish_reason == "length":
                raise RispostaTroncata()

            messaggio = scelta.message
            testo = messaggio.content

            if testo is None or not testo.strip():
                # Reasoning obbligatorio: se il JSON e' finito nel campo reasoning, proviamo a recuperarlo
                reasoning = getattr(messaggio, "reasoning", None) or \
                            (messaggio.model_extra or {}).get("reasoning")
                if reasoning and "{" in reasoning:
                    print("  [Avviso] content vuoto: tento il recupero dal campo reasoning...")
                    return reasoning
                raise ValueError("Contenuto vuoto o nullo.")

            return testo

        except APIStatusError as e:
            codice = e.status_code

            if codice in (401, 402, 403):
                sys.exit(f"[Fatale {codice}] Autenticazione/crediti: {dettaglio_errore(e)}")
            if codice == 404:
                sys.exit(f"[Fatale 404] Modello '{MODELLO}' inesistente su OpenRouter.")

            if codice == 400:
                print(f"  [400] Dettaglio: {dettaglio_errore(e)}")
                if _config_idx < len(CONFIGS) - 1:
                    _config_idx += 1
                    print(f"  [Fallback] Cambio configurazione richiesta (#{_config_idx + 1}/{len(CONFIGS)}) e riprovo...")
                    continue
                sys.exit("[Fatale 400] Tutte le configurazioni sono state rifiutate. Incolla il dettaglio qui sopra.")

            if codice == 429:
                retry_after = None
                try:
                    retry_after = e.response.headers.get("retry-after")
                except Exception:
                    pass
                attesa = float(retry_after) if retry_after else min(60, 5 * 2 ** (tentativo - 1))
            else:
                attesa = min(60, 5 * 2 ** (tentativo - 1))
            attesa += random.uniform(0, 1.5)
            print(f"  [Tentativo {tentativo}/{TENTATIVI_MAX}] HTTP {codice}: {dettaglio_errore(e)[:150]}")
            print(f"  Riprovo tra {attesa:.0f}s...")
            if tentativo == TENTATIVI_MAX:
                raise
            time.sleep(attesa)

        except (APIConnectionError, APITimeoutError) as e:
            attesa = min(60, 5 * 2 ** (tentativo - 1)) + random.uniform(0, 1.5)
            print(f"  [Tentativo {tentativo}/{TENTATIVI_MAX}] Rete/timeout: {e}. Riprovo tra {attesa:.0f}s...")
            if tentativo == TENTATIVI_MAX:
                raise
            time.sleep(attesa)

        except (ValueError, RispostaTroncata) as e:
            if isinstance(e, RispostaTroncata):
                raise  # gestita da analizza_batch con lo split del batch
            attesa = min(60, 5 * 2 ** (tentativo - 1)) + random.uniform(0, 1.5)
            print(f"  [Tentativo {tentativo}/{TENTATIVI_MAX}] {e}. Riprovo tra {attesa:.0f}s...")
            if tentativo == TENTATIVI_MAX:
                raise
            time.sleep(attesa)

    raise RuntimeError(f"Esauriti i {TENTATIVI_MAX} tentativi con {MODELLO}.")


def estrai_json(testo: str) -> dict:
    pulito = testo.strip()
    pulito = re.sub(r"^```(?:json)?\s*", "", pulito, flags=re.IGNORECASE)
    pulito = re.sub(r"\s*```\s*$", "", pulito).strip()
    try:
        return json.loads(pulito)
    except json.JSONDecodeError:
        pass
    inizio, fine = pulito.find("{"), pulito.rfind("}")
    if inizio != -1 and fine > inizio:
        try:
            return json.loads(pulito[inizio: fine + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"JSON non valido. Output grezzo: {testo[:150]!r}...")


def chiave_norm(titolo, anno) -> str:
    t = re.sub(r"\s+", " ", str(titolo or "")).strip().lower()
    return f"{t}_{str(anno or '').strip()}"


def _analizza_batch_unitario(batch: list) -> dict:
    prompt_utente = json.dumps(
        [{"titolo_it": r["titolo_it"], "anno": r["anno"]} for r in batch],
        ensure_ascii=False,
    )
    dati_json = estrai_json(chiama_modello(prompt_utente))

    if not isinstance(dati_json, dict) or not isinstance(dati_json.get("film"), list):
        raise ValueError("Struttura JSON inattesa: manca l'array 'film'.")

    mappatura = {}
    for f in dati_json.get("film", []):
        if not isinstance(f, dict):
            continue
        mappatura[chiave_norm(f.get("titolo_it"), f.get("anno"))] = {
            "genere": f.get("genere"),
            "casa_produzione": f.get("casa_produzione"),
        }
    return mappatura


def analizza_batch(batch: list) -> dict:
    try:
        return _analizza_batch_unitario(batch)
    except RispostaTroncata:
        if len(batch) < 2:
            raise
        meta = len(batch) // 2
        print(f"  [Troncamento] Risposta troppo lunga: divido il batch ({len(batch)} -> {meta}+{len(batch) - meta})...")
        sinistra = analizza_batch(batch[:meta])
        destra = analizza_batch(batch[meta:])
        sinistra.update(destra)
        return sinistra


def salva_checkpoint(righe: list):
    tmp = Path(OUTPUT_JSON).with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(righe, f, ensure_ascii=False, indent=2)
    tmp.replace(OUTPUT_JSON)


def main():
    if not Path(INPUT_CSV).exists():
        sys.exit(f"ERRORE: file di input non trovato: {INPUT_CSV}")

    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        tutte_le_righe = list(csv.DictReader(f))

    righe_gia_processate = []
    id_processati = set()

    if Path(OUTPUT_JSON).exists():
        try:
            righe_gia_processate = json.loads(Path(OUTPUT_JSON).read_text(encoding="utf-8"))
            id_processati = {f"{r['titolo_it']}_{r['anno']}" for r in righe_gia_processate}
            print(f"Trovato checkpoint: {len(id_processati)} film già processati.")
        except json.JSONDecodeError:
            print("Avviso: File JSON esistente illeggibile. Inizio da zero.")

    righe_da_processare = [r for r in tutte_le_righe if f"{r['titolo_it']}_{r['anno']}" not in id_processati]
    totale_complessivo = len(tutte_le_righe)
    da_processare = len(righe_da_processare)

    if da_processare == 0:
        print("Tutti i record sono già stati analizzati.")
        return

    print(f"Record rimanenti da elaborare: {da_processare} su {totale_complessivo}")
    print(f"Modello: {MODELLO} | Batch: {BATCH_SIZE} | Pausa: {PAUSA_TRA_CHIAMATE}s")

    for i in range(0, da_processare, BATCH_SIZE):
        batch = righe_da_processare[i:i + BATCH_SIZE]
        num_ciclo = (i // BATCH_SIZE) + 1
        totale_cicli = (da_processare // BATCH_SIZE) + (1 if da_processare % BATCH_SIZE != 0 else 0)

        print(f"Processando batch {num_ciclo}/{totale_cicli} ({len(batch)} record)...")

        try:
            mappatura_risultati = analizza_batch(batch)
        except Exception as e:
            print(f"  [Errore API] {type(e).__name__}: {e}")
            print(f"  I progressi completati sono salvi in: {OUTPUT_JSON}")
            sys.exit(1)

        mancanti = 0
        for riga in batch:
            chiave = chiave_norm(riga["titolo_it"], riga["anno"])
            dati_estratti = mappatura_risultati.get(chiave)
            if dati_estratti is None:
                mancanti += 1
                dati_estratti = {"genere": None, "casa_produzione": None}

            nuova_riga = dict(riga)
            nuova_riga["genere"] = dati_estratti["genere"]
            nuova_riga["casa_produzione"] = dati_estratti["casa_produzione"]
            righe_gia_processate.append(nuova_riga)

        if mancanti:
            print(f"  [Avviso] {mancanti} record assenti dalla risposta: campi a null.")

        salva_checkpoint(righe_gia_processate)

        if i + BATCH_SIZE < da_processare:
            time.sleep(PAUSA_TRA_CHIAMATE)

    print(f"\nElaborazione completata. Output in: {Path(OUTPUT_JSON).resolve()}")


if __name__ == "__main__":
    main()