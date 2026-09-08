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

# ----------------- CONFIGURAZIONE (struttura invariata) -----------------
INPUT_CSV = "oscar_miglior_film.csv"
OUTPUT_JSON = "oscar_produzione_genere.json"

BATCH_SIZE = 40
PAUSA_TRA_CHIAMATE = 10
TENTATIVI_MAX = 5
MODELLO = "z-ai/glm-5.3-flash"   # slug OpenRouter di GLM 5.3 Flash

load_dotenv()

API_KEY = os.environ.get("OPENROUTER_API_KEY")
if not API_KEY:
    sys.exit(
        "ERRORE: OPENROUTER_API_KEY non trovata nell'ambiente.\n"
        "Attenzione ai refusi: la variabile corretta è OPENROUTER_API_KEY\n"
        "(nel tuo comando era 'OPENROUTER_API_KET', senza la Y finale).\n\n"
        "Uso corretto:\n"
        '  export OPENROUTER_API_KEY="sk-or-v1-..." && python3 scrapingllm.py\n'
        '  oppure: OPENROUTER_API_KEY="sk-or-v1-..." python3 scrapingllm.py'
    )

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=API_KEY,
    timeout=180.0,      # batch da 40 record => risposte lunghe, timeout generoso
    max_retries=1,      # 1 retry a livello SDK; il resto è gestito manualmente
    default_headers={   # header raccomandati da OpenRouter
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

_json_mode_ok = True  # disattivata automaticamente se il provider non la supporta


def chiama_modello(prompt_utente: str) -> str:
    """Chiamata a OpenRouter con retry, backoff esponenziale e gestione errori robusta."""
    global _json_mode_ok

    for tentativo in range(1, TENTATIVI_MAX + 1):
        kwargs = dict(
            model=MODELLO,
            messages=[
                {"role": "system", "content": PROMPT_SISTEMA},
                {"role": "user", "content": prompt_utente},
            ],
            temperature=0.0,
            # GLM 5.3 Flash ha il 'thinking' attivo di default: lo disattiviamo
            # ed escludiamo il reasoning dall'output: è la causa tipica di
            # message.content = None (l'errore NoneType che avevi).
            extra_body={"reasoning": {"enabled": False, "exclude": True}},
        )
        if _json_mode_ok:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            risposta = client.chat.completions.create(**kwargs)

            if not risposta.choices:
                raise ValueError("Risposta API senza 'choices' (errore a monte del provider).")

            scelta = risposta.choices[0]
            if scelta.finish_reason == "length":
                raise ValueError(
                    "Risposta troncata (finish_reason='length'): riduci BATCH_SIZE (es. 20)."
                )

            messaggio = scelta.message
            testo = messaggio.content

            # >>> FIX dell'errore: 'NoneType' object has no attribute 'strip' <<<
            if testo is None or not testo.strip():
                reasoning = getattr(messaggio, "reasoning", None) or \
                            (messaggio.model_extra or {}).get("reasoning")
                extra = " (ricevuto solo il campo 'reasoning')" if reasoning else ""
                raise ValueError(f"Il modello ha restituito un contenuto vuoto o nullo{extra}.")

            return testo

        except APIStatusError as e:
            codice = e.status_code
            # Errori NON recuperabili: esce subito con messaggio chiaro
            if codice in (401, 402, 403):
                sys.exit(f"[Fatale {codice}] Problema di autenticazione o credito OpenRouter: {e}")
            if codice == 404:
                sys.exit(
                    f"[Fatale 404] Modello '{MODELLO}' non trovato su OpenRouter. "
                    "Verifica lo slug esatto (vedi nota 'verifica slug' nella documentazione dello script)."
                )
            if codice == 400 and "response_format" in str(e):
                _json_mode_ok = False
                print("  [Info] JSON mode non supportata dal provider: riprovo senza 'response_format'.")
                continue
            # 429 (rate limit) e 5xx: recuperabili -> backoff
            if codice == 429:
                retry_after = None
                try:
                    retry_after = e.response.headers.get("retry-after")
                except Exception:
                    pass
                attesa = float(retry_after) if retry_after else min(60, 5 * 2 ** (tentativo - 1))
            else:
                attesa = min(60, 5 * 2 ** (tentativo - 1))
            attesa += random.uniform(0, 1.5)  # jitter anti-thundering-herd
            print(f"  [Tentativo {tentativo}/{TENTATIVI_MAX}] HTTP {codice}: riprovo tra {attesa:.0f}s...")
            if tentativo == TENTATIVI_MAX:
                raise
            time.sleep(attesa)

        except (APIConnectionError, APITimeoutError) as e:
            attesa = min(60, 5 * 2 ** (tentativo - 1)) + random.uniform(0, 1.5)
            print(f"  [Tentativo {tentativo}/{TENTATIVI_MAX}] Rete/timeout: {e}. Riprovo tra {attesa:.0f}s...")
            if tentativo == TENTATIVI_MAX:
                raise
            time.sleep(attesa)

        except ValueError as e:
            attesa = min(60, 5 * 2 ** (tentativo - 1)) + random.uniform(0, 1.5)
            print(f"  [Tentativo {tentativo}/{TENTATIVI_MAX}] {e}. Riprovo tra {attesa:.0f}s...")
            if tentativo == TENTATIVI_MAX:
                raise
            time.sleep(attesa)

    raise RuntimeError(f"Esauriti i {TENTATIVI_MAX} tentativi con il modello {MODELLO}.")


def estrai_json(testo: str) -> dict:
    """Pulisce recinti markdown e parsa il JSON, con fallback robusto."""
    pulito = testo.strip()
    pulito = re.sub(r"^```(?:json)?\s*", "", pulito, flags=re.IGNORECASE)
    pulito = re.sub(r"\s*```\s*$", "", pulito).strip()
    try:
        return json.loads(pulito)
    except json.JSONDecodeError:
        pass
    # Fallback: isola il primo oggetto '{' ... '}' presente nel testo
    inizio, fine = pulito.find("{"), pulito.rfind("}")
    if inizio != -1 and fine > inizio:
        try:
            return json.loads(pulito[inizio: fine + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Il modello non ha restituito un JSON valido. Output grezzo: {testo[:150]!r}...")


def chiave_norm(titolo, anno) -> str:
    """Chiave normalizzata (case/spazi-insensitive) per abbinare input e output del modello."""
    t = re.sub(r"\s+", " ", str(titolo or "")).strip().lower()
    return f"{t}_{str(anno or '').strip()}"


def analizza_batch(batch: list[dict]) -> dict:
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


def salva_checkpoint(righe: list[dict]):
    """Scrittura atomica: evita checkpoint corrotti se il processo si interrompe."""
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
            print(f"  [Avviso] {mancanti} record non trovati nella risposta del modello: campi a null.")

        salva_checkpoint(righe_gia_processate)

        if i + BATCH_SIZE < da_processare:
            time.sleep(PAUSA_TRA_CHIAMATE)

    print(f"\nElaborazione completata. Output in: {Path(OUTPUT_JSON).resolve()}")


if __name__ == "__main__":
    main()