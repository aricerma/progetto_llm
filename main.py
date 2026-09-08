# scraper_oscar.py
# Scarica la pagina Wikipedia "Oscar al miglior film" ed estrae
# la lista dei film VINCITORI in un file CSV.

import csv
import re

import requests
from bs4 import BeautifulSoup


URL = "https://it.wikipedia.org/wiki/Oscar_al_miglior_film"
OUTPUT = "oscar_best_pictures.csv"


def pulisci_testo(testo):
    """Rimuove note tra parentesi quadre (es. [1]) e spazi superflui."""
    testo = re.sub(r"\[\d+\]", "", testo)
    testo = re.sub(r"\s+", " ", testo)
    return testo.strip()


def separa_titoli(testo_cella):
    """Da 'Titolo italiano (Original Title)' separa i due titoli.
    Se non c'è una parte fra parentesi, il titolo originale resta vuoto
    (succede per i film già in inglese, senza traduzione italiana)."""
    match = re.search(r"^(.*?)\s*\((.*?)\)\s*$", testo_cella)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return testo_cella.strip(), ""


def estrai_vincitori(html):
    """Analizza la tabella principale e restituisce una lista di
    dizionari, uno per ciascun film VINCITORE (i candidati non vincitori
    vengono scartati)."""
    soup = BeautifulSoup(html, "html.parser")
    tabella = soup.find("table", class_="wikitable")

    vincitori = []
    anno_corrente = None

    for riga in tabella.find_all("tr")[1:]:  # salta l'intestazione
        celle = riga.find_all(["td", "th"])
        if not celle:
            continue

        # Se la prima cella ha un rowspan, contiene un nuovo anno:
        # aggiorniamo anno_corrente e togliamo quella cella dal resto.
        prima_cella = celle[0]
        if prima_cella.get("rowspan") and re.search(r"\d{4}", prima_cella.get_text()):
            anno_corrente = pulisci_testo(prima_cella.get_text())
            celle = celle[1:]

        if len(celle) < 2:
            continue

        cella_film, cella_regia = celle[0], celle[1]

        # Il vincitore è marcato in grassetto (<b>) dentro la cella del film
        e_vincitore = cella_film.find("b") is not None
        if not e_vincitore:
            continue  # scartiamo i semplici candidati

        titolo_italiano, titolo_originale = separa_titoli(
            cella_film.get_text(" ", strip=True)
        )
        registi = [a.get_text(strip=True) for a in cella_regia.find_all("a")]
        regista = " e ".join(registi) if registi else cella_regia.get_text(" ", strip=True)

        vincitori.append({
            "anno": anno_corrente,
            "titolo_italiano": pulisci_testo(titolo_italiano),
            "titolo_originale": pulisci_testo(titolo_originale),
            "regista": pulisci_testo(regista),
        })

    return vincitori


def salva_csv(vincitori, percorso):
    with open(percorso, "w", encoding="utf-8", newline="") as f:
        scrittore = csv.DictWriter(
            f, fieldnames=["anno", "titolo_italiano", "titolo_originale", "regista"]
        )
        scrittore.writeheader()
        scrittore.writerows(vincitori)


def main():
    risposta = requests.get(URL, headers={"User-Agent": "progetto-dh-oscar/1.0"})
    vincitori = estrai_vincitori(risposta.text)
    salva_csv(vincitori, OUTPUT)
    print(f"Salvati {len(vincitori)} film vincitori in {OUTPUT}")


if __name__ == "__main__":
    main()