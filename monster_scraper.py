"""
Energy Drink Angebotsscraper – Kombinierter Ansatz
════════════════════════════════════════════════════════════════════════
Gesuchte Marken: Monster Energy, Rockstar, Gönrgy, Red Bull, Reign,
                 Burn, Celsius, Effect, NOS
Strategie 1: Marktguru & KaufDA APIs  → direkte Angebotsdaten, kein Bot-Schutz
Strategie 2: curl_cffi                → emuliert echten Chrome, umgeht 403/Cloudflare
Strategie 3: Pepper.com               → Community-Deals, sehr zuverlässig

Installation:
    pip install curl_cffi beautifulsoup4 rich

Ausführung:
    python monster_scraper.py
    python monster_scraper.py --maerkte REWE Lidl Edeka
    python monster_scraper.py --marken Monster Rockstar Gönrgy
    python monster_scraper.py --json ergebnisse.json
"""

from __future__ import annotations
import json, re, time, logging, argparse
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

# ── curl_cffi (Chrome-Fingerprint-Emulation) ─────────────────────────────────
try:
    from curl_cffi import requests as cffi_requests
    CFFI_OK = True
except ImportError:
    import requests as cffi_requests          # type: ignore[no-redef]
    CFFI_OK = False

# ── Standard requests als Fallback ───────────────────────────────────────────
import requests as _requests

# ── Rich für hübsche Ausgabe (optional) ──────────────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich import print as rprint
    RICH = True
    console = Console()
except ImportError:
    RICH = False
    console = None  # type: ignore[assignment]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#   Marken-Konfiguration & Markt-IDs
# ══════════════════════════════════════════════════════════════════════════════

# WICHTIG für Edeka: Ohne lokale Markt-ID gibt es oft keine korrekten Angebotspreise.
# So findest du sie: edeka.de aufrufen -> Markt wählen -> F12 (Netzwerk-Tab) -> nach "marketId" suchen.
EDEKA_MARKET_ID = "071378"  # <--- HIER DEINE LOKALE EDEKA-ID EINTRAGEN

MARKEN: dict[str, dict] = {
    "Monster":  {"pattern": r"monster(\s*energy)?",       "queries": ["monster energy"],        "emoji": "🟢"},
    "Rockstar": {"pattern": r"rockstar(\s*energy)?",      "queries": ["rockstar energy"],       "emoji": "⭐"},
    "Gönrgy":   {"pattern": r"g[oö]n+rgy",                "queries": ["gönrgy", "gonrgy"],      "emoji": "🔵"},
    "Red Bull": {"pattern": r"red\s*bull",                "queries": ["red bull"],              "emoji": "🔴"},
    "Reign":    {"pattern": r"reign(\s*total\s*body)?",   "queries": ["reign energy"],          "emoji": "👑"},
    "Burn":     {"pattern": r"\bburn\s*energy",           "queries": ["burn energy"],           "emoji": "🔥"},
    "Celsius":  {"pattern": r"\bcelsius\b",               "queries": ["celsius energy drink"],  "emoji": "🧊"},
    "Effect":   {"pattern": r"\beffect\b",                "queries": ["effect energy"],         "emoji": "⚡"},
    "NOS":      {"pattern": r"\bnos\s*energy",            "queries": ["nos energy drink"],      "emoji": "💨"},
    "Booster":  {"pattern": r"\bbooster",                 "queries": ["booster energy drink"],  "emoji": "💨"},
    "Energy":   {"pattern": r"\benergy",                  "queries": ["algemein energy drink"], "emoji": "💨"},
}

# Wird beim Start via _init_marken() befüllt
_AKTIVE_MARKEN: dict[str, dict] = {}
ENERGY_RE: re.Pattern = re.compile(r".", re.IGNORECASE)   # Platzhalter bis init
SEARCH_QUERIES: list[str] = []


def _init_marken(auswahl: list[str] | None = None) -> None:
    """Initialisiert aktive Marken + baut Regex und Query-Liste."""
    global _AKTIVE_MARKEN, ENERGY_RE, SEARCH_QUERIES

    if auswahl:
        unbekannt = [m for m in auswahl if m not in MARKEN]
        if unbekannt:
            log.warning(f"Unbekannte Marken ignoriert: {unbekannt}  |  Verfügbar: {list(MARKEN)}")
        _AKTIVE_MARKEN = {k: v for k, v in MARKEN.items() if k in auswahl}
    else:
        _AKTIVE_MARKEN = dict(MARKEN)

    if not _AKTIVE_MARKEN:
        _AKTIVE_MARKEN = dict(MARKEN)

    # Kombiniertes Regex
    patterns = [m["pattern"] for m in _AKTIVE_MARKEN.values()]
    ENERGY_RE = re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)

    # Suchbegriffe (dedupliziert)
    seen: set[str] = set()
    SEARCH_QUERIES = []
    for m in _AKTIVE_MARKEN.values():
        for q in m["queries"]:
            if q not in seen:
                seen.add(q)
                SEARCH_QUERIES.append(q)

    log.info(f"✅  Aktive Marken: {', '.join(_AKTIVE_MARKEN)} → {len(SEARCH_QUERIES)} Suchanfragen")


def ist_energy(text: str) -> bool:
    return bool(ENERGY_RE.search(text))

def marke_von(text: str) -> str:
    """Erkennt welche Marke ein Produktname ist."""
    for name, cfg in _AKTIVE_MARKEN.items():
        if re.search(cfg["pattern"], text, re.IGNORECASE):
            return name
    return "Sonstige"

def preis_clean(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip().replace("\xa0", " ")
    return re.sub(r"\s+", " ", raw)

PREIS_RE = re.compile(r"\d+[,\.]\d{2}")

def get_edeka_market_id(plz="56316"):
    url = "https://www.edeka.de/api/market"
    params = {"plz": plz}

    data = plain_get(url, params=params, json_mode=True)
    if isinstance(data, dict):
        markets = data.get("markets", [])
        if markets:
            return markets[0].get("id")

    return None



# ── Normalisierung ─────────────────────────────────────────────

PREIS_RE = re.compile(r"\d+[,.]?\d*")
VOLUME_RE = re.compile(r"(\d+[,.]?\d*)\s*(ml|l)", re.IGNORECASE)


def _parse_preis(preis: str | None) -> Optional[float]:
    if not preis:
        return None
    preis = preis.replace(",", ".")
    m = PREIS_RE.search(preis)
    try:
        return float(m.group()) if m else None
    except:
        return None


def _parse_volume(text: str) -> tuple[Optional[float], Optional[str]]:
    m = VOLUME_RE.search(text)
    if not m:
        return None, None

    val = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()

    if unit == "ml":
        val = val / 1000

    return val, f"{val}L"


def canonical_name(name: str) -> str:
    name = name.lower()
    name = re.sub(r"\d+[,.]?\d*\s*(ml|l)", "", name)
    name = re.sub(r"[^a-z0-9 ]", "", name)
    return re.sub(r"\s+", " ", name).strip()



@dataclass
class Angebot:
    markt: str
    produkt: str
    preis: Optional[str]

    marke: str = "Unbekannt"
    normalpreis: Optional[str] = None

    # 🔥 NEU
    preis_eur: Optional[float] = None
    einheit: Optional[str] = None
    preis_pro_liter: Optional[float] = None

    ersparnis: Optional[str] = None
    gueltig_von: Optional[str] = None
    gueltig_bis: Optional[str] = None
    url: str = ""
    bild_url: Optional[str] = None
    quelle: str = "scraper"
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.marke == "Unbekannt":
            self.marke = marke_von(self.produkt)

        self._normalize()

    def _normalize(self):
        # Preis numerisch
        self.preis_eur = _parse_preis(self.preis)

        # Volumen
        liter, einheit = _parse_volume(self.produkt)
        self.einheit = einheit

        # €/Liter
        if self.preis_eur and liter:
            self.preis_pro_liter = round(self.preis_eur / liter, 2)

    def zeile(self) -> str:
        emoji = _AKTIVE_MARKEN.get(self.marke, {}).get("emoji", "🥤")
        teile = [f"{emoji} [{self.markt}]", self.produkt]

        if self.preis:
            teile.append(f"→ {self.preis}")

        if self.preis_pro_liter:
            teile.append(f"({self.preis_pro_liter} €/L)")

        return "  ".join(teile)


def deduplizieren(angebote: list[Angebot]) -> list[Angebot]:
    gesehen: set[tuple] = set()
    unique = []

    for a in angebote:
        key = (
            a.markt.lower(),
            canonical_name(a.produkt),
            a.preis_eur
        )

        if key not in gesehen:
            gesehen.add(key)
            unique.append(a)

    return unique


def preis_uebersicht(angebote: list[Angebot]) -> dict:
    stats = {}

    for a in angebote:
        if not a.preis_eur:
            continue
        stats.setdefault(a.marke, []).append(a)

    result = {}

    for marke, items in stats.items():
        preise = [a.preis_eur for a in items if a.preis_eur]
        literpreise = [a.preis_pro_liter for a in items if a.preis_pro_liter]

        result[marke] = {
            "anzahl": len(items),
            "min": min(preise) if preise else None,
            "max": max(preise) if preise else None,
            "avg": round(sum(preise) / len(preise), 2) if preise else None,
            "best_pro_liter": min(literpreise) if literpreise else None,
        }

    return result


def ausgeben_preisstats(angebote: list[Angebot]):
    stats = preis_uebersicht(angebote)

    print("\n📊 PREISÜBERSICHT")
    print("═" * 50)

    for marke, s in stats.items():
        print(f"\n🏷️ {marke}")
        print(f"  Angebote: {s['anzahl']}")

        if s["min"]:
            print(f"  💰 Günstigster Preis: {s['min']} €")

        if s["max"]:
            print(f"  💰 Teuerster Preis: {s['max']} €")

        if s["avg"]:
            print(f"  📉 Durchschnitt: {s['avg']} €")

        if s["best_pro_liter"]:
            print(f"  🧪 Bester €/L: {s['best_pro_liter']}")




# ── HTTP-Header ───────────────────────────────────────────────────────────────
CHROME_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
}

ANDROID_HEADERS = {
    "User-Agent": "MarktGuru/4.12.0 (Android 13; Pixel 7)",
    "Accept": "application/json",
    "Accept-Language": "de-DE",
    "X-MarktGuru-Client": "android",
}


# ══════════════════════════════════════════════════════════════════════════════
#   Datenmodell
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class Angebot:
    markt:        str
    produkt:      str
    preis:        Optional[str]
    marke:        str            = "Unbekannt"
    normalpreis:  Optional[str]  = None
    ersparnis:    Optional[str]  = None
    gueltig_von:  Optional[str]  = None
    gueltig_bis:  Optional[str]  = None
    url:          str            = ""
    bild_url:     Optional[str]  = None
    quelle:       str            = "scraper"   # 'api' | 'scraper' | 'community'
    extra:        dict           = field(default_factory=dict)

    def __post_init__(self):
        if self.marke == "Unbekannt":
            self.marke = marke_von(self.produkt)

    def zeile(self) -> str:
        emoji = _AKTIVE_MARKEN.get(self.marke, {}).get("emoji", "🥤")
        teile = [f"{emoji} [{self.markt}]", self.produkt]
        if self.preis:
            teile.append(f"→ {self.preis}")
        if self.normalpreis:
            teile.append(f"(statt {self.normalpreis})")
        if self.gueltig_bis:
            teile.append(f"bis {self.gueltig_bis}")
        return "  ".join(teile)


# ══════════════════════════════════════════════════════════════════════════════
#   HTTP-Helfer
# ══════════════════════════════════════════════════════════════════════════════
def cffi_get(url: str, params: dict | None = None, headers: dict | None = None,
             timeout: int = 20, json_mode: bool = False):
    h = {**CHROME_HEADERS, **(headers or {})}
    try:
        if CFFI_OK:
            r = cffi_requests.get(url, params=params, headers=h, timeout=timeout,
                                  impersonate="chrome120")
        else:
            r = _requests.get(url, params=params, headers=h, timeout=timeout)
        r.raise_for_status()
        return r.json() if json_mode else r
    except Exception as e:
        log.debug(f"cffi_get {url[:70]} → {e}")
        return None


def plain_get(url: str, params: dict | None = None, headers: dict | None = None,
              timeout: int = 20, json_mode: bool = False):
    h = {**ANDROID_HEADERS, **(headers or {})}
    try:
        r = _requests.get(url, params=params, headers=h, timeout=timeout)
        r.raise_for_status()
        return r.json() if json_mode else r
    except Exception as e:
        log.debug(f"plain_get {url[:70]} → {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
#   HTML-Parser (generisch)
# ══════════════════════════════════════════════════════════════════════════════
try:
    from bs4 import BeautifulSoup
    BS4_OK = True
except ImportError:
    BS4_OK = False

def _html_parse(r, markt: str, url: str) -> list[Angebot]:
    if not r or not BS4_OK:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    CARD_SELS = [
        "[data-testid*='product']", "[class*='ProductCard']", "[class*='product-card']",
        "[class*='product-item']", "[class*='offer-item']", "[class*='OfferItem']",
        ".mod-article-tile", "[class*='article-tile']", "[class*='grid-item']",
    ]
    NAME_SELS  = ["h2", "h3", "[class*='title']", "[class*='name']", "[class*='heading']"]
    PREIS_SELS = ["[class*='price']", "[class*='Price']", "[class*='amount']"]

    angebote = []
    karten = []
    for sel in CARD_SELS:
        karten = soup.select(sel)
        if karten:
            break

    for card in karten:
        name = ""
        for ns in NAME_SELS:
            el = card.select_one(ns)
            if el:
                name = el.get_text(strip=True)
                break
        if not name:
            name = card.get_text(" ", strip=True)[:80]
        if not ist_energy(name):
            continue

        preis = None
        for ps in PREIS_SELS:
            el = card.select_one(ps)
            if el:
                preis = preis_clean(el.get_text())
                break

        img_el  = card.select_one("img[src]")
        link_el = card.select_one("a[href]")
        angebote.append(Angebot(
            markt=markt, produkt=name, preis=preis,
            url=(urljoin(url, link_el["href"]) if link_el else url),
            bild_url=(img_el.get("src") or img_el.get("data-src")) if img_el else None,
            quelle="scraper",
        ))
    return angebote


def _json_ld_parse(r, markt: str, url: str) -> list[Angebot]:
    """Extrahiert Produkte aus JSON-LD wenn HTML-Karten nicht gefunden."""
    angebote = []
    if not r or not BS4_OK:
        return angebote
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup.select("script[type='application/ld+json']"):
        try:
            d = json.loads(tag.string or "")
            items = d if isinstance(d, list) else [d]
            for item in items:
                name = item.get("name", "")
                if not ist_energy(name):
                    continue
                offers = item.get("offers", {})
                preis = offers.get("price") if isinstance(offers, dict) else None
                angebote.append(Angebot(
                    markt=markt, produkt=name,
                    preis=f"{preis} €" if preis else None,
                    url=item.get("url", url), quelle="scraper",
                ))
        except (json.JSONDecodeError, AttributeError):
            pass
    return angebote


def _cffi_scrape(markt: str, url: str, extra_headers: dict | None = None) -> list[Angebot]:
    r = cffi_get(url, headers=extra_headers)
    ergebnis = _html_parse(r, markt, url)
    if not ergebnis:
        ergebnis = _json_ld_parse(r, markt, url)
    return ergebnis


# ══════════════════════════════════════════════════════════════════════════════
#   STRATEGIE 1 – Marktguru API
# ══════════════════════════════════════════════════════════════════════════════
MARKTGURU_URL = "https://www.marktguru.de/api/v1/offers"

def scrape_marktguru() -> list[Angebot]:
    log.info("🔍  Marktguru API …")
    angebote: list[Angebot] = []

    for query in SEARCH_QUERIES:
        params = {"as": "android", "lang": "de", "limit": 50, "offset": 0, "q": query}
        data = plain_get(MARKTGURU_URL, params=params, json_mode=True)
        if not data:
            data = cffi_get(MARKTGURU_URL, params=params,
                            headers={"Accept": "application/json"}, json_mode=True)
        if not isinstance(data, dict):
            continue

        for o in data.get("offers", data.get("data", data.get("results", []))):
            name = o.get("name", o.get("title", ""))
            if not ist_energy(name):
                continue
            preis     = o.get("price")
            old_preis = o.get("regularPrice", o.get("oldPrice"))
            von       = o.get("validFrom",  o.get("startDate"))
            bis       = o.get("validUntil", o.get("endDate"))
            markt_i   = o.get("retailer", o.get("store", {}))
            markt     = markt_i.get("name", "Unbekannt") if isinstance(markt_i, dict) else str(markt_i)
            angebote.append(Angebot(
                markt=markt, produkt=name,
                preis=f"{preis} €" if preis else None,
                normalpreis=f"{old_preis} €" if old_preis else None,
                gueltig_von=str(von)[:10] if von else None,
                gueltig_bis=str(bis)[:10] if bis else None,
                url=o.get("url", "https://www.marktguru.de"),
                bild_url=o.get("imageUrl", o.get("image")),
                quelle="api",
            ))
        time.sleep(0.5)

    log.info(f"   Marktguru: {len(angebote)} Treffer")
    return angebote


# ══════════════════════════════════════════════════════════════════════════════
#   STRATEGIE 1b – KaufDA API
# ══════════════════════════════════════════════════════════════════════════════
KAUFDA_URL = "https://www.kaufda.de/webapp/api/search"

def scrape_kaufda() -> list[Angebot]:
    log.info("🔍  KaufDA API …")
    angebote: list[Angebot] = []

    for query in SEARCH_QUERIES:
        params = {"q": query, "types": "offer", "limit": 50}
        data = plain_get(KAUFDA_URL, params=params,
                         headers={"Accept": "application/json", "User-Agent": "KaufDA/5.0 (Android)"},
                         json_mode=True)
        if not data:
            data = cffi_get(KAUFDA_URL, params=params, json_mode=True)
        if not isinstance(data, dict):
            continue

        for item in data.get("results", data.get("offers", data.get("hits", []))):
            src = item.get("_source", item)
            name = src.get("title", src.get("name", ""))
            if not ist_energy(name):
                continue
            preis = src.get("price", src.get("currentPrice"))
            bis   = src.get("validTo", src.get("endDate"))
            markt = src.get("storeName", src.get("retailer", "KaufDA"))
            angebote.append(Angebot(
                markt=markt, produkt=name,
                preis=f"{preis} €" if preis else None,
                gueltig_bis=str(bis)[:10] if bis else None,
                url=src.get("url", "https://www.kaufda.de"),
                bild_url=src.get("image"), quelle="api",
            ))
        time.sleep(0.5)

    log.info(f"   KaufDA: {len(angebote)} Treffer")
    return angebote


# ══════════════════════════════════════════════════════════════════════════════
#   STRATEGIE 1c – Pepper / mydealz Community-Deals
# ══════════════════════════════════════════════════════════════════════════════
def scrape_pepper() -> list[Angebot]:
    log.info("🔍  Pepper/mydealz …")
    angebote: list[Angebot] = []

    extra_queries = list(SEARCH_QUERIES) + ["energy drink"]
    seen_ids: set = set()

    for query in extra_queries:
        for url, site in [
            (f"https://www.pepper.com/api/v1/threads?locale=de&q={query}&limit=50", "pepper"),
            (f"https://www.mydealz.de/api/v1/threads?q={query}&limit=50", "mydealz"),
        ]:
            data = plain_get(url, headers={"Accept": "application/json"}, json_mode=True)
            if not data:
                data = cffi_get(url, headers={"Accept": "application/json"}, json_mode=True)
            if not isinstance(data, dict):
                continue

            for t in data.get("data", data.get("threads", [])):
                tid = t.get("id")
                if tid in seen_ids:
                    continue
                name = t.get("title", "")
                if not ist_energy(name):
                    continue
                seen_ids.add(tid)
                preis     = t.get("price", t.get("nextBestPrice"))
                markt_i   = t.get("merchant", {})
                markt     = markt_i.get("name", "Unbekannt") if isinstance(markt_i, dict) else str(markt_i)
                angebote.append(Angebot(
                    markt=markt, produkt=name,
                    preis=f"{preis} €" if preis else None,
                    ersparnis=t.get("percentageDiscount"),
                    url=t.get("shareableUrl", t.get("link", "")),
                    quelle="community",
                ))
        time.sleep(0.4)

    log.info(f"   Pepper/mydealz: {len(angebote)} Treffer")
    return angebote


# ══════════════════════════════════════════════════════════════════════════════
#   STRATEGIE 2 – curl_cffi HTML-Scraper & Spezial-APIs je Markt
# ══════════════════════════════════════════════════════════════════════════════
def _multi_query_scrape(markt: str, url_template: str) -> list[Angebot]:
    angebote: list[Angebot] = []
    seen: set[str] = set()
    for query in SEARCH_QUERIES:
        url = url_template.replace("{q}", query.replace(" ", "+"))
        for a in _cffi_scrape(markt, url):
            key = a.produkt.lower()[:40]
            if key not in seen:
                seen.add(key)
                angebote.append(a)
        time.sleep(0.6)
    return angebote


def scrape_rewe() -> list[Angebot]:
    log.info("🔍  REWE (curl_cffi) …")
    angebote = _multi_query_scrape(
        "REWE",
        "https://www.rewe.de/suche/?search={q}&serviceType=DELIVERY",
    )
    for query in SEARCH_QUERIES:
        data = plain_get(
            "https://mobile-api.rewe.de/api/v3/all-items",
            params={"search": query, "pageSize": 50},
            headers={"User-Agent": "REWE-App/3.19 (Android)", "Accept": "application/json"},
            json_mode=True,
        )
        if isinstance(data, dict):
            for item in data.get("products", data.get("items", [])):
                name = item.get("name", "")
                if not ist_energy(name):
                    continue
                pi = item.get("pricing", {})
                preis = pi.get("currentRetailPrice") if isinstance(pi, dict) else None
                angebote.append(Angebot(
                    markt="REWE", produkt=name,
                    preis=f"{preis} €" if preis else None,
                    url="https://www.rewe.de", quelle="api",
                ))
    log.info(f"   REWE: {len(angebote)} Treffer")
    return angebote


def scrape_lidl() -> list[Angebot]:
    log.info("🔍  Lidl (curl_cffi + API) …")
    angebote: list[Angebot] = []

    for query in SEARCH_QUERIES:
        data = plain_get(
            "https://www.lidl.de/p/api/gridboxes/DE/de",
            params={"search": query},
            headers={"Accept": "application/json"},
            json_mode=True,
        )
        if isinstance(data, (list, dict)):
            items = data if isinstance(data, list) else data.get("gridBoxes", [])
            for p in items:
                name = p.get("productName", p.get("name", ""))
                if not ist_energy(name):
                    continue
                preis = p.get("price", {})
                ps = preis.get("price") if isinstance(preis, dict) else preis
                angebote.append(Angebot(
                    markt="Lidl", produkt=name,
                    preis=preis_clean(str(ps)) if ps else None,
                    url=f"https://www.lidl.de{p.get('canonicalPath', '')}",
                    bild_url=p.get("image"), quelle="api",
                ))
        time.sleep(0.5)

    if not angebote:
        angebote = _multi_query_scrape("Lidl", "https://www.lidl.de/s?q={q}")

    log.info(f"   Lidl: {len(angebote)} Treffer")
    return angebote


def scrape_edeka() -> list[Angebot]:
    log.info("🔍  Edeka (API v2) …")
    angebote: list[Angebot] = []

    market_id = EDEKA_MARKET_ID

    for query in SEARCH_QUERIES:
        params = {
            "q": query,
            "marketId": market_id,
            "size": 50
        }

        data = plain_get(
            "https://www.edeka.de/api/v2/search",
            params=params,
            headers={"Accept": "application/json"},
            json_mode=True
        )

        if not isinstance(data, dict):
            continue

        items = data.get("results", [])

        for item in items:
            name = item.get("name", "")
            if not ist_energy(name):
                continue

            preis = None
            price_info = item.get("price", {})
            if isinstance(price_info, dict):
                preis = price_info.get("formatted")

            angebote.append(Angebot(
                markt="Edeka",
                produkt=name,
                preis=preis,
                url="https://www.edeka.de",
                quelle="api",
            ))

        time.sleep(0.5)

    log.info(f"   Edeka: {len(angebote)} Treffer")
    return angebote


def scrape_aldi_sued() -> list[Angebot]:
    log.info("🔍  Aldi Süd …")
    res = _multi_query_scrape("Aldi Süd", "https://www.aldi-sued.de/de/suche.html?q={q}")
    log.info(f"   Aldi Süd: {len(res)} Treffer")
    return res

def scrape_aldi_nord() -> list[Angebot]:
    log.info("🔍  Aldi Nord …")
    res = _multi_query_scrape("Aldi Nord", "https://www.aldi-nord.de/suche.html?q={q}")
    log.info(f"   Aldi Nord: {len(res)} Treffer")
    return res

def scrape_penny() -> list[Angebot]:
    log.info("🔍  Penny …")
    res = _multi_query_scrape("Penny", "https://www.penny.de/suche?q={q}")
    log.info(f"   Penny: {len(res)} Treffer")
    return res

def scrape_netto() -> list[Angebot]:
    log.info("🔍  Netto …")
    res = _multi_query_scrape("Netto", "https://www.netto-online.de/search?q={q}")
    log.info(f"   Netto: {len(res)} Treffer")
    return res

def scrape_kaufland() -> list[Angebot]:
    log.info("🔍  Kaufland …")
    res = _multi_query_scrape("Kaufland", "https://www.kaufland.de/search-results/?search_value={q}")
    log.info(f"   Kaufland: {len(res)} Treffer")
    return res


# ══════════════════════════════════════════════════════════════════════════════
#   Scraper-Registry
# ══════════════════════════════════════════════════════════════════════════════
SCRAPER: dict[str, callable] = {
    "Marktguru":  scrape_marktguru,
    "KaufDA":     scrape_kaufda,
    "Pepper":     scrape_pepper,
    "REWE":       scrape_rewe,
    "Lidl":       scrape_lidl,
    "Edeka":      scrape_edeka,
    "Aldi Süd":   scrape_aldi_sued,
    "Aldi Nord":  scrape_aldi_nord,
    "Penny":      scrape_penny,
    "Netto":      scrape_netto,
    "Kaufland":   scrape_kaufland,
}


# ══════════════════════════════════════════════════════════════════════════════
#   Duplikat-Filter
# ══════════════════════════════════════════════════════════════════════════════
def deduplizieren(angebote: list[Angebot]) -> list[Angebot]:
    gesehen: set[tuple] = set()
    unique = []
    for a in angebote:
        key = (a.markt.lower(), a.produkt.lower()[:40], a.preis)
        if key not in gesehen:
            gesehen.add(key)
            unique.append(a)
    return unique


# ══════════════════════════════════════════════════════════════════════════════
#   Ausgabe
# ══════════════════════════════════════════════════════════════════════════════
def ausgeben_rich(angebote: list[Angebot]) -> None:
    from rich.panel import Panel
    from rich import box

    console.print(Panel(
        f"[bold green]🥤 {len(angebote)} Energy Drink Angebote[/bold green]\n"
        f"[dim]Marken: {', '.join(_AKTIVE_MARKEN)}[/dim]\n"
        f"[dim]Stand: {datetime.now().strftime('%d.%m.%Y %H:%M')}[/dim]",
        expand=False,
    ))

    nach_marke: dict[str, list[Angebot]] = {}
    for a in angebote:
        nach_marke.setdefault(a.marke, []).append(a)

    for marke, liste in sorted(nach_marke.items()):
        emoji = _AKTIVE_MARKEN.get(marke, {}).get("emoji", "🥤")
        t = Table(
            title=f"{emoji} [bold]{marke}[/bold]  ({len(liste)} Angebot{'e' if len(liste)!=1 else ''})",
            box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan",
        )
        t.add_column("Markt",       style="bold",    width=14)
        t.add_column("Produkt",     style="white",   max_width=42)
        t.add_column("Preis",       style="green",   width=10)
        t.add_column("Normalpreis", style="red dim", width=12)
        t.add_column("Gültig bis",  style="yellow",  width=11)
        t.add_column("Quelle",      style="dim",     width=9)
        for a in sorted(liste, key=lambda x: x.markt):
            t.add_row(
                a.markt[:14], a.produkt[:42],
                a.preis or "–", a.normalpreis or "",
                a.gueltig_bis or "", a.quelle,
            )
        console.print(t)


def ausgeben_plain(angebote: list[Angebot]) -> None:
    if not angebote:
        print("\n❌  Keine Energy-Drink-Angebote gefunden.\n")
        return

    print(f"\n{'═'*65}")
    print(f"  🥤  {len(angebote)} Energy Drink Angebote")
    print(f"  🏷️    Marken: {', '.join(_AKTIVE_MARKEN)}")
    print(f"  📅  Stand: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    print(f"{'═'*65}\n")

    nach_marke: dict[str, list[Angebot]] = {}
    for a in angebote:
        nach_marke.setdefault(a.marke, []).append(a)

    for marke, liste in sorted(nach_marke.items()):
        emoji = _AKTIVE_MARKEN.get(marke, {}).get("emoji", "🥤")
        print(f"{'─'*65}")
        print(f"  {emoji}  {marke}  ({len(liste)} Angebot{'e' if len(liste)!=1 else ''})")
        print(f"{'─'*65}")
        for a in sorted(liste, key=lambda x: x.markt):
            print(f"  🏪  {a.markt}")
            print(f"  📦  {a.produkt}")
            if a.preis:
                s = f"  💰  {a.preis}"
                if a.normalpreis:
                    s += f"  (statt {a.normalpreis})"
                print(s)
            if a.gueltig_von or a.gueltig_bis:
                print(f"  📅  {a.gueltig_von or '?'} – {a.gueltig_bis or '?'}")
            print(f"  🔗  {a.url}")
            print()


def ausgeben(angebote: list[Angebot]) -> None:
    if not angebote:
        print("\n❌  Keine Energy-Drink-Angebote gefunden.\n")
        return
    if RICH and console:
        ausgeben_rich(angebote)
    else:
        ausgeben_plain(angebote)


def als_json(angebote: list[Angebot], pfad: str) -> None:
    with open(pfad, "w", encoding="utf-8") as f:
        json.dump(
            {"generiert_am": datetime.now().isoformat(),
             "marken": list(_AKTIVE_MARKEN.keys()),
             "anzahl": len(angebote),
             "angebote": [asdict(a) for a in angebote]},
            f, ensure_ascii=False, indent=2,
        )
    print(f"💾  JSON exportiert: {pfad}")


# ══════════════════════════════════════════════════════════════════════════════
#   Öffentliche API
# ══════════════════════════════════════════════════════════════════════════════
def alle_angebote(
    maerkte: list[str] | None = None,
    marken: list[str] | None = None,
    pause: float = 1.2,
) -> list[Angebot]:
    _init_marken(marken)

    if not CFFI_OK:
        log.warning("curl_cffi fehlt – HTML-Scraping weniger effektiv.\n  → pip install curl_cffi")

    auswahl = maerkte or list(SCRAPER.keys())
    alle: list[Angebot] = []

    for markt in auswahl:
        if markt not in SCRAPER:
            log.warning(f"Unbekannter Markt: '{markt}'  (verfügbar: {', '.join(SCRAPER)})")
            continue
        try:
            alle.extend(SCRAPER[markt]())
        except Exception as e:
            log.error(f"Fehler bei {markt}: {e}", exc_info=True)
        time.sleep(pause)

    return deduplizieren(alle)


# ══════════════════════════════════════════════════════════════════════════════
#   CLI
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Energy Drink Angebotsscraper (Marktguru API + curl_cffi)",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            f"Märkte:  {', '.join(SCRAPER)}\n"
            f"Marken:  {', '.join(MARKEN)}"
        ),
    )
    parser.add_argument("--maerkte", nargs="*", metavar="MARKT",
                        help="Nur bestimmte Märkte (Standard: alle)")
    parser.add_argument("--marken", nargs="*", metavar="MARKE",
                        help=f"Nur bestimmte Marken (Standard: alle)\nVerfügbar: {', '.join(MARKEN)}")
    parser.add_argument("--json", metavar="DATEI",
                        help="Ergebnisse als JSON speichern")
    parser.add_argument("--pause", type=float, default=1.2,
                        help="Pause zwischen Anfragen in Sek. (Standard: 1.2)")
    parser.add_argument("--verbose", action="store_true",
                        help="Debug-Logging aktivieren")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    ergebnisse = alle_angebote(
        maerkte=args.maerkte,
        marken=args.marken,
        pause=args.pause,
    )
    ausgeben(ergebnisse)
    ausgeben_preisstats(ergebnisse)

    if args.json:
        als_json(ergebnisse, args.json)
