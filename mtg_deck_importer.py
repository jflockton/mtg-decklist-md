"""MTG Deck Importer — deck URL -> Obsidian vault deck note.

Usage:
    python mtg_deck_importer.py [--force] https://moxfield.com/decks/<public_id>
    python mtg_deck_importer.py [--force] https://edhrec.com/deckpreview/<hash>
    python mtg_deck_importer.py [--force] "path/to/My Deck.txt"

A .txt file is a decklist in the usual export format — one "1 Card Name" per
line, first card is the commander. The file name (minus .txt) becomes the
deck name.

An existing deck note is never overwritten (your review notes live in it) —
pass --force to regenerate it anyway.

--own marks the whole deck as owned: its card list is appended to
_Collection.md (under the deck's name) before the comparison runs — use it
when you buy a precon you'd imported as a wishlist deck.

`python mtg_deck_importer.py --recheck` (no URL) refreshes every note's
Cards to Buy section against the current _Collection.md, using the deck
list stored in each note — run it after updating your collection. No site
fetching, no browser; review sections and everything else stay untouched.

Fetches the deck list (Moxfield via a headed browser because of Cloudflare;
EDHREC via plain HTTP), downloads the commander's artwork from Scryfall, and
writes a markdown deck note into the Obsidian vault folder set by the
VAULT_OUTPUT_DIR environment variable (usually via a .env file next to this
script — see .env.example).

Output note:  YYYY-MM-DD_MTG_<Commander Name>.md
Output image: Attachments/YYYY-MM-DD_MTG_<Commander Name>.jpg
"""

import atexit
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).parent

USER_AGENT = "mtg-decklist-md/1.0 (personal Obsidian tool)"
HTTP_HEADERS = {"User-Agent": USER_AGENT, "Accept": "*/*"}

SCRYFALL_NAMED = "https://api.scryfall.com/cards/named"
SCRYFALL_COLLECTION = "https://api.scryfall.com/cards/collection"
SCRYFALL_SEARCH = "https://api.scryfall.com/cards/search"
ECB_RATES = "https://api.frankfurter.dev/v1/latest"
MANAPOOL_PRICES = "https://manapool.com/api/v1/prices/singles"
# Cache lives in .cache/ so tab-completing "m..." never hands the JSON file
# to python by mistake (it sorts before mtg_deck_importer.py otherwise)
MANAPOOL_CACHE = SCRIPT_DIR / ".cache" / "manapool_prices.json"
MOXFIELD_API = "https://api2.moxfield.com/v3/decks/all/{deck_id}"

# Windows-illegal filename characters (commas are fine and kept)
ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')

# Moxfield export decorations after a card name: foil/etched markers (*F*),
# collector info like (PLST) 123 — strip so names match Scryfall
NAME_DECORATIONS = re.compile(r"\s*(\*[A-Za-z]\*|\([A-Z0-9]{2,6}\)\s*[\w-]*)\s*$")


def parse_card_line(line: str) -> tuple[int, str] | None:
    line = line.strip()
    if not line:
        return None
    m = re.match(r"(\d+)[xX]?\s+(.+)", line)
    qty, name = (int(m.group(1)), m.group(2)) if m else (1, line)
    name = NAME_DECORATIONS.sub("", name).strip()
    return (qty, name) if name else None


def load_collection(out_dir: Path) -> tuple[str, dict[str, int]] | None:
    """Owned cards from _Collection.md next to the deck notes (override with
    COLLECTION_FILE in .env). Only 'N Card Name' lines count — headings,
    prose, and blank lines are ignored, so the file can be a normal note.
    Returns (file name, {lowercased name: owned qty}) or None if absent.
    """
    path = collection_path(out_dir)
    if not path.is_file():
        return None
    owned: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not re.match(r"\d", line.strip()):
            continue  # only 'N Card Name' lines count in a collection note
        parsed = parse_card_line(line)
        if parsed:
            qty, name = parsed
            owned[name.lower()] = owned.get(name.lower(), 0) + qty
    return path.name, owned


_card_info_cache: dict[str, dict] = {}
_prints_cache_loaded = False
_prints_cache_dirty = False
PRINTS_CACHE = SCRIPT_DIR / ".cache" / "scryfall_prints.json"
PRINTS_TTL = 72 * 3600  # prices drift slowly; 3 days is fine for budget hints


def _load_prints_cache() -> None:
    global _prints_cache_loaded
    _prints_cache_loaded = True
    try:
        raw = json.loads(PRINTS_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return
    now = time.time()
    for key, val in raw.items():
        if now - val.get("ts", 0) < PRINTS_TTL:
            _card_info_cache[key] = {"aliases": set(val["aliases"]),
                                     "canonical": val["canonical"],
                                     "cheapest": val["cheapest"],
                                     "ts": val["ts"]}


def _save_prints_cache() -> None:
    if not _prints_cache_dirty:
        return
    try:
        PRINTS_CACHE.parent.mkdir(exist_ok=True)
        out = {k: {"aliases": sorted(v["aliases"]), "canonical": v["canonical"],
                   "cheapest": v["cheapest"], "ts": v.get("ts", time.time())}
               for k, v in _card_info_cache.items()}
        PRINTS_CACHE.write_text(json.dumps(out), encoding="utf-8")
    except Exception:
        pass


atexit.register(_save_prints_cache)


def card_prints_info(name: str) -> dict:
    """One prints lookup per card, reused for two jobs: (a) every name the
    card can appear under — canonical plus Universes Beyond flavor names
    (the Marvel precons print Spark Double as "Loki's Double"); (b) the
    cheapest paper printing, since a flavor-named skin is the same card and
    the plain version is often cheaper.
    """
    if not _prints_cache_loaded:
        _load_prints_cache()
    key = name.lower()
    if key in _card_info_cache:
        return _card_info_cache[key]
    global _prints_cache_dirty
    _prints_cache_dirty = True
    info = {"aliases": {key}, "canonical": name, "cheapest": None,
            "ts": time.time()}
    r = http("GET", SCRYFALL_NAMED, params={"exact": name})
    time.sleep(0.1)
    if r.status_code == 200:
        info["canonical"] = r.json()["name"]
        info["aliases"].add(info["canonical"].lower())
        s = http("GET", SCRYFALL_SEARCH,
                 params={"q": f'!"{info["canonical"]}" game:paper',
                         "unique": "prints"})
        time.sleep(0.1)
        if s.status_code == 200:
            best = None
            for c in s.json().get("data", []):
                if c.get("flavor_name"):
                    info["aliases"].add(c["flavor_name"].lower())
                eur = float(c["prices"]["eur"]) if c["prices"].get("eur") else None
                usd = float(c["prices"]["usd"]) if c["prices"].get("usd") else None
                if eur is None and usd is None:
                    continue
                rank = (eur if eur is not None else float("inf"),
                        usd if usd is not None else float("inf"))
                if best is None or rank < best[0]:
                    best = (rank, {"eur": eur, "usd": usd, "set": c["set_name"],
                                   "printed_as": c.get("flavor_name") or c["name"]})
            if best:
                info["cheapest"] = best[1]
    _card_info_cache[key] = info
    return info


def buy_report(decklist: list[tuple[int, str]], owned: dict[str, int],
               prices: dict[str, dict]) -> dict:
    """Which deck cards are missing from the collection, and what the gap
    costs (per-source totals use the already-fetched deck prices).
    """
    missing = []
    totals = {"eur": 0.0, "usd": 0.0, "mp": 0.0}
    owned_unique = owned_copies = total_copies = 0
    for qty, name in decklist:
        total_copies += qty
        have = owned.get(name.lower(), 0)
        info = None
        if have < qty:
            # Not obviously owned — check flavor-name aliases before giving up
            info = card_prints_info(name)
            have = sum(owned.get(a, 0) for a in info["aliases"])
        have = min(have, qty)
        owned_copies += have
        if have >= qty:
            owned_unique += 1
            continue
        need = qty - have
        p = prices.get(name.lower()) or {}
        totals["eur"] += (p.get("eur") or 0) * need
        totals["usd"] += (p.get("usd") or 0) * need
        totals["mp"] += (p.get("mp") or 0) * need
        missing.append((need, name, p.get("eur"), p.get("usd"), p.get("mp"), info))
    missing.sort(key=lambda c: c[2] or 0, reverse=True)
    return {"missing": missing, "totals": totals,
            "owned_unique": owned_unique, "owned_copies": owned_copies,
            "unique": len(decklist), "total_copies": total_copies}


def http(method: str, url: str, **kwargs) -> requests.Response:
    """Request with polite backoff on 429/5xx — Scryfall rate-limits bursts."""
    r = None
    for attempt in range(5):
        r = requests.request(method, url, headers=HTTP_HEADERS, timeout=30, **kwargs)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(float(r.headers.get("Retry-After", 2)) + attempt)
            continue
        return r
    return r


_mp_index: dict[str, float] | None = None


def manapool_index() -> dict[str, float]:
    """Card name -> cheapest non-foil ManaPool listing in USD, at the
    condition set by MANAPOOL_CONDITION in .env (any | lp | nm; default lp).
    ManaPool's full catalog (~50 MB) is cached next to the script and only
    re-downloaded when the cache is older than 24 hours — repeat runs in the
    same day read the local file. Prices are live lowest listings on a US
    marketplace (shipping not included).
    """
    global _mp_index
    if _mp_index is not None:
        return _mp_index
    cond = os.environ.get("MANAPOOL_CONDITION", "lp").lower()
    field = {"any": "price_cents", "lp": "price_cents_lp_plus",
             "nm": "price_cents_nm"}.get(cond, "price_cents_lp_plus")
    try:
        MANAPOOL_CACHE.parent.mkdir(exist_ok=True)
        legacy = SCRIPT_DIR / "manapool_prices.json"
        if legacy.is_file() and not MANAPOOL_CACHE.is_file():
            legacy.replace(MANAPOOL_CACHE)
        stale = (not MANAPOOL_CACHE.is_file()
                 or time.time() - MANAPOOL_CACHE.stat().st_mtime > 86400)
        if stale:
            r = http("GET", MANAPOOL_PRICES)
            r.raise_for_status()
            MANAPOOL_CACHE.write_bytes(r.content)
            print(f"ManaPool:  price catalog refreshed ({MANAPOOL_CACHE.name})")
        data = json.loads(MANAPOOL_CACHE.read_text(encoding="utf-8"))["data"]
    except Exception as exc:
        print(f"ManaPool:  unavailable ({exc}) — MP prices skipped this run")
        _mp_index = {}
        return _mp_index
    index: dict[str, float] = {}
    for card in data:
        cents = card.get(field)
        if not cents:
            continue
        name = card["name"].lower()
        usd = cents / 100
        if name not in index or usd < index[name]:
            index[name] = usd
    _mp_index = index
    return index


def collection_path(out_dir: Path) -> Path:
    return Path(os.environ.get("COLLECTION_FILE") or out_dir / "_Collection.md")


def add_deck_to_collection(out_dir: Path, deck_name: str,
                           decklist: list[tuple[int, str]]) -> bool:
    """Append the whole deck under its own heading in _Collection.md (--own).
    Returns False if a section for this deck already exists.
    """
    path = collection_path(out_dir)
    heading = f"## 📦 {deck_name}"
    existing = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    if heading.lower() in existing.lower():
        return False
    listing = "\n".join(f"{qty} {name}" for qty, name in decklist)
    block = f"\n{heading} (added {date.today().isoformat()})\n\n{listing}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(block)
    return True


def output_dir() -> Path:
    # .env sits next to the script; a real environment variable wins over it
    load_dotenv(SCRIPT_DIR / ".env")
    value = os.environ.get("VAULT_OUTPUT_DIR")
    if not value:
        sys.exit(
            "VAULT_OUTPUT_DIR is not set.\n"
            "Copy .env.example to .env and point it at your vault's deck folder."
        )
    return Path(value)


# ---------------------------------------------------------------------------
# Fetchers — each returns a normalized deck:
#   {"name", "format", "source", "commanders": [names],
#    "mainboard": [(qty, name), ...]}   (mainboard excludes the commanders)
# ---------------------------------------------------------------------------

def fetch_moxfield(url: str) -> dict:
    """Moxfield: Cloudflare blocks plain HTTP clients (403) and headless
    browsers (block page), so the deck JSON is fetched from inside a headed
    system Chrome — a visible window appears for a few seconds per run.
    """
    from playwright.sync_api import sync_playwright

    deck_id = re.search(r"moxfield\.com/decks/([A-Za-z0-9_-]+)", url).group(1)
    api_url = MOXFIELD_API.format(deck_id=deck_id)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        page = browser.new_page()
        page.goto(f"https://moxfield.com/decks/{deck_id}", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        result = page.evaluate(
            """async (apiUrl) => {
                const r = await fetch(apiUrl, {headers: {"x-requested-with": "XMLHttpRequest"}});
                return {status: r.status, body: await r.text()};
            }""",
            api_url,
        )
        browser.close()
    if result["status"] != 200:
        sys.exit(f"Moxfield API returned HTTP {result['status']} for {api_url}")
    deck = json.loads(result["body"])

    def board(name):
        cards = deck.get("boards", {}).get(name, {}).get("cards", {})
        return [(c["quantity"], c["card"]["name"]) for c in cards.values()]

    return {
        "name": deck["name"],
        "format": deck.get("format", "commander").title(),
        "source_md": f"[Moxfield]({url})",
        "commanders": sorted(name for _, name in board("commanders")),
        "mainboard": board("mainboard"),
    }


def fetch_edhrec(url: str) -> dict:
    """EDHREC deck previews are plain server-rendered Next.js pages — the deck
    ships inside the __NEXT_DATA__ JSON blob, no browser needed.
    """
    r = requests.get(url, headers=HTTP_HEADERS, timeout=30)
    r.raise_for_status()
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        r.text, re.S,
    )
    if not m:
        sys.exit("Could not find __NEXT_DATA__ on the EDHREC page — layout changed?")
    data = json.loads(m.group(1))["props"]["pageProps"]["data"]

    commanders = sorted(data.get("commanders") or [])
    cards = []
    for line in data.get("deck") or []:
        qty_name = re.match(r"(\d+)\s+(.*)", line)
        cards.append((int(qty_name.group(1)), qty_name.group(2)) if qty_name else (1, line))
    mainboard = [(q, n) for q, n in cards if n not in commanders]

    return {
        "name": data.get("header") or f"Deck with {', '.join(commanders)}",
        "format": "Commander (cEDH)" if data.get("cedh") else "Commander",
        "source_md": f"[EDHREC]({url})",
        "commanders": commanders,
        "mainboard": mainboard,
    }


def fetch_textfile(path_str: str) -> dict:
    """A local decklist in the standard export format: one 'N Card Name' per
    line, first card is the commander. Deck name = file name without .txt.
    """
    path = Path(path_str)
    cards: list[tuple[int, str]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        parsed = parse_card_line(line)
        if parsed:
            cards.append(parsed)
    if not cards:
        sys.exit(f"No cards found in {path}")
    commander = cards[0][1]
    # Merge duplicate lines (Moxfield exports split printings onto separate rows)
    merged: dict[str, int] = {}
    for qty, name in cards[1:]:
        if name != commander:
            merged[name] = merged.get(name, 0) + qty
    return {
        "name": path.stem,
        "format": "Commander",
        "source_md": f"📄 `{path.name}`",
        "commanders": [commander],
        "mainboard": [(q, n) for n, q in merged.items()],
        "txt_path": str(path),
    }


FETCHERS = [
    (re.compile(r"moxfield\.com/decks/"), fetch_moxfield),
    (re.compile(r"edhrec\.com/deckpreview"), fetch_edhrec),
]


def fetch_deck(url: str) -> dict:
    for pattern, fetcher in FETCHERS:
        if pattern.search(url):
            return fetcher(url)
    if url.lower().endswith(".txt"):
        if Path(url).is_file():
            return fetch_textfile(url)
        sys.exit(f"Decklist file not found: {Path(url).resolve()}\n"
                 "Check the spelling — the file must already exist.")
    supported = ", ".join(p.pattern.replace("\\", "") for p, _ in FETCHERS)
    sys.exit(f"Unsupported deck source: {url}\n"
             f"Supported: {supported}, or a path to a .txt decklist")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def fetch_commander_art(commander: str, dest: Path) -> str:
    """Download the card image to dest (offline backup) and return its
    Scryfall URL — the note embeds the URL so it renders in any markdown
    viewer, not just Obsidian.
    """
    # Scryfall rejects requests without proper User-Agent/Accept headers
    r = http("GET", SCRYFALL_NAMED, params={"exact": commander})
    r.raise_for_status()
    card = r.json()
    # Double-faced cards keep image_uris per face
    image_uris = card.get("image_uris") or card["card_faces"][0]["image_uris"]
    time.sleep(0.1)  # Scryfall asks for ~10 requests/sec max
    img = http("GET", image_uris["normal"])
    img.raise_for_status()
    dest.write_bytes(img.content)
    return image_uris["normal"]


def fetch_prices(names: list[str]) -> dict[str, dict]:
    """Daily market prices via Scryfall, always for the standard (non-foil)
    card: EUR = Cardmarket, USD = TCGPlayer, TIX = Cardhoarder (MTGO).
    Returns {lowercased card name: {"eur"|"usd"|"tix": float|None}}.
    """
    prices: dict[str, dict] = {}
    for i in range(0, len(names), 75):  # collection endpoint caps at 75 cards
        chunk = names[i:i + 75]
        r = http("POST", SCRYFALL_COLLECTION,
                 json={"identifiers": [{"name": n} for n in chunk]})
        r.raise_for_status()
        for card in r.json().get("data", []):
            p = card.get("prices", {})
            entry = {
                "eur": float(p["eur"]) if p.get("eur") else None,
                "usd": float(p["usd"]) if p.get("usd") else None,
                "tix": float(p["tix"]) if p.get("tix") else None,
                "mp": manapool_index().get(card["name"].lower()),
            }
            prices[card["name"].lower()] = entry
            # Let a front-face name find its double-faced card
            if "//" in card["name"]:
                prices.setdefault(card["name"].split("//")[0].strip().lower(), entry)
        time.sleep(0.1)  # Scryfall asks for ~10 requests/sec max

    # The collection endpoint returns one arbitrary printing per card, which
    # can be an online-only set with no paper prices (e.g. Tempest Remastered
    # Mox Diamond). For those, fall back to the cheapest paper printing.
    for name in names:
        entry = prices.get(name.lower())
        if entry and (entry["eur"] is not None or entry["usd"] is not None):
            continue
        cheap = cheapest_paper_printing(name)
        if cheap:
            cheap["tix"] = entry["tix"] if entry else None
            cheap["mp"] = manapool_index().get(name.lower())
            prices[name.lower()] = cheap
        time.sleep(0.1)
    return prices


def cheapest_paper_printing(name: str) -> dict | None:
    r = http("GET", SCRYFALL_SEARCH,
             params={"q": f'!"{name}" game:paper', "unique": "prints"})
    if r.status_code != 200:
        return None
    printings = r.json().get("data", [])
    eurs = [float(c["prices"]["eur"]) for c in printings if c["prices"].get("eur")]
    usds = [float(c["prices"]["usd"]) for c in printings if c["prices"].get("usd")]
    if not eurs and not usds:
        return None
    return {"eur": min(eurs) if eurs else None, "usd": min(usds) if usds else None}


def fx_rates() -> dict | None:
    """Current ECB reference rates via frankfurter.dev: EUR→GBP and USD→GBP
    (derived through EUR). None if the rate API is unreachable.
    """
    try:
        r = requests.get(ECB_RATES, params={"base": "EUR", "symbols": "GBP,USD"},
                         headers=HTTP_HEADERS, timeout=15)
        r.raise_for_status()
        rates = r.json()["rates"]
        return {"eur_gbp": float(rates["GBP"]),
                "usd_gbp": float(rates["GBP"]) / float(rates["USD"])}
    except Exception:
        return None


def price_report(decklist: list[tuple[int, str]], prices: dict[str, dict]) -> dict:
    totals = {"eur": 0.0, "usd": 0.0, "tix": 0.0, "mp": 0.0}
    coverage = {"eur": 0, "usd": 0, "tix": 0, "mp": 0}
    unpriced = []
    priced_cards = []
    all_cards = []
    for qty, name in decklist:
        p = prices.get(name.lower())
        if not p or (p["eur"] is None and p["usd"] is None):
            unpriced.append(name)
            all_cards.append((qty, name, None, None, None))
            continue
        for src in totals:
            if p.get(src) is not None:
                totals[src] += p[src] * qty
                coverage[src] += 1
        priced_cards.append((name, p["eur"], p["usd"], p.get("mp")))
        all_cards.append((qty, name, p["eur"], p["usd"], p.get("mp")))
    top = sorted(priced_cards, key=lambda c: c[1] or 0, reverse=True)[:10]
    all_cards.sort(key=lambda c: c[2] or 0, reverse=True)
    return {"totals": totals, "coverage": coverage, "unique": len(decklist),
            "top": top, "all": all_cards, "unpriced": unpriced,
            "rates": fx_rates()}


REVIEW_SECTIONS = ["🧠 First Impressions", "💪 Strengths", "⚠️ Weaknesses",
                   "🔄 Cards to Consider Swapping", "📝 Play Notes"]


def _money(eur, usd):
    e = f"€{eur:,.2f}" if eur is not None else "—"
    u = f"${usd:,.2f}" if usd is not None else "—"
    return e, u


def _gbp_cell(amount):
    return f"£{amount:,.2f}" if amount is not None else "—"


def _usd_cell(amount):
    return f"${amount:,.2f}" if amount is not None else "—"


def _sane_cheaper(cheap_eur, deck_eur):
    """Guard against junk market data: a 'cheapest printing' under 5% of the
    deck version's price on a card worth over €1 (e.g. a €0.02 Summer Magic
    Wrath of God) is a data error, not a bargain.
    """
    if cheap_eur is None:
        return False
    if deck_eur and deck_eur > 1 and cheap_eur < deck_eur * 0.05:
        return False
    return deck_eur is None or cheap_eur < deck_eur - 0.005


def _card_gbp(eur, usd, rates):
    # Prefer the Cardmarket EUR price; fall back to USD if only that exists
    if not rates:
        return None
    if eur is not None:
        return eur * rates["eur_gbp"]
    if usd is not None:
        return usd * rates["usd_gbp"]
    return None


def buy_frontmatter(buy: dict, rates: dict | None) -> str:
    lines = [f"owned: {buy['owned_unique']}/{buy['unique']}",
             f"buy-eur: {buy['totals']['eur']:.2f}",
             f"buy-mp: {buy['totals']['mp']:.2f}"]
    if rates:
        lines.append(f"buy-gbp: {buy['totals']['eur'] * rates['eur_gbp']:.2f}")
    return "\n".join(lines)


def render_buy_section(buy: dict, collection_name: str, rates: dict | None) -> str:
    summary = (f"Compared against `{collection_name}` — you own "
               f"**{buy['owned_unique']}/{buy['unique']}** cards "
               f"({buy['owned_copies']}/{buy['total_copies']} copies).")
    if not buy["missing"]:
        return f"""## 🛒 Cards to Buy

{summary}
🎉 **You own every card in this deck — nothing to buy!**

"""
    buy_gbp = _gbp_cell(buy["totals"]["eur"] * rates["eur_gbp"] if rates else None)
    buy_rows = "\n".join(
        f"| {name} | {need} | {_money(eur, usd)[0]} | {_money(eur, usd)[1]} | {_usd_cell(mp)} | {_gbp_cell(_card_gbp(eur, usd, rates))} |"
        for need, name, eur, usd, mp, _ in buy["missing"]
    )

    # Same card, cheaper printing (incl. plain-MTG versions of
    # Universes Beyond skins) — worth a table when it saves anything
    cheaper = []
    best_total_eur = 0.0
    for need, name, eur, usd, _mp, info in buy["missing"]:
        ch = (info or {}).get("cheapest")
        effective = eur
        if ch and _sane_cheaper(ch["eur"], eur):
            label = ch["printed_as"] if ch["printed_as"].lower() != name.lower() \
                else info["canonical"]
            cheaper.append((name, label, ch["set"], ch["eur"], eur, need))
            effective = ch["eur"]
        best_total_eur += (effective or 0) * need
    cheaper_section = ""
    if cheaper:
        saved = buy["totals"]["eur"] - best_total_eur
        cheaper_rows = "\n".join(
            f"| {name} | {label} ({set_name}) | €{cheap:,.2f} | {_money(deck_eur, None)[0]} | €{(deck_eur or 0) - cheap:,.2f} |"
            for name, label, set_name, cheap, deck_eur, _ in cheaper
        )
        best_gbp = _gbp_cell(best_total_eur * rates["eur_gbp"] if rates else None)
        cheaper_section = f"""### 💡 Cheaper Printings

Same card, different printing or name — Universes Beyond skins are only art
and printed-name swaps, so the plain version is functionally identical.

| Card in deck | Cheapest version (set) | EUR | Deck version | Save |
|--------------|------------------------|----:|-------------:|-----:|
{cheaper_rows}

Buying the cheapest printings instead ≈ **€{best_total_eur:,.2f} · {best_gbp}** — saves **€{saved:,.2f}**.

"""
    return f"""## 🛒 Cards to Buy

{summary}
Completing the deck ≈ **€{buy["totals"]["eur"]:,.2f} · ${buy["totals"]["usd"]:,.2f} · MP ${buy["totals"]["mp"]:,.2f} · {buy_gbp}** (prices per copy below).

| Card | Need | EUR | USD | MP $ | ≈ GBP |
|------|-----:|----:|----:|-----:|------:|
{buy_rows}

{cheaper_section}"""


def extract_reviews(text: str) -> dict[str, str]:
    """Pull hand-written review content out of an existing note so a --force
    regeneration refreshes the data without destroying your thoughts.
    """
    reviews = {}
    for heading in REVIEW_SECTIONS:
        m = re.search(rf"## {re.escape(heading)}\n(.*?)(?=\n## )", text, re.S)
        if m:
            body = m.group(1).strip()
            if body and body != "-":
                reviews[heading] = body
    return reviews


def build_note(deck: dict, decklist: list[tuple[int, str]],
               image_url: str, deck_url: str, report: dict,
               buy: dict | None, collection_name: str | None,
               reviews: dict[str, str], budget_section: str) -> str:
    today = date.today().isoformat()
    commander_line = ", ".join(deck["commanders"])
    listing = "\n".join(f"{qty} {name}" for qty, name in decklist)

    price_frontmatter = price_frontmatter_str(report)
    if buy is not None:
        price_frontmatter += "\n" + buy_frontmatter(buy, report["rates"])
    buy_section = render_buy_section(buy, collection_name, report["rates"]) \
        if buy is not None else ""
    review_block = "\n\n".join(
        f"## {heading}\n\n{reviews.get(heading, '-')}" for heading in REVIEW_SECTIONS
    )

    return f"""---
tags: [mtg, deck, commander]
created: {today}
commander: {commander_line}
deck-name: {deck["name"]}
deck-url: {deck_url}
{price_frontmatter}
price-date: {today}
---

# 🃏 {deck["name"]}

**Commander:** {commander_line}
**Format:** {deck["format"]}
**Source:** {deck["source_md"]}

{render_value_block(report, today)}

![{commander_line}|290]({image_url})

{review_block}

{buy_section}{render_card_tables(report)}## 📜 Deck List

```
{listing}
```

{budget_section}"""


def render_value_block(report: dict, today: str) -> str:
    totals, coverage = report["totals"], report["coverage"]
    unique, rates = report["unique"], report["rates"]
    # tix trade at roughly $1 each on MTGO, so they convert via the USD rate
    source_rows = [
        ("🇪🇺 Cardmarket", f"€{totals['eur']:,.2f}",
         totals["eur"] * rates["eur_gbp"] if rates else None, coverage["eur"]),
        ("🇺🇸 TCGPlayer", f"${totals['usd']:,.2f}",
         totals["usd"] * rates["usd_gbp"] if rates else None, coverage["usd"]),
        ("🛍️ ManaPool", f"${totals['mp']:,.2f}",
         totals["mp"] * rates["usd_gbp"] if rates else None, coverage["mp"]),
        ("🖥️ Cardhoarder (MTGO)", f"{totals['tix']:,.2f} tix",
         totals["tix"] * rates["usd_gbp"] if rates else None, coverage["tix"]),
    ]
    value_rows = "\n".join(
        f"| {label} | {native} | {_gbp_cell(gbp)} | {cov}/{unique} |"
        for label, native, gbp, cov in source_rows
    )
    return f"""| Source | Value | ≈ GBP | Cards priced |
|--------|------:|------:|-------------:|
{value_rows}

*💰 Standard (non-foil) cards. Cardmarket/TCGPlayer/tix: Scryfall daily snapshot ({today}); ManaPool: cheapest live listings (LP+ by default), US marketplace, shipping excluded. ≈ GBP is rough — ECB reference rates via frankfurter.dev; 1 tix ≈ $1.*"""


def render_card_tables(report: dict) -> str:
    rates = report["rates"]
    top_rows = "\n".join(
        f"| {name} | {_money(eur, usd)[0]} | {_money(eur, usd)[1]} | {_usd_cell(mp)} | {_gbp_cell(_card_gbp(eur, usd, rates))} |"
        for name, eur, usd, mp in report["top"]
    )
    all_rows = "\n".join(
        f"| {name}{f' ×{qty}' if qty > 1 else ''} | {_money(eur, usd)[0]} | {_money(eur, usd)[1]} | {_usd_cell(mp)} | {_gbp_cell(_card_gbp(eur, usd, rates))} |"
        for qty, name, eur, usd, mp in report["all"]
    )
    unpriced_note = (
        f"\n> ⚠️ No price found for {len(report['unpriced'])} card(s): "
        + ", ".join(report["unpriced"]) if report["unpriced"] else ""
    )
    return f"""## 🏆 Priciest Cards

| Card | EUR | USD | MP $ | ≈ GBP |
|------|----:|----:|-----:|------:|
{top_rows}

### 💵 All Card Prices

Per-card prices (×N marks multiples — basics etc.; the price shown is per copy).

| Card | EUR | USD | MP $ | ≈ GBP |
|------|----:|----:|-----:|------:|
{all_rows}
{unpriced_note}
"""


def render_budget_list(decklist: list[tuple[int, str]], prices: dict[str, dict],
                       report: dict) -> str:
    """Full deck list where every card is shown at its cheapest
    functionally-identical version (any printing/name, incl. ManaPool's
    cheapest live listing). Bulk under €0.50 keeps the deck's own version —
    there's nothing meaningful to save on pennies.
    """
    rates = report["rates"]
    rows = []
    totals = {"eur": 0.0, "mp": 0.0, "best_gbp": 0.0}
    for qty, name in decklist:
        p = prices.get(name.lower()) or {}
        deck_eur, mp = p.get("eur"), p.get("mp")
        label, eur = f"{name}", deck_eur
        worth_checking = (deck_eur or p.get("usd") or 0) > 0.50
        if worth_checking:
            info = card_prints_info(name)
            ch = info.get("cheapest")
            if ch and _sane_cheaper(ch["eur"], deck_eur):
                eur = ch["eur"]
                printed = ch["printed_as"] if ch["printed_as"].lower() != name.lower() \
                    else name
                label = f"{printed} ({ch['set']})"
        gbp_candidates = []
        if rates:
            if eur is not None:
                gbp_candidates.append(eur * rates["eur_gbp"])
            if mp is not None:
                gbp_candidates.append(mp * rates["usd_gbp"])
        best_gbp = min(gbp_candidates) if gbp_candidates else None
        totals["eur"] += (eur or 0) * qty
        totals["mp"] += (mp or 0) * qty
        totals["best_gbp"] += (best_gbp or 0) * qty
        rows.append(
            f"| {label}{f' ×{qty}' if qty > 1 else ''} | "
            f"{_money(eur, None)[0]} | {_usd_cell(mp)} | {_gbp_cell(best_gbp)} |"
        )
    body = "\n".join(rows)
    return f"""## 💸 Cheapest Build

The whole deck with every card at its cheapest functionally-identical version
— other printings and Universes Beyond/plain-name swaps included. EUR is the
cheapest Cardmarket printing, MP $ the cheapest ManaPool listing, ≈ GBP the
cheaper of the two converted. Cards under €0.50 keep the deck's own version.

| Card (cheapest version) | EUR | MP $ | ≈ GBP |
|-------------------------|----:|-----:|------:|
{body}

Whole deck at cheapest versions ≈ **€{totals["eur"]:,.2f} · MP ${totals["mp"]:,.2f} · best mix ≈ {_gbp_cell(totals["best_gbp"] if rates else None)}**.
"""


def price_frontmatter_str(report: dict) -> str:
    totals, rates = report["totals"], report["rates"]
    fm_prices = {"eur": totals["eur"]}
    if rates:
        fm_prices["gbp"] = totals["eur"] * rates["eur_gbp"]
    fm_prices["usd"] = totals["usd"]
    fm_prices["mp"] = totals["mp"]
    fm_prices["tix"] = totals["tix"]
    return "\n".join(f"price-{k}: {v:.2f}" for k, v in fm_prices.items())


def recheck_all(out_dir: Path) -> None:
    """Refresh every deck note's price data and collection comparison using
    the deck list stored in the note itself — no site fetching, no browser.
    Rewrites the value table, price tables, Cards to Buy, and frontmatter;
    review sections and everything else stay untouched.
    """
    collection = load_collection(out_dir)
    if not collection:
        sys.exit(f"No collection file found at {collection_path(out_dir)}")
    collection_name, owned = collection
    today = date.today().isoformat()
    notes = sorted(out_dir.glob("????-??-??_MTG_*.md"))
    if not notes:
        sys.exit(f"No deck notes found in {out_dir}")

    for note in notes:
        text = note.read_text(encoding="utf-8")
        block = re.search(r"## 📜 Deck List\s*```\n(.*?)```", text, re.S)
        if not block:
            print(f"{note.name}: no deck list found — skipped")
            continue
        decklist = []
        for line in block.group(1).splitlines():
            parsed = parse_card_line(line)
            if parsed:
                decklist.append(parsed)
        prices = fetch_prices([n for _, n in decklist])
        report = price_report(decklist, prices)
        buy = buy_report(decklist, owned, prices)
        rates = report["rates"]

        # Deck value table + caption (tolerate Obsidian's table re-padding)
        text = re.sub(r"\| Source\s*\|\s*Value\s*\|.*?\*💰[^\n]*\*",
                      lambda _: render_value_block(report, today), text,
                      count=1, flags=re.S)
        # Priciest / All Card Prices tables
        text = re.sub(r"## 🏆 Priciest Cards\n.*?(?=## 📜 Deck List)",
                      lambda _: render_card_tables(report), text,
                      count=1, flags=re.S)
        # Cards to Buy
        buy_sec = render_buy_section(buy, collection_name, rates)
        if "## 🛒 Cards to Buy" in text:
            text = re.sub(r"## 🛒 Cards to Buy\n.*?(?=## 🏆 Priciest Cards)",
                          lambda _: buy_sec, text, count=1, flags=re.S)
        else:
            text = text.replace("## 🏆 Priciest Cards",
                                buy_sec + "## 🏆 Priciest Cards", 1)
        # Frontmatter: rebuild all price/owned/buy fields, stamp price-date
        text = re.sub(r"^(price-(eur|gbp|usd|tix|mp)|owned|buy-eur|buy-gbp|buy-mp): .*\n",
                      "", text, flags=re.M)
        fm = price_frontmatter_str(report) + "\n" + buy_frontmatter(buy, rates)
        text = text.replace("\nprice-date:", f"\n{fm}\nprice-date:", 1)
        text = re.sub(r"^price-date: .*$", f"price-date: {today}", text,
                      count=1, flags=re.M)
        # Cheapest Build (sits at the very bottom — replace or append)
        budget_section = render_budget_list(decklist, prices, report)
        if "## 💸 Cheapest Build" in text:
            text = re.sub(r"## 💸 Cheapest Build\n.*\Z",
                          lambda _: budget_section, text, count=1, flags=re.S)
        else:
            text = text.rstrip("\n") + "\n\n" + budget_section

        note.write_text(text, encoding="utf-8")
        print(f"{note.name}: value ~EUR {report['totals']['eur']:,.2f}"
              f" — own {buy['owned_unique']}/{buy['unique']}"
              f" — to buy {len(buy['missing'])} (~EUR {buy['totals']['eur']:,.2f})")


def main() -> None:
    argv = sys.argv[1:]
    force = "--force" in argv
    own = "--own" in argv
    recheck = "--recheck" in argv
    argv = [a for a in argv if a not in ("--force", "--own", "--recheck")]
    if recheck:
        if argv:
            sys.exit("--recheck takes no URL/file — it refreshes every note's"
                     " Cards to Buy from the deck lists already in the notes.")
        recheck_all(output_dir())
        return
    if len(argv) != 1:
        sys.exit(__doc__)
    deck_url = argv[0]
    out_dir = output_dir()
    if not out_dir.is_dir():
        sys.exit(f"Vault output folder does not exist: {out_dir}")

    deck = fetch_deck(deck_url)
    if not deck["commanders"]:
        sys.exit("No commander found on this deck — is it a Commander deck?")

    # Imported .txt files are archived to ./imports (gitignored) after a
    # successful run — the note's deck-url points at the archived copy
    txt_src = Path(deck["txt_path"]).resolve() if deck.get("txt_path") else None
    if txt_src:
        deck_url = f"imports/{txt_src.name}"
    mainboard = sorted(deck["mainboard"], key=lambda c: c[1].lower())
    decklist = [(1, name) for name in deck["commanders"]] + mainboard

    primary = deck["commanders"][0]
    safe_name = ILLEGAL_FILENAME_CHARS.sub("", primary)

    # A commander can have several builds (precon + enhanced, etc.). A note is
    # the SAME deck if its deck-url or deck-name matches; same-commander notes
    # for a different build get a " - <deck name>" suffix instead of colliding.
    match = None
    for candidate in sorted(out_dir.glob(f"????-??-??_MTG_{safe_name}*.md")):
        text = candidate.read_text(encoding="utf-8")
        url_m = re.search(r"^deck-url: (.+)$", text, re.M)
        name_m = (re.search(r"^deck-name: (.+)$", text, re.M)
                  or re.search(r"^# 🃏 (.+)$", text, re.M))
        if (url_m and url_m.group(1).strip() == deck_url) or \
           (name_m and name_m.group(1).strip().lower() == deck["name"].lower()):
            match = candidate
            break
    if match and not force:
        sys.exit(f"Note already exists (use --force to overwrite): {match}")
    if match:
        note_path = match
    else:
        others = list(out_dir.glob(f"????-??-??_MTG_{safe_name}*.md"))
        base = safe_name if not others else \
            f"{safe_name} - {ILLEGAL_FILENAME_CHARS.sub('', deck['name'])}"
        note_path = out_dir / f"{date.today().isoformat()}_MTG_{base}.md"
    stem = note_path.stem

    attachments_dir = out_dir / "Attachments"
    attachments_dir.mkdir(exist_ok=True)
    image_path = attachments_dir / f"{stem}.jpg"
    image_url = fetch_commander_art(primary, image_path)

    if own:
        if add_deck_to_collection(out_dir, deck["name"], decklist):
            print(f"Collection: deck added to {collection_path(out_dir).name}")
        else:
            print(f"Collection: already lists '{deck['name']}' — nothing added")

    prices = fetch_prices([name for _, name in decklist])
    report = price_report(decklist, prices)

    collection = load_collection(out_dir)
    collection_name, buy = None, None
    if collection:
        collection_name, owned = collection
        buy = buy_report(decklist, owned, prices)

    reviews = extract_reviews(note_path.read_text(encoding="utf-8")) \
        if note_path.exists() else {}
    if reviews:
        print(f"Preserved: your written content in {len(reviews)} review section(s)")

    budget_section = render_budget_list(decklist, prices, report)
    note_path.write_text(
        build_note(deck, decklist, image_url, deck_url, report, buy,
                   collection_name, reviews, budget_section),
        encoding="utf-8",
    )

    if txt_src:
        imports_dir = SCRIPT_DIR / "imports"
        imports_dir.mkdir(exist_ok=True)
        dest = imports_dir / txt_src.name
        if txt_src != dest.resolve():
            txt_src.replace(dest)
            print(f"Archived:  {dest}")

    total = sum(qty for qty, _ in decklist)
    totals = report["totals"]
    print(f"Deck:      {deck['name']}")
    print(f"Commander: {', '.join(deck['commanders'])}")
    print(f"Cards:     {total} ({len(decklist)} unique)")
    rates = report["rates"]
    gbp = f" / GBP {totals['eur'] * rates['eur_gbp']:,.2f}" if rates else ""
    print(f"Value:     ~EUR {totals['eur']:,.2f}{gbp} / USD {totals['usd']:,.2f}"
          f" / TIX {totals['tix']:,.2f}"
          + (f"  ({len(report['unpriced'])} unpriced)" if report["unpriced"] else ""))
    if buy is not None:
        print(f"Owned:     {buy['owned_unique']}/{buy['unique']} cards"
              f" — to buy: {len(buy['missing'])} (~EUR {buy['totals']['eur']:,.2f})")
    else:
        print("Owned:     no _Collection.md found — skipped the Cards to Buy section")
    print(f"Note:      {note_path}")
    print(f"Artwork:   {image_path}")


if __name__ == "__main__":
    main()
