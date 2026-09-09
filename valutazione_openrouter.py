import csv
import json
import time
import random
import os
import sys
import re
from pathlib import Path
from collections import Counter
from dotenv import load_dotenv

from openai import OpenAI, APIStatusError, APIConnectionError, APITimeoutError

FILE_WIKI = "oscar_produzione_genere.json"
FILE_LLM = "oscar_produzione_genere_llm.json"
OUTPUT_CSV = "report_divergenza.csv"

BATCH_SIZE = 40
PAUSA_TRA_CHIAMATE = 5
TENTATIVI_MAX = 5
MODELLO = "z-ai/glm-5.3-flash"

CATEGORIE = {"CONCORDE", "PARZIALE", "DISCORDANTE", "NON_CONFRONTABILE"}
CAMPI_CSV = ["id_film", "esito_genere", "esito_produzione",
             "wiki_genere", "wiki_casa_produzione",
             "llm_genere", "llm_casa_produzione"]

load_dotenv()

API_KEY = os.environ.get("OPENROUTER_API_KEY")
if not API_KEY:
    sys.exit("ERRORE: OPENROUTER_API_KEY non trovata (file .env o export).")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=API_KEY,
    timeout=300.0,
    max_retries=1,
    default_headers={
        "HTTP-Referer": "http://localhost",
        "X-Title": "oscar-divergence-eval",
    },
)

PROMPT_SISTEMA_EVAL = """Sei un analista dati. Ricevi coppie di valori per 'genere' e 'casa_produzione' estratti da Wikipedia (wiki) e generati da un modello (llm).
Classifica ciascun attributo usando ESATTAMENTE queste categorie:
- CONCORDE: Significato equivalente, match esatto, sinonimo o variante di denominazione (es. 'Paramount' vs 'Paramount Pictures').
- PARZIALE: Sovrapposizione parziale (es. 'Drammatico' vs 'Drammatico/Guerra', oppure una major è presente in coproduzione).
- DISCORDANTE: Valori incompatibili o semanticamente differenti.
- NON_CONFRONTABILE: Almeno uno dei due valori è nullo o mancante.

Ricevi un array JSON di oggetti con i campi: id_film, wiki, llm.
Restituisci ESCLUSIVAMENTE un oggetto JSON con questa struttura esatta, senza testo aggiuntivo:
{
  "valutazioni": [
    {"id_film": "id ricevuto in input", "esito_genere": "CATEGORIA", "esito_produzione": "CATEGORIA"}
  ]
}
Le CATEGORIE ammesse sono SOLO: CONCORDE, PARZIALE, DISCORDANTE, NON_CONFRONTABILE.
Includi una valutazione per OGNI id_film ricevuto, nello stesso ordine."""

CONFIGS = [
    {"response_format": {"type": "json_object"},
     "extra_body": {"reasoning": {"effort": "low", "exclude": True}}},
    {"response_format": {"type": "json_object"}},
    {},
]
_config_idx = 0


def dettaglio_errore(e) -> str:
    try:
        return (e.response.text or str(e))[:500]
    except Exception:
        return str(e)[:500]


def chiama_modello(prompt_utente: str) -> str:
    global _config_idx

    for tentativo in range(1, TENTATIVI_MAX + 1):
        kwargs = dict(
            model=MODELLO,
            messages=[
                {"role": "system", "content": PROMPT_SISTEMA_EVAL},
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
                raise ValueError("Risposta troncata (finish_reason='length').")

            messaggio = scelta.message
            testo = messaggio.content

            if testo is None or not testo.strip():
                reasoning = getattr(messaggio, "reasoning", None) or \
                            (messaggio.model_extra or {}).get("reasoning")
                if reasoning and "{" in reasoning:
                    print("  [Avviso] content vuoto: recupero il JSON dal campo reasoning...")
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
                    print(f"  [Fallback] Cambio configurazione (#{_config_idx + 1}/{len(CONFIGS)}) e riprovo...")
                    continue
                sys.exit("[Fatale 400] Tutte le configurazioni rifiutate.")

            if codice == 429 or codice >= 500:
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
            print(f"  [Tentativo {tentativo}/{TENTATIVI_MAX}] Rete/timeout. Riprovo tra {attesa:.0f}s...")
            if tentativo == TENTATIVI_MAX:
                raise
            time.sleep(attesa)

        except ValueError as e:
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


def normalizza_categoria(valore):
    v = re.sub(r"[\s\-]+", "_", str(valore or "").strip().upper())
    return v if v in CATEGORIE else None


def chiave_id(id_film) -> str:
    return re.sub(r"\s+", " ", str(id_film or "")).strip().lower()


def valuta_batch_llm(batch: list) -> dict:
    prompt_payload = json.dumps(batch, ensure_ascii=False)
    dati = estrai_json(chiama_modello(prompt_payload))

    valutazioni = dati.get("valutazioni") if isinstance(dati, dict) else None
    if not isinstance(valutazioni, list):
        raise ValueError("Struttura JSON inattesa: manca l'array 'valutazioni'.")

    mappatura = {}
    for v in valutazioni:
        if not isinstance(v, dict):
            continue
        mappatura[chiave_id(v.get("id_film"))] = {
            "esito_genere": normalizza_categoria(v.get("esito_genere")),
            "esito_produzione": normalizza_categoria(v.get("esito_produzione")),
        }
    return mappatura


def stampa_metriche(risultati: list):
    tot = len(risultati)
    if tot == 0:
        return
    for campo in ["esito_genere", "esito_produzione"]:
        conteggi = Counter(r[campo] for r in risultati)
        parti = [f"{k}: {(v / tot) * 100:.1f}%" for k, v in sorted(conteggi.items())]
        print(f"  [{campo.replace('esito_', '').upper()}] " + " | ".join(parti))


def salva_checkpoint(risultati: list):
    tmp = Path(OUTPUT_CSV).with_suffix(".csv.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CAMPI_CSV)
        writer.writeheader()
        writer.writerows(risultati)
    tmp.replace(OUTPUT_CSV)


def main():
    try:
        dati_wiki = {f"{r['titolo_it']}_{r['anno']}": r for r in json.loads(Path(FILE_WIKI).read_text(encoding="utf-8"))}
        dati_llm = {f"{r['titolo_it']}_{r['anno']}": r for r in json.loads(Path(FILE_LLM).read_text(encoding="utf-8"))}
    except FileNotFoundError as e:
        print(f"Errore: File mancante. {e}")
        return

    risultati = {}
    if Path(OUTPUT_CSV).exists():
        try:
            with open(OUTPUT_CSV, "r", encoding="utf-8", newline="") as f:
                for riga in csv.DictReader(f):
                    if riga.get("id_film"):
                        risultati[riga["id_film"]] = riga
            print(f"Trovato checkpoint: {len(risultati)} record già processati e salvati.")
        except (csv.Error, UnicodeDecodeError):
            print("Avviso: CSV esistente illeggibile. Inizio da zero.")
            risultati = {}

    coppie_da_valutare = []
    for chiave, r_wiki in dati_wiki.items():
        if chiave in risultati:
            continue

        r_llm = dati_llm.get(chiave, {})
        coppie_da_valutare.append({
            "id_film": chiave,
            "wiki": {"genere": r_wiki.get("genere"), "casa_produzione": r_wiki.get("casa_produzione")},
            "llm": {"genere": r_llm.get("genere"), "casa_produzione": r_llm.get("casa_produzione")}
        })

    totale_complessivo = len(dati_wiki)
    da_processare = len(coppie_da_valutare)

    if da_processare == 0:
        print("Tutti i record sono già stati analizzati. Procedura completata.")
        stampa_metriche(list(risultati.values()))
        return

    print(f"Record rimanenti da elaborare: {da_processare} su {totale_complessivo}")
    print(f"Modello: {MODELLO} | Batch: {BATCH_SIZE} | Pausa: {PAUSA_TRA_CHIAMATE}s")

    for i in range(0, da_processare, BATCH_SIZE):
        batch = coppie_da_valutare[i:i + BATCH_SIZE]
        num_ciclo = (i // BATCH_SIZE) + 1
        totale_cicli = (da_processare // BATCH_SIZE) + (1 if da_processare % BATCH_SIZE != 0 else 0)

        print(f"Processando batch {num_ciclo}/{totale_cicli} ({len(batch)} record)...")

        try:
            mappatura = valuta_batch_llm(batch)
        except Exception as e:
            print(f"  [Errore Critico API] {type(e).__name__}: {e}")
            print(f"  I progressi sono salvi nel file: {OUTPUT_CSV}")
            sys.exit(1)

        mancanti = 0
        for elemento in batch:
            valutazione = mappatura.get(chiave_id(elemento["id_film"]))
            esito_g = valutazione["esito_genere"] if valutazione else None
            esito_p = valutazione["esito_produzione"] if valutazione else None
            if esito_g is None or esito_p is None:
                mancanti += 1

            risultati[elemento["id_film"]] = {
                "id_film": elemento["id_film"],
                "esito_genere": esito_g or "ERRORE",
                "esito_produzione": esito_p or "ERRORE",
                "wiki_genere": elemento["wiki"]["genere"],
                "wiki_casa_produzione": elemento["wiki"]["casa_produzione"],
                "llm_genere": elemento["llm"]["genere"],
                "llm_casa_produzione": elemento["llm"]["casa_produzione"],
            }

        if mancanti:
            print(f"  [Avviso] {mancanti} valutazioni assenti/invalidi: marcate come ERRORE.")

        salva_checkpoint(list(risultati.values()))
        stampa_metriche(list(risultati.values()))

        if i + BATCH_SIZE < da_processare:
            time.sleep(PAUSA_TRA_CHIAMATE)

    print(f"\nAnalisi completata. Report salvato in: {Path(OUTPUT_CSV).resolve()}")
    stampa_metriche(list(risultati.values()))


if __name__ == "__main__":
    main()
