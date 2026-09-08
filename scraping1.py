"""
Scraping Oscar al miglior film – Wikipedia IT
Fonte: https://it.wikipedia.org/wiki/Oscar_al_miglior_film

Struttura reale della pagina:
  section > ul > li[anno] > ul > li[film]
  - Il testo dell'<a> nel li-anno contiene già anno ed edizione (es. "1930 (aprile)")
  - Il vincitore è il primo li del sub-ul contenente un tag <b>
  - I restanti li sono candidati

Output: stampa formattata + lista di dict (anno, edizione, vincitore, titolo_it, titolo_orig, regista)
"""

import re
import sys
import requests
from bs4 import BeautifulSoup
import csv
import pathlib

URL = "https://it.wikipedia.org/wiki/Oscar_al_miglior_film"


def clean(text: str) -> str:
    """Rimuove note [n], spazi extra, nbsp."""
    text = re.sub(r'\[\w+\]', '', text)
    return text.replace('\xa0', ' ').strip()


def parse_film(li) -> tuple[str, str, str]:
    """
    Estrae (titolo_it, titolo_orig, regista) da un <li> film.
    - titolo_it  : testo prima della prima '('
    - titolo_orig: testo dentro la prima coppia '(…)'
    - regista    : testo dopo 'regia di '
    """
    raw = clean(li.get_text())

    titolo_it_m = re.match(r'^([^(,]+?)(?:\s*[,(]|$)', raw)
    titolo_it = titolo_it_m.group(1).strip() if titolo_it_m else raw

    titolo_orig_m = re.search(r'\(([^)]+)\)', raw)
    titolo_orig = titolo_orig_m.group(1).strip() if titolo_orig_m else ''

    regista_m = re.search(r'regia di\s+(.+)', raw, re.IGNORECASE)
    regista = regista_m.group(1).strip() if regista_m else ''
    regista = re.split(r'[;,]', regista)[0].strip()

    return titolo_it, titolo_orig, regista


def parse_anno_label(a_tag) -> tuple[str, str]:
    """
    Estrae (anno, edizione) dall'<a> del li-anno.
    Esempi:
      '1929'           → ('1929', '')
      '1930 (aprile)'  → ('1930', 'aprile')
    """
    text = clean(a_tag.get_text())
    anno_m = re.search(r'\b(\d{4})\b', text)
    anno = anno_m.group(1) if anno_m else ''
    ediz_m = re.search(r'\(([^)]+)\)', text)
    edizione = ediz_m.group(1).strip() if ediz_m else ''
    return anno, edizione


def scrape_file(path: str) -> list[dict]:
    """Parsa il file HTML locale (utile per test senza connessione)."""
    with open(path, 'r', encoding='utf-8') as f:
        html = f.read()
    return _parse(html)


def scrape_url() -> list[dict]:
    """Scarica la pagina da Wikipedia e la parsa."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'it-IT,it;q=0.9',
    }
    r = requests.get(URL, headers=headers, timeout=15)
    r.raise_for_status()
    return _parse(r.text)


def _parse(html: str) -> list[dict]:
    soup = BeautifulSoup(html, 'html.parser')
    container = soup.find('div', class_='mw-parser-output')
    if not container:
        raise RuntimeError("div.mw-parser-output non trovato")

    results = []
    seen = set()

    for section in container.find_all('section'):
        for ul in section.find_all('ul'):
            for anno_li in ul.find_all('li', recursive=False):
                a_tag = anno_li.find('a')
                if not a_tag:
                    continue
                anno, edizione = parse_anno_label(a_tag)
                if not re.match(r'^\d{4}$', anno):
                    continue

                sub_ul = anno_li.find('ul')
                if not sub_ul:
                    continue

                film_lis = sub_ul.find_all('li', recursive=False)
                for fli in film_lis:
                    vincitore = bool(fli.find('b'))
                    t_it, t_orig, reg = parse_film(fli)

                    key = (anno, edizione, t_it)
                    if key in seen:
                        continue
                    seen.add(key)

                    results.append({
                        'anno': anno,
                        'edizione': edizione,
                        'vincitore': vincitore,
                        'titolo_it': t_it,
                        'titolo_orig': t_orig,
                        'regista': reg,
                    })

    return results


def print_results(data: list[dict]):
    prev = None
    for r in data:
        chiave = (r['anno'], r['edizione'])
        if chiave != prev:
            ed = f" ({r['edizione']})" if r['edizione'] else ''
            print(f"\n── {r['anno']}{ed} ──")
            prev = chiave

        flag = '🏆' if r['vincitore'] else '  '
        print(f"  {flag} {r['titolo_it']}")
        if r['titolo_orig'] and r['titolo_orig'] != r['titolo_it']:
            print(f"       [{r['titolo_orig']}]")
        if r['regista']:
            print(f"       Regia: {r['regista']}")

    print(f"\nTotale record  : {len(data)}")
    print(f"Edizioni/anni  : {sum(1 for r in data if r['vincitore'])}")

# ── OUTPUT CSV ────────────────────────────────────────────────────────────────



def save_csv(data: list[dict], path: str = "oscar_miglior_film.csv"):
    """Salva i dati in un file CSV."""
    if not data:
        return
    fieldnames = ["anno", "edizione", "vincitore", "titolo_it", "titolo_orig", "regista"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)
    print(f"CSV salvato: {pathlib.Path(path).resolve()}")

if __name__ == '__main__':
    # Se viene passato un file html come argomento, usa quello
    if len(sys.argv) > 1:
        print(f"Parsing da file: {sys.argv[1]}")
        data = scrape_file(sys.argv[1])
    else:
        print(f"Scraping: {URL}")
        data = scrape_url()

    if not data:
        print("Nessun dato estratto.")
    else:
        print_results(data)
        save_csv(data)


