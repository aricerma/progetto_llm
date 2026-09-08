"""
Fase 2bis - Genere e casa di produzione per ogni film
A partire da oscar_miglior_film.csv (TUTTE le candidature, non solo i
vincitori), per ciascuna riga:

  1. individua la voce Wikipedia italiana del film applicando le regole
     standard di disambiguazione dei titoli di film su Wikipedia, in
     ordine di probabilità:
       a) Titolo                      -> nessuna ambiguità
       b) Titolo (film)                -> disambiguazione generica
       c) Titolo (film ANNO_CERIMONIA) -> raro, ma capita
       d) Titolo (film ANNO_CERIMONIA - 1) -> il caso più comune: un
          film esce l'anno PRIMA della cerimonia Oscar che lo premia,
          e Wikipedia disambigua con l'anno di USCITA, non di premiazione
     Ogni candidato viene verificato con l'API (non indovinato "alla
     cieca": se non esiste, semplicemente non c'è nessuna pagina con
     quel titolo, quindi si passa al successivo).
  2. se nessuno dei tentativi diretti trova la pagina, usa la ricerca
     libera dell'API come rete di sicurezza (stesso meccanismo di
     scraping2.py);
  3. scarica il wikitext della voce trovata ed estrae dal template
     infobox {{Film ...}} i parametri "genere" e "casa di produzione"
     (provando alcuni alias, perché non tutte le voci sono compilate
     allo stesso modo).

Input : oscar_miglior_film.csv     (NON viene mai modificato)
Output: oscar_produzione_genere.json
        (tutti i campi originali del CSV + casa_produzione + genere,
        entrambi null quando non vengono riconosciuti)
"""

import csv
import json
import re
import time
import pathlib

import requests

INPUT_CSV = "oscar_miglior_film.csv"
OUTPUT_JSON = "oscar_produzione_genere.json"
API_URL = "https://it.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "progetto-dh-oscar/1.0 (uso didattico)"}

PAUSA_TRA_CHIAMATE_API = 1.5   # secondi, tra una chiamata API e l'altra
TENTATIVI_MASSIMI = 5          # ritentativi in caso di errore 429


# ── Chiamate API centralizzate (con pausa e gestione 429) ─────────────────

def chiamata_api(parametri: dict) -> dict:
    """Esegue una richiesta all'API di Wikipedia, con pausa dopo ogni
    chiamata e ritentativi con attesa in caso di 429 (troppe richieste).
    Stessa logica già usata in scraping2.py e scraping3.py."""
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

    raise RuntimeError("Troppi errori 429 consecutivi: riprova più tardi.")


# ── Passo 1: trovare la pagina giusta ──────────────────────────────────────

def pagina_esiste(titolo: str) -> str | None:
    """Controlla se una pagina con questo titolo esiste (seguendo gli
    eventuali redirect, es. maiuscole/minuscole diverse). Restituisce il
    titolo REALE della pagina se esiste, altrimenti None."""
    parametri = {
        "action": "query",
        "titles": titolo,
        "redirects": 1,
        "format": "json",
    }
    dati = chiamata_api(parametri)
    pagine = dati.get("query", {}).get("pages", {})
    for pagina in pagine.values():
        if "missing" in pagina:
            return None
        return pagina.get("title", titolo)
    return None


def cerca_con_opensearch(query: str) -> str | None:
    """Rete di sicurezza: se nessuno slug candidato esiste, prova una
    ricerca libera (stesso meccanismo di scraping2.py)."""
    parametri = {
        "action": "opensearch",
        "search": query,
        "limit": 3,
        "namespace": 0,
        "format": "json",
    }
    dati = chiamata_api(parametri)
    titoli = dati[1] if len(dati) > 1 else []
    return titoli[0] if titoli else None


def trova_titolo_pagina(titolo_it: str, anno: str) -> tuple[str | None, str]:
    """Prova le regole di disambiguazione standard, in ordine di
    probabilità, verificando ciascun candidato con l'API. Se nessuno
    esiste, ricorre alla ricerca libera. Restituisce
    (titolo_trovato_o_None, descrizione_del_metodo_usato)."""
    anno_int = int(anno)
    candidati = [
        titolo_it,
        f"{titolo_it} (film)",
        f"{titolo_it} (film {anno_int})",
        f"{titolo_it} (film {anno_int - 1})",
    ]
    candidati = list(dict.fromkeys(candidati))  # rimuove duplicati, mantiene l'ordine

    for candidato in candidati:
        trovato = pagina_esiste(candidato)
        if trovato:
            return trovato, f"slug diretto ({candidato})"

    risultato_ricerca = cerca_con_opensearch(f"{titolo_it} film {anno_int}")
    if risultato_ricerca:
        return risultato_ricerca, "ricerca libera"

    return None, "non trovato"


# ── Passo 2: estrazione di genere e casa di produzione dal wikitext ───────

def ottieni_wikitext(titolo_pagina: str) -> str | None:
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
    """Rimuove markup wikitext residuo: commenti, ref, wikilink, grassetto/corsivo, template inline."""
    testo = re.sub(r"<!--.*?-->", "", testo, flags=re.DOTALL)
    testo = re.sub(r"<ref[^>]*>.*?</ref>", "", testo, flags=re.DOTALL)
    testo = re.sub(r"<ref[^>]*/>", "", testo)
    testo = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", testo)  # [[link|testo]] -> testo
    testo = re.sub(r"\[\[([^\]]+)\]\]", r"\1", testo)              # [[link]] -> link
    testo = re.sub(r"'{2,}", "", testo)                             # '' o ''' -> niente
    testo = re.sub(r"\{\{[^}]*\}\}", "", testo)                     # piccoli template inline
    return testo.strip()


def estrai_parametro(wikitext: str, nomi_parametro: list[str]) -> str | None:
    """Cerca il primo tra vari possibili alias di un parametro del
    template infobox e ne restituisce il valore ripulito. I nomi dei
    parametri non sono sempre identici in ogni voce (template compilati
    con varianti vecchie o non standard), quindi si prova più alias in
    ordine di probabilità."""
    for nome in nomi_parametro:
        pattern_intermedio = rf"\|\s*{re.escape(nome)}\s*=\s*(.*?)\n\s*\|"
        match = re.search(pattern_intermedio, wikitext, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            pattern_finale = rf"\|\s*{re.escape(nome)}\s*=\s*(.*?)\n\}}\}}"
            match = re.search(pattern_finale, wikitext, flags=re.IGNORECASE | re.DOTALL)
        if match:
            valore = pulisci_wikitext(match.group(1))
            valore = re.sub(r"\s+", " ", valore).strip(" ,;")
            if valore:
                return valore
    return None


def estrai_genere_e_produzione(wikitext: str) -> tuple[str | None, str | None]:
    genere = estrai_parametro(wikitext, ["genere"])
    casa_produzione = estrai_parametro(
        wikitext, ["casaproduzione", "casa di produzione", "produzione"]
    )
    return genere, casa_produzione


# ── Passo 3: pipeline per singolo film ─────────────────────────────────────

def arricchisci_riga(riga: dict) -> dict:
    titolo_it = riga["titolo_it"]
    anno = riga["anno"]

    titolo_pagina, metodo = trova_titolo_pagina(titolo_it, anno)

    nuova_riga = dict(riga)  # mantiene tutti i campi originali del CSV
    nuova_riga["genere"] = None
    nuova_riga["casa_produzione"] = None

    if not titolo_pagina:
        print(f"  [!] Pagina non trovata: {titolo_it} ({anno})")
        return nuova_riga

    wikitext = ottieni_wikitext(titolo_pagina)
    if not wikitext:
        print(f"  [!] Wikitext non disponibile: {titolo_pagina}")
        return nuova_riga

    genere, casa_produzione = estrai_genere_e_produzione(wikitext)
    nuova_riga["genere"] = genere
    nuova_riga["casa_produzione"] = casa_produzione

    print(f"  [OK] {titolo_it} -> {titolo_pagina} ({metodo}) "
          f"| genere={genere!r} | casa_produzione={casa_produzione!r}")

    return nuova_riga


def main():
    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        righe = list(csv.DictReader(f))
    print(f"Caricate {len(righe)} righe da {INPUT_CSV}\n")

    righe_arricchite = []
    for i, riga in enumerate(righe, start=1):
        print(f"[{i}/{len(righe)}] {riga['titolo_it']} ({riga['anno']})")
        righe_arricchite.append(arricchisci_riga(riga))

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(righe_arricchite, f, ensure_ascii=False, indent=2)
    print(f"\nJSON salvato: {pathlib.Path(OUTPUT_JSON).resolve()}")

    senza_genere = sum(1 for r in righe_arricchite if r["genere"] is None)
    senza_produzione = sum(1 for r in righe_arricchite if r["casa_produzione"] is None)
    print(f"Righe totali: {len(righe_arricchite)} "
          f"| senza genere: {senza_genere} | senza casa di produzione: {senza_produzione}")


if __name__ == "__main__":
    main()