# Energy Drink Angebotsscraper

Ein Python-basierter Scraper zur Aggregation und Analyse von Energy-Drink-Angeboten aus verschiedenen Quellen.

---

## Features

* Multi-Source Scraping:

  * Marktguru API
  * KaufDA API
  * Pepper / mydealz (Community Deals)
  * Direktes Scraping von Shops (REWE, Lidl, Edeka, etc.)

* Daten-Normalisierung:

  * Preis-Parsing zu Float (`preis_eur`)
  * Volumen-Erkennung (`ml`, `L`)
  * Berechnung von Preis pro Liter (`€/L`)

* Analyse:

  * Minimaler Preis pro Marke
  * Maximaler Preis
  * Durchschnittspreis
  * Bestes Verhältnis (€/L)

* Deduplizierung:

  * Normalisierung von Produktnamen zur Zusammenführung identischer Produkte

---

## Installation

```bash
pip install curl_cffi beautifulsoup4 rich
```

---

## Nutzung

```bash
python monster_scraper.py
```

### Optionen

```bash
python monster_scraper.py --maerkte REWE Lidl Edeka
python monster_scraper.py --marken Monster Rockstar Gönrgy
python monster_scraper.py --json ergebnisse.json
```

---

## Unterstützte Marken

* Monster Energy
* Rockstar
* Gönrgy
* Red Bull
* Reign
* Burn
* Celsius
* Effect
* NOS

---

## Unterstützte Märkte

* Marktguru (API)
* KaufDA (API)
* Pepper / mydealz
* REWE
* Lidl
* Edeka
* Aldi Süd / Nord
* Penny
* Netto
* Kaufland

---

## Datenmodell

```python
@dataclass
class Angebot:
    markt: str
    produkt: str
    preis: Optional[str]

    preis_eur: Optional[float]
    einheit: Optional[str]
    preis_pro_liter: Optional[float]
```

---

## Daten-Normalisierung

### Preis

* "1,49 €" → `1.49`

### Volumen

* "500ml" → `0.5L`

### Preis pro Liter

```
preis_pro_liter = preis / liter
```

---

## Preisanalyse

Beispiel:

```
PREISÜBERSICHT

Monster:
  Angebote: 12
  Min: 0.88 €
  Max: 1.79 €
  Durchschnitt: 1.21 €
  Bestes €/L: 1.76

Red Bull:
  Angebote: 8
  Min: 0.99 €
  Durchschnitt: 1.39 €
```

---

## Deduplizierung

Beispiel:

```
Monster Energy 500ml
Monster 0,5 L Dose
```

→ wird als identisches Produkt erkannt

---

## Konfiguration

### Edeka Markt-ID

```python
EDEKA_MARKET_ID = "071378"
```

Ohne lokale Markt-ID können Preise ungenau sein.

---

## Performance

* API-first Ansatz
* curl_cffi zur Umgehung von Bot-Schutzmechanismen
* Fallback auf HTML-Scraping

---

## Troubleshooting

### IndentationError

```bash
python -tt monster_scraper.py
```

---

## Roadmap

* Preis-Alerts (z. B. Telegram, Discord)
* Persistenz (SQLite, CSV)
* Preisverlauf und Trends
* Web-Interface
* Parallelisierung

---

## Lizenz

MIT License

---

## Contribution

Pull Requests sind willkommen.

---

## Hinweis

Dieses Projekt dient zu Analyse- und Lernzwecken. Bitte beachte die Nutzungsbedingungen der jeweiligen Anbieter.
