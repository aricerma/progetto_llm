"""
Fase 3bis - Analisi descrittiva (Capitolo 6)
Tre grafici sui vincitori dell'Oscar al Miglior Attore, costruiti a
partire da doppie_vittorie.json (prodotto da scraping3.py).

Ogni funzione segue lo schema del Capitolo 6:
  idea -> domanda operativa -> tipo di grafico -> codice.

1. Eta_vincente_nel_tempo.png    : andamento nel tempo di una variabile
                                    -> grafico a linee
2. percentuale_doppia_vittoria.png: composizione di un totale
                                    -> grafico a torta
3. attori_piu_vincenti.png       : confronto fra gruppi
                                    -> grafico a barre
"""

import json

import pandas as pd
import matplotlib.pyplot as plt

INPUT_JSON = "doppie_vittorie.json"


def carica_dati() -> pd.DataFrame:
    """Carica doppie_vittorie.json in un DataFrame e converte le colonne
    numeriche: erano state salvate come stringa vuota "" quando il dato
    era mancante, pandas le riconosce come NaN una volta convertite."""
    with open(INPUT_JSON, encoding="utf-8") as f:
        righe = json.load(f)

    df = pd.DataFrame(righe)
    df["anno"] = pd.to_numeric(df["anno"], errors="coerce")
    df["eta_alla_vittoria"] = pd.to_numeric(df["eta_alla_vittoria"], errors="coerce")
    df["vittorie_miglior_attore_in_carriera"] = pd.to_numeric(
        df["vittorie_miglior_attore_in_carriera"], errors="coerce"
    )
    print(f"Caricate {len(df)} righe da {INPUT_JSON}.")
    return df


# ── Grafico 1: età vincente nel tempo ──────────────────────────────────────
# Idea: come è cambiata nel tempo l'età a cui si vince l'Oscar al Miglior
#       Attore?
# Domanda operativa: un punto per ogni vincitore (anno, età), collegati
#       in ordine cronologico.
# Tipo di grafico: andamento nel tempo -> grafico a linee.

def grafico_eta_nel_tempo(df: pd.DataFrame):
    dati = df.dropna(subset=["eta_alla_vittoria"]).sort_values("anno")
    n = len(dati)
    print(f"Grafico 1: età disponibile per {n} vincitori su {len(df)} righe totali.")

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(dati["anno"], dati["eta_alla_vittoria"], marker="o", markersize=3, linewidth=1)
    ax.set_title(f"Età dei vincitori dell'Oscar al Miglior Attore nel tempo (n={n})")
    ax.set_xlabel("Anno")
    ax.set_ylabel("Età alla vittoria")
    fig.tight_layout()
    fig.savefig("eta_vincente_nel_tempo.png", dpi=150)
    plt.close(fig)
    print("Grafico salvato in eta_vincente_nel_tempo.png")


# ── Grafico 2: percentuale di doppie vittorie ──────────────────────────────
# Idea: quante volte il film Miglior Film aveva nel cast anche il
#       vincitore del Miglior Attore dello stesso anno?
# Domanda operativa: per ciascun ANNO (non per ciascuna riga: il 1930 ha
#       due cerimonie ma un solo film Miglior Film, quindi si aggrega per
#       anno prima di contare, altrimenti si rischia un doppio conteggio),
#       il film ha almeno una corrispondenza nel cast?
# Tipo di grafico: composizione di un totale -> grafico a torta.

def grafico_percentuale_doppie_vittorie(df: pd.DataFrame):
    per_anno = df.groupby("anno")["presente_nel_cast"].any()
    conteggio = per_anno.value_counts()
    n_si = int(conteggio.get(True, 0))
    n_no = int(conteggio.get(False, 0))
    totale = n_si + n_no
    print(f"Grafico 2: {n_si}/{totale} film Miglior Film avevano nel cast "
          f"anche il vincitore Miglior Attore dello stesso anno.")

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.pie(
        [n_si, n_no],
        labels=[f"Sì ({n_si})", f"No ({n_no})"],
        autopct="%1.1f%%",
        startangle=90,
        colors=["#4C72B0", "#DD8452"],
    )
    ax.set_title("Film Miglior Film con anche il vincitore\nMiglior Attore nel cast, stesso anno")
    ax.axis("equal")  # garantisce che la torta sia un cerchio e non un'ellisse
    fig.tight_layout()
    fig.savefig("percentuale_doppia_vittoria.png", dpi=150)
    plt.close(fig)
    print("Grafico salvato in percentuale_doppia_vittoria.png")


# ── Grafico 3: gli attori più vincenti ─────────────────────────────────────
# Idea: quali attori hanno vinto più volte l'Oscar al Miglior Attore in
#       carriera (indipendentemente dal Miglior Film)?
# Domanda operativa: per ciascun attore distinto, il numero di vittorie
#       in carriera (già calcolato in scraping3.py); si mostrano i primi N.
# Tipo di grafico: confronto fra gruppi -> grafico a barre.

def grafico_attori_piu_vincenti(df: pd.DataFrame, top_n: int = 10):
    distinti = (
        df.dropna(subset=["vittorie_miglior_attore_in_carriera"])
        .drop_duplicates(subset=["vincitore_miglior_attore"])
        [["vincitore_miglior_attore", "vittorie_miglior_attore_in_carriera"]]
        .sort_values("vittorie_miglior_attore_in_carriera", ascending=False)
    )
    top = distinti.head(top_n)
    print(f"Grafico 3: primi {len(top)} attori per numero di vittorie in carriera.")

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.bar(top["vincitore_miglior_attore"], top["vittorie_miglior_attore_in_carriera"])
    ax.set_title(f"I {top_n} attori con più Oscar al Miglior Attore in carriera")
    ax.set_xlabel("Attore")
    ax.set_ylabel("Numero di vittorie")
    ax.tick_params(axis="x", rotation=45)
    for etichetta in ax.get_xticklabels():
        etichetta.set_ha("right")
    fig.tight_layout()
    fig.savefig("attori_piu_vincenti.png", dpi=150)
    plt.close(fig)
    print("Grafico salvato in attori_piu_vincenti.png")


def main():
    df = carica_dati()
    grafico_eta_nel_tempo(df)
    grafico_percentuale_doppie_vittorie(df)
    grafico_attori_piu_vincenti(df)


if __name__ == "__main__":
    main()
