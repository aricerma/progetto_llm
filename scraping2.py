"""
Fase 2 - Arricchimento cast dei film vincitori
Legge oscar_miglior_film.csv (prodotto da scraping1.py), per ogni film
VINCITORE cerca la pagina Wikipedia corrispondente tramite l'API di
Wikipedia, ne estrae il wikitext ed estrae il parametro "Attori" del
template infobox {{Film ...}}.

Input : oscar_miglior_film.csv   (NON viene mai modificato)
Output: attori_film_vincitore.csv (nuovo file, una riga per attore/film)

Perché l'API e non l'HTML?
Il markup HTML delle infobox può variare (classi diverse, template
diversi da voce a voce). Il wikitext del template {{Film ...}} è invece
una struttura dati esplicita e stabile: |Attori = * [[Nome]]: Ruolo ...
"""

import csv
import re
import time
import pathlib

import requests

INPUT_CSV = "oscar_miglior_film.csv"
OUTPUT_CSV = "attori_film_vincitore.csv"
API_URL = "https://it.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "progetto-dh-oscar/1.0 (uso didattico)"}
PAUSA_TRA_RICHIESTE = 2.0      # secondi, tra un film e l'altro
PAUSA_TRA_CHIAMATE_API = 1.5   # secondi, tra una chiamata API e l'altra (dentro lo stesso film)
TENTATIVI_MASSIMI = 5           # numero di tentativi in caso di errore 429


def chiamata_api(parametri: dict) -> dict:
    """Esegue una richiesta all'API di Wikipedia, con pausa dopo ogni
    chiamata e ritentativi con attesa se il server risponde 429 (troppe
    richieste). Centralizzare qui la logica evita di doverla ripetere
    in ogni funzione che parla con l'API."""
    for tentativo in range(1, TENTATIVI_MASSIMI + 1):
        r = requests.get(API_URL, params=parametri, headers=HEADERS, timeout=15)

        if r.status_code == 429:
            attesa = int(r.headers.get("Retry-After", 10))
            print(f"    [429] Troppe richieste, aspetto {attesa}s "
                  f"(tentativo {tentativo}/{TENTATIVI_MASSIMI})")
            time.sleep(attesa)
            continue

        r.raise_for_status()
        time.sleep(PAUSA_TRA_CHIAMATE_API)  # pausa anche dopo una richiesta riuscita
        return r.json()

    raise RuntimeError(
        "Troppi errori 429 consecutivi: l'API continua a rifiutare le richieste. "
        "Riprova più tardi o aumenta PAUSA_TRA_CHIAMATE_API."
    )


def leggi_vincitori(percorso: str) -> list[dict]:
    """Legge il CSV esistente e restituisce solo le righe dei film vincitori."""
    with open(percorso, "r", encoding="utf-8") as f:
        lettore = csv.DictReader(f)
        righe = list(lettore)
    return [r for r in righe if r["vincitore"].strip().lower() == "true"]


def cerca_titolo_pagina(query: str) -> str | None:
    """Usa l'opensearch dell'API per trovare il titolo esatto della pagina
    Wikipedia più vicino alla query. Restituisce None se non trova nulla."""
    parametri = {
        "action": "opensearch",
        "search": query,
        "limit": 5,
        "namespace": 0,
        "format": "json",
    }
    dati = chiamata_api(parametri)
    titoli_trovati = dati[1] if len(dati) > 1 else []
    return titoli_trovati[0] if titoli_trovati else None


def ottieni_wikitext(titolo_pagina: str) -> str | None:
    """Scarica il wikitext grezzo (ultima revisione) di una pagina."""
    parametri = {
        "action": "query",
        "prop": "revisions",
        "rvslots": "main",
        "rvprop": "content",
        "format": "json",
        "titles": titolo_pagina,
    }
    dati = chiamata_api(parametri)
    pagine = dati.get("query", {}).get("pages", {})
    for pagina in pagine.values():
        revisioni = pagina.get("revisions")
        if revisioni:
            return revisioni[0]["slots"]["main"]["*"]
    return None


def pulisci_wikitext(testo: str) -> str:
    """Rimuove markup wikitext residuo: wikilink, grassetto/corsivo, ref, template inline."""
    testo = re.sub(r"<ref[^>]*>.*?</ref>", "", testo, flags=re.DOTALL)
    testo = re.sub(r"<ref[^>]*/>", "", testo)
    testo = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", testo)  # [[link|testo]] -> testo
    testo = re.sub(r"\[\[([^\]]+)\]\]", r"\1", testo)              # [[link]] -> link
    testo = re.sub(r"'{2,}", "", testo)                             # '' o ''' -> niente
    testo = re.sub(r"\{\{[^}]*\}\}", "", testo)                     # piccoli template inline
    return testo.strip()


def estrai_attori(wikitext: str) -> list[dict]:
    """Estrae la lista attori dal parametro Attori del template {{Film ...}}.
    Ogni voce diventa un dict {'attore': ..., 'personaggio': ...}."""
    match = re.search(
        r"\|\s*[Aa]ttori\s*=\s*(.*?)\n\s*\|", wikitext, flags=re.DOTALL
    )
    if not match:
        # a volte è l'ultimo parametro prima della chiusura del template
        match = re.search(
            r"\|\s*[Aa]ttori\s*=\s*(.*?)\n\}\}", wikitext, flags=re.DOTALL
        )
    if not match:
        return []

    blocco = match.group(1)
    voci = []
    for riga in blocco.splitlines():
        riga = riga.strip()
        if not riga.startswith("*"):
            continue
        riga = riga.lstrip("*").strip()
        riga = pulisci_wikitext(riga)
        if not riga:
            continue

        if ":" in riga:
            attore, personaggio = riga.split(":", 1)
        else:
            attore, personaggio = riga, ""

        voci.append({"attore": attore.strip(), "personaggio": personaggio.strip()})

    return voci


def elabora_film(riga_film: dict) -> list[dict]:
    """Per un film vincitore: trova la pagina, estrae gli attori,
    restituisce le righe pronte per il CSV di output."""
    titolo_it = riga_film["titolo_it"]
    titolo_orig = riga_film.get("titolo_orig", "")
    anno = riga_film["anno"]

    query_candidate = [
        f"{titolo_it} film {anno}",
        titolo_it,
        f"{titolo_orig} film {anno}" if titolo_orig else None,
    ]
    query_candidate = [q for q in query_candidate if q]

    titolo_pagina = None
    for query in query_candidate:
        titolo_pagina = cerca_titolo_pagina(query)
        if titolo_pagina:
            break

    if not titolo_pagina:
        print(f"  [!] Nessuna pagina trovata per: {titolo_it} ({anno})")
        return [{
            "anno": anno, "titolo_it": titolo_it, "titolo_orig": titolo_orig,
            "attore": "", "personaggio": "", "nota": "pagina non trovata",
        }]

    wikitext = ottieni_wikitext(titolo_pagina)
    if not wikitext:
        print(f"  [!] Impossibile leggere il wikitext di: {titolo_pagina}")
        return [{
            "anno": anno, "titolo_it": titolo_it, "titolo_orig": titolo_orig,
            "attore": "", "personaggio": "", "nota": "wikitext non disponibile",
        }]

    attori = estrai_attori(wikitext)
    if not attori:
        print(f"  [!] Nessun campo 'Attori' trovato in: {titolo_pagina}")
        return [{
            "anno": anno, "titolo_it": titolo_it, "titolo_orig": titolo_orig,
            "attore": "", "personaggio": "", "nota": "campo Attori assente",
        }]

    print(f"  [OK] {titolo_pagina}: {len(attori)} attori trovati")
    return [{
        "anno": anno, "titolo_it": titolo_it, "titolo_orig": titolo_orig,
        "attore": a["attore"], "personaggio": a["personaggio"], "nota": "",
    } for a in attori]


def salva_csv(righe: list[dict], percorso: str):
    campi = ["anno", "titolo_it", "titolo_orig", "attore", "personaggio", "nota"]
    with open(percorso, "w", newline="", encoding="utf-8") as f:
        scrittore = csv.DictWriter(f, fieldnames=campi)
        scrittore.writeheader()
        scrittore.writerows(righe)
    print(f"\nCSV salvato: {pathlib.Path(percorso).resolve()}")


def main():
    vincitori = leggi_vincitori(INPUT_CSV)
    print(f"Trovati {len(vincitori)} film vincitori in {INPUT_CSV}\n")

    tutte_le_righe = []
    for i, film in enumerate(vincitori, start=1):
        print(f"[{i}/{len(vincitori)}] {film['titolo_it']} ({film['anno']})")
        tutte_le_righe.extend(elabora_film(film))
        time.sleep(PAUSA_TRA_RICHIESTE)

    salva_csv(tutte_le_righe, OUTPUT_CSV)

    non_trovati = sum(1 for r in tutte_le_righe if r["nota"])
    print(f"Righe totali: {len(tutte_le_righe)} | Film senza cast estratto: {non_trovati}")


if __name__ == "__main__":
    main()