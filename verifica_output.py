import json
import time
import os
from pathlib import Path
from dotenv import load_dotenv

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# Assicurati che i nomi dei file corrispondano ai JSON generati negli step precedenti
FILE_WIKI = "oscar_produzione_genere.json" 
FILE_LLM = "oscar_produzione_genere_llm.json"
OUTPUT_REPORT = "report_divergenza.json"

BATCH_SIZE = 40
PAUSA_TRA_CHIAMATE = 15
MODELLO = "gemini-flash-latest"

load_dotenv()
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# ── 1. Schemi Pydantic ───────────────────────────────────────────────────

class ValutazioneCoppia(BaseModel):
    id_film: str
    esito_genere: str = Field(description="CONCORDE | PARZIALE | DISCORDANTE | NON_CONFRONTABILE")
    esito_produzione: str = Field(description="CONCORDE | PARZIALE | DISCORDANTE | NON_CONFRONTABILE")

class RispostaValutazioneBatch(BaseModel):
    valutazioni: list[ValutazioneCoppia]

PROMPT_SISTEMA_EVAL = """Sei un analista dati. Ricevi coppie di valori per 'genere' e 'casa_produzione' estratti da Wikipedia (wiki) e generati da un modello (llm).
Classifica ciascun attributo usando ESATTAMENTE queste categorie:
- CONCORDE: Significato equivalente, match esatto, sinonimo o variante di denominazione (es. 'Paramount' vs 'Paramount Pictures').
- PARZIALE: Sovrapposizione parziale (es. 'Drammatico' vs 'Drammatico/Guerra', oppure una major è presente in coproduzione).
- DISCORDANTE: Valori incompatibili o semanticamente differenti.
- NON_CONFRONTABILE: Almeno uno dei due valori è nullo o mancante."""

# ── 2. Chiamata API Batch ────────────────────────────────────────────────

def valuta_batch_llm(batch: list[dict]) -> dict:
    prompt_payload = json.dumps(batch, ensure_ascii=False)
    try:
        risposta = client.models.generate_content(
            model=MODELLO,
            contents=prompt_payload,
            config=types.GenerateContentConfig(
                system_instruction=PROMPT_SISTEMA_EVAL,
                response_mime_type="application/json",
                response_schema=RispostaValutazioneBatch,
                temperature=0.0
            ),
        )
        # Associa i risultati all'ID per ricostruzione sicura
        return {v.id_film: v for v in risposta.parsed.valutazioni}
    except Exception as e:
        print(f"  [Errore API durante la valutazione]: {e}")
        return {}

# ── 3. Pipeline Principale ───────────────────────────────────────────────

def main():
    try:
        dati_wiki = {f"{r['titolo_it']}_{r['anno']}": r for r in json.loads(Path(FILE_WIKI).read_text(encoding="utf-8"))}
        dati_llm = {f"{r['titolo_it']}_{r['anno']}": r for r in json.loads(Path(FILE_LLM).read_text(encoding="utf-8"))}
    except FileNotFoundError as e:
        print(f"Errore: File mancante. {e}")
        return

    coppie_da_valutare = []
    
    # Costruzione array per l'LLM con tutti i record
    for chiave, r_wiki in dati_wiki.items():
        r_llm = dati_llm.get(chiave, {})
        coppie_da_valutare.append({
            "id_film": chiave,
            "wiki": {"genere": r_wiki.get("genere"), "casa_produzione": r_wiki.get("casa_produzione")},
            "llm": {"genere": r_llm.get("genere"), "casa_produzione": r_llm.get("casa_produzione")}
        })

    totale_record = len(coppie_da_valutare)
    print(f"Record caricati: {totale_record}")
    print(f"Inoltro all'LLM in batch da {BATCH_SIZE}...")

    risultati_finali = []

    # Iterazione batch e rispetto limiti API
    for i in range(0, totale_record, BATCH_SIZE):
        batch = coppie_da_valutare[i:i + BATCH_SIZE]
        num_ciclo = (i // BATCH_SIZE) + 1
        totale_cicli = (totale_record // BATCH_SIZE) + (1 if totale_record % BATCH_SIZE != 0 else 0)
        
        print(f"Processando batch {num_ciclo}/{totale_cicli} ({len(batch)} record)...")

        mappatura = valuta_batch_llm(batch)
        
        for elemento in batch:
            valutazione = mappatura.get(elemento["id_film"])
            risultati_finali.append({
                "id_film": elemento["id_film"],
                "esito_genere": valutazione.esito_genere if valutazione else "ERRORE",
                "esito_produzione": valutazione.esito_produzione if valutazione else "ERRORE",
                "wiki": elemento["wiki"],
                "llm": elemento["llm"]
            })

        if i + BATCH_SIZE < totale_record:
            time.sleep(PAUSA_TRA_CHIAMATE)

    # Calcolo Metriche
    conteggi = {
        "genere": {"CONCORDE": 0, "PARZIALE": 0, "DISCORDANTE": 0, "NON_CONFRONTABILE": 0, "ERRORE": 0},
        "produzione": {"CONCORDE": 0, "PARZIALE": 0, "DISCORDANTE": 0, "NON_CONFRONTABILE": 0, "ERRORE": 0}
    }

    for r in risultati_finali:
        for campo in ["genere", "produzione"]:
            esito = r[f"esito_{campo}"]
            if esito in conteggi[campo]:
                conteggi[campo][esito] += 1

    report = {
        "metadati": {"totale_film_analizzati": totale_record},
        "metriche": {
            campo: {
                stato: f"{(conteggio / totale_record) * 100:.2f}%"
                for stato, conteggio in valori.items() if stato != "ERRORE" or conteggio > 0
            } for campo, valori in conteggi.items()
        },
        "dettaglio": risultati_finali
    }

    Path(OUTPUT_REPORT).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    
    print("\n--- RISULTATI ANALISI DIVERGENZA ---")
    for campo, metriche in report["metriche"].items():
        print(f"\nAttributo: {campo.upper()}")
        for stato, perc in metriche.items():
            print(f"  {stato:<18}: {perc}")
    print(f"\nReport dettagliato salvato in: {Path(OUTPUT_REPORT).resolve()}")

if __name__ == "__main__":
    main()