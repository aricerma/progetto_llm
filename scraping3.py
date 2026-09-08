"""
Fase 3 - Doppie vittorie e anagrafica dei vincitori Miglior Attore
Per ogni anno, verifica se uno degli attori del cast del film Miglior
Film ha vinto anche l'Oscar al Miglior Attore in quella stessa edizione.
Arricchisce inoltre ogni vincitore con:
  - anno di nascita (cercato su Wikipedia)
  - eta alla vittoria (calcolata: anno vittoria - anno nascita)
  - numero di Oscar al Miglior Attore vinti in carriera (contato su
    TUTTE le edizioni scaricate, anche quelle in cui il film non ha
    vinto il Miglior Film)

Fonte 1 (già disponibile, non riscaricata): attori_film_vincitore.json
                                             -> cast dei film Miglior Film
Fonte 2 (scaricata da Wikipedia)          : Oscar al miglior attore
Fonte 3 (scaricata da Wikipedia, per persona): pagina biografica di
                                             ogni vincitore, per l'anno
                                             di nascita

Output: doppie_vittorie.csv  e  doppie_vittorie.json (nuovi file, non
        toccano gli altri già presenti nel progetto)
"""

import csv
import json
import re
import time
import unicodedata
import pathlib
from difflib import SequenceMatcher

import requests
from bs4 import BeautifulSoup

URL_MIGLIOR_ATTORE = "https://it.wikipedia.org/wiki/Oscar_al_miglior_attore"
API_URL = "https://it.wikipedia.org/w/api.php"
INPUT_JSON = "attori_film_vincitore.json"
OUTPUT_CSV = "doppie_vittorie.csv"
OUTPUT_JSON = "doppie_vittorie.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "it-IT,it;q=0.9",
}
SOGLIA_SIMILARITA = 0.90       # per il confronto "fuzzy" dei nomi nel cast
PAUSA_TRA_CHIAMATE_API = 1.5   # secondi, tra una chiamata API e l'altra
TENTATIVI_MASSIMI = 5          # ritentativi in caso di errore 429


# ── Utility di pulizia testo (stesse convenzioni di scraping1.py) ─────────

def clean(text: str) -> str:
    """Rimuove note [n], spazi extra, nbsp."""
    text = re.sub(r'\[\w+\]', '', text)
    return text.replace('\xa0', ' ').strip()


def parse_anno_label(a_tag) -> tuple[str, str]:
    """Estrae (anno, edizione) dall'<a> del li-anno, es. '1930 (aprile)'."""
    text = clean(a_tag.get_text())
    anno_m = re.search(r'\b(\d{4})\b', text)
    anno = anno_m.group(1) if anno_m else ''
    ediz_m = re.search(r'\(([^)]+)\)', text)
    edizione = ediz_m.group(1).strip() if ediz_m else ''
    return anno, edizione


def normalizza_nome(nome: str) -> str:
    """Toglie accenti, maiuscole e punteggiatura per confrontare i nomi
    senza differenze spurie tra pagine Wikipedia diverse."""
    nome = unicodedata.normalize('NFKD', nome).encode('ascii', 'ignore').decode()
    nome = re.sub(r"[^a-zA-Z\s]", " ", nome)
    nome = re.sub(r"\s+", " ", nome).strip().lower()
    return nome


# ── Chiamate API centralizzate (con pausa e gestione 429) ─────────────────

def chiamata_api(parametri: dict) -> dict:
    """Esegue una richiesta all'API di Wikipedia, con pausa dopo ogni
    chiamata e ritentativi con attesa in caso di 429 (troppe richieste)."""
    for tentativo in range(1, TENTATIVI_MASSIMI + 1):
        r = requests.get(API_URL, params=parametri, headers=HEADERS, timeout=15)

        if r.status_code == 429:
            attesa = int(r.headers.get("Retry-After", 10))
            print(f"    [429] Troppe richieste, aspetto {attesa}s "
                  f"(tentativo {tentativo}/{TENTATIVI_MASSIMI})")
            time.sleep(attesa)
            continue

        r.raise_for_status()
        time.sleep(PAUSA_TRA_CHIAMATE_API)
        return r.json()

    raise RuntimeError(
        "Troppi errori 429 consecutivi: l'API continua a rifiutare le richieste."
    )


# ── Fase A: scarica i vincitori dell'Oscar al miglior attore ──────────────

def estrai_nome_vincitore(li) -> str:
    """Dal <li> del vincitore estrae solo il nome dell'attore.
    Formato tipico: 'Nome Attore - Titolo del film (Original Title)',
    a volte con annotazioni come '(ex aequo)', '(rifiutato)', '(postumo)'."""
    testo = clean(li.get_text(" ", strip=True))
    parti = re.split(r"\s+[-–]\s+", testo, maxsplit=1)
    nome = parti[0] if parti else testo
    nome = re.sub(r"\([^)]*\)\s*$", "", nome).strip()
    return nome


def scarica_vincitori_miglior_attore() -> dict:
    """Restituisce {anno: [nomi vincitori]}. Di solito un nome per anno;
    nel 1930 (due cerimonie, aprile e novembre) l'anno mappa su DUE nomi."""
    r = requests.get(URL_MIGLIOR_ATTORE, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    container = soup.find("div", class_="mw-parser-output")
    if not container:
        raise RuntimeError("div.mw-parser-output non trovato")

    vincitori_per_anno: dict[str, list[str]] = {}

    for section in container.find_all("section"):
        for ul in section.find_all("ul"):
            for anno_li in ul.find_all("li", recursive=False):
                a_tag = anno_li.find("a")
                if not a_tag:
                    continue
                anno, _edizione = parse_anno_label(a_tag)
                if not re.match(r"^\d{4}$", anno):
                    continue

                sub_ul = anno_li.find("ul")
                if not sub_ul:
                    continue

                film_lis = sub_ul.find_all("li", recursive=False)
                vincitori_li = [li for li in film_lis if li.find("b")]
                candidati = vincitori_li if vincitori_li else film_lis[:1]

                for li in candidati:
                    nome = estrai_nome_vincitore(li)
                    if nome:
                        vincitori_per_anno.setdefault(anno, []).append(nome)

    return vincitori_per_anno


def conta_vittorie_in_carriera(vincitori_per_anno: dict) -> dict:
    """Conta quante volte ogni attore compare come vincitore, su TUTTE le
    edizioni scaricate (indipendentemente dal fatto che quell'anno il
    Miglior Film coincida o no). Chiave = nome normalizzato.
    Restituisce {nome_normalizzato: {'nome_display': ..., 'vittorie': N}}."""
    conteggio: dict[str, dict] = {}
    for nomi_anno in vincitori_per_anno.values():
        for nome in nomi_anno:
            chiave = normalizza_nome(nome)
            if chiave not in conteggio:
                conteggio[chiave] = {"nome_display": nome, "vittorie": 0}
            conteggio[chiave]["vittorie"] += 1
    return conteggio


# ── Fase B: anno di nascita di ogni vincitore (via Wikipedia) ─────────────

def cerca_titolo_pagina_persona(nome: str) -> str | None:
    """Cerca la pagina Wikipedia della persona con opensearch."""
    parametri = {
        "action": "opensearch",
        "search": nome,
        "limit": 3,
        "namespace": 0,
        "format": "json",
    }
    dati = chiamata_api(parametri)
    titoli = dati[1] if len(dati) > 1 else []
    return titoli[0] if titoli else None


def estrai_anno_nascita(estratto: str) -> int | None:
    """Le voci biografiche italiane iniziano tipicamente con
    'Nome Cognome (Città, giorno mese anno – ...)'. Cerchiamo il primo
    anno a 4 cifre plausibile dentro la prima coppia di parentesi."""
    match_parentesi = re.search(r"\(([^()]*)\)", estratto)
    blocco = match_parentesi.group(1) if match_parentesi else estratto[:200]
    anno_m = re.search(r"\b(1[7-9]\d{2}|20[0-2]\d)\b", blocco)
    return int(anno_m.group(1)) if anno_m else None


def ottieni_anno_nascita(titolo_pagina: str) -> int | None:
    """Scarica l'incipit (extract) della voce e ne ricava l'anno di nascita."""
    parametri = {
        "action": "query",
        "prop": "extracts",
        "exintro": 1,
        "explaintext": 1,
        "format": "json",
        "titles": titolo_pagina,
    }
    dati = chiamata_api(parametri)
    pagine = dati.get("query", {}).get("pages", {})
    for pagina in pagine.values():
        estratto = pagina.get("extract", "")
        if estratto:
            return estrai_anno_nascita(estratto)
    return None


def costruisci_anagrafica(nomi_attori: set[str]) -> dict:
    """Per ogni nome distinto, cerca la pagina e l'anno di nascita.
    Restituisce {nome_originale: anno_nascita_o_None}."""
    anagrafica = {}
    totale = len(nomi_attori)
    for i, nome in enumerate(sorted(nomi_attori), start=1):
        print(f"  [{i}/{totale}] Cerco anno di nascita: {nome}")
        titolo_pagina = cerca_titolo_pagina_persona(nome)
        anno_nascita = ottieni_anno_nascita(titolo_pagina) if titolo_pagina else None
        if anno_nascita is None:
            print(f"    [!] Anno di nascita non trovato per {nome}")
        anagrafica[nome] = anno_nascita
    return anagrafica


# ── Fase C: cast del film Miglior Film (già disponibile) ──────────────────

def carica_cast_per_anno(percorso_json: str) -> dict:
    """Raggruppa il JSON del cast per anno:
    {anno: {'titolo_it': ..., 'attori': [...]}}."""
    with open(percorso_json, "r", encoding="utf-8") as f:
        righe = json.load(f)

    per_anno: dict[str, dict] = {}
    for riga in righe:
        anno = riga["anno"]
        if anno not in per_anno:
            per_anno[anno] = {"titolo_it": riga["titolo_it"], "attori": []}
        if riga.get("attore"):
            per_anno[anno]["attori"].append(riga["attore"])

    return per_anno


def trova_corrispondenza(nome_vincitore: str, cast: list[str]):
    """Cerca 'nome_vincitore' nel cast: prima un confronto esatto
    (normalizzato), poi uno 'fuzzy' basato sulla similarità di stringa."""
    nome_norm = normalizza_nome(nome_vincitore)

    for attore in cast:
        if normalizza_nome(attore) == nome_norm:
            return attore, 1.0

    migliore, punteggio_migliore = None, 0.0
    for attore in cast:
        punteggio = SequenceMatcher(None, nome_norm, normalizza_nome(attore)).ratio()
        if punteggio > punteggio_migliore:
            punteggio_migliore, migliore = punteggio, attore

    if migliore and punteggio_migliore >= SOGLIA_SIMILARITA:
        return migliore, punteggio_migliore

    return None


# ── Fase D: assemblaggio finale ────────────────────────────────────────────

def confronta_e_arricchisci(
    vincitori_per_anno: dict,
    cast_per_anno: dict,
    anagrafica: dict,
    conteggio_vittorie: dict,
) -> list[dict]:
    righe_output = []

    for anno, info_film in sorted(cast_per_anno.items(), key=lambda x: int(x[0])):
        titolo_it = info_film["titolo_it"]
        cast = info_film["attori"]
        nomi_vincitori = vincitori_per_anno.get(anno, [])

        if not nomi_vincitori:
            righe_output.append({
                "anno": anno, "titolo_it": titolo_it,
                "vincitore_miglior_attore": "", "attore_nel_cast": "",
                "presente_nel_cast": False, "punteggio_similarita": "",
                "anno_nascita": "", "eta_alla_vittoria": "",
                "vittorie_miglior_attore_in_carriera": "",
                "nota": "vincitore miglior attore non trovato su Wikipedia",
            })
            continue

        for nome_vincitore in nomi_vincitori:
            corrispondenza = trova_corrispondenza(nome_vincitore, cast) if cast else None

            anno_nascita = anagrafica.get(nome_vincitore)
            eta_alla_vittoria = (int(anno) - anno_nascita) if anno_nascita else ""

            chiave_conteggio = normalizza_nome(nome_vincitore)
            vittorie_carriera = conteggio_vittorie.get(chiave_conteggio, {}).get("vittorie", "")

            nota = "" if cast else "cast non disponibile per questo film"
            if anno_nascita is None:
                nota = (nota + "; " if nota else "") + "anno di nascita non trovato"

            righe_output.append({
                "anno": anno,
                "titolo_it": titolo_it,
                "vincitore_miglior_attore": nome_vincitore,
                "attore_nel_cast": corrispondenza[0] if corrispondenza else "",
                "presente_nel_cast": bool(corrispondenza),
                "punteggio_similarita": round(corrispondenza[1], 2) if corrispondenza else "",
                "anno_nascita": anno_nascita if anno_nascita else "",
                "eta_alla_vittoria": eta_alla_vittoria,
                "vittorie_miglior_attore_in_carriera": vittorie_carriera,
                "nota": nota,
            })

    return righe_output


def salva_csv(righe: list[dict], percorso: str):
    campi = [
        "anno", "titolo_it", "vincitore_miglior_attore", "attore_nel_cast",
        "presente_nel_cast", "punteggio_similarita", "anno_nascita",
        "eta_alla_vittoria", "vittorie_miglior_attore_in_carriera", "nota",
    ]
    with open(percorso, "w", newline="", encoding="utf-8") as f:
        scrittore = csv.DictWriter(f, fieldnames=campi)
        scrittore.writeheader()
        scrittore.writerows(righe)
    print(f"CSV salvato: {pathlib.Path(percorso).resolve()}")


def salva_json(righe: list[dict], percorso: str):
    with open(percorso, "w", encoding="utf-8") as f:
        json.dump(righe, f, ensure_ascii=False, indent=2)
    print(f"JSON salvato: {pathlib.Path(percorso).resolve()}")


def main():
    print(f"Scarico i vincitori da: {URL_MIGLIOR_ATTORE}")
    vincitori_per_anno = scarica_vincitori_miglior_attore()
    print(f"Trovati vincitori per {len(vincitori_per_anno)} anni\n")

    conteggio_vittorie = conta_vittorie_in_carriera(vincitori_per_anno)

    print(f"Carico il cast da: {INPUT_JSON}")
    cast_per_anno = carica_cast_per_anno(INPUT_JSON)
    print(f"Trovati {len(cast_per_anno)} film vincitori con cast\n")

    nomi_distinti = {nome for nomi in vincitori_per_anno.values() for nome in nomi}
    print(f"Cerco l'anno di nascita per {len(nomi_distinti)} attori distinti...")
    anagrafica = costruisci_anagrafica(nomi_distinti)

    righe = confronta_e_arricchisci(
        vincitori_per_anno, cast_per_anno, anagrafica, conteggio_vittorie
    )

    salva_csv(righe, OUTPUT_CSV)
    salva_json(righe, OUTPUT_JSON)

    doppie = [r for r in righe if r["presente_nel_cast"]]
    print(f"\nDoppie vittorie trovate (film + attore protagonista nello stesso anno): {len(doppie)}")
    for r in doppie:
        print(f"  {r['anno']}: {r['vincitore_miglior_attore']} "
              f"(età {r['eta_alla_vittoria']}, {r['vittorie_miglior_attore_in_carriera']} Oscar in carriera) "
              f"— {r['titolo_it']}")


if __name__ == "__main__":
    main()
