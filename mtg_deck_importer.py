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

Every note carries a stable deck-id (shown by `--list`) so a single deck can
be targeted by number.

`python mtg_deck_importer.py --recheck` (no ID) refreshes every note by
re-importing it from its original source — a Moxfield/EDHREC URL or the
archived .txt in the vault's imports/ folder — so deck edits, fresh
prices and fresh art all
land, plus the Cards to Complete comparisons against the current _Collection.md. A
deck whose source can't be reached falls back to a price/buy refresh from the
list stored in its note. Review sections are always preserved. `--recheck
<id>` does the same for a single deck (see --list for ids).

`python mtg_deck_importer.py --reimport` refreshes deck lists and card art
without fetching new prices — for every note, or one with `--reimport <id>`.
It re-fetches each list from its source (URL or archived .txt), falling back
to the list stored in the note, re-downloads the commander art, and rebuilds
the card gallery. Price, buy and Cheapest Build sections are left untouched.

`python mtg_deck_importer.py --list` prints every deck's id and name.

Fetches the deck list (Moxfield via a headed browser because of Cloudflare;
EDHREC via plain HTTP), downloads the commander's artwork from Scryfall, and
writes a markdown deck note into the Obsidian vault folder set by the
VAULT_OUTPUT_DIR environment variable (usually via a .env file next to this
script — see .env.example).

Output note:  YYYY-MM-DD_MTG_<Commander Name>.md
Output image: Attachments/YYYY-MM-DD_MTG_<Commander Name>.jpg
"""

import argparse
import atexit
import json
import os
import re
import shutil
import sys
import time
from datetime import date
from functools import lru_cache
from pathlib import Path, PureWindowsPath

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).parent

USER_AGENT = "mtg-decklist-md/1.0 (personal Obsidian tool)"
HTTP_HEADERS = {"User-Agent": USER_AGENT, "Accept": "*/*"}

# One Session for the whole run so the hundreds of sequential API calls reuse
# a single keep-alive connection instead of a fresh TLS handshake each time.
SESSION = requests.Session()
SESSION.headers.update(HTTP_HEADERS)

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
_prints_fetched = 0
PRINTS_CACHE = SCRIPT_DIR / ".cache" / "scryfall_prints.json"
PRINTS_TTL = 72 * 3600  # prices drift slowly; 3 days is fine for budget hints


def _load_prints_cache() -> None:
    global _prints_cache_loaded
    _prints_cache_loaded = True
    try:
        raw = json.loads(PRINTS_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return  # missing or corrupt cache — just start empty
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
    except OSError:
        pass


atexit.register(_save_prints_cache)


def scryfall_card_image(card: dict, size: str = "small") -> str | None:
    """Image URL for a Scryfall card object, handling double-faced cards
    (image lives per face). 'small' is 146px wide — the right size for a
    gallery grid and light to load remotely.
    """
    uris = card.get("image_uris")
    if not uris:
        faces = card.get("card_faces") or []
        uris = faces[0].get("image_uris") if faces else None
    return uris.get(size) if uris else None


def card_prints_info(name: str, canonical: str | None = None) -> dict:
    """One prints lookup per card, reused for two jobs: (a) every name the
    card can appear under — canonical plus Universes Beyond flavor names
    (the Marvel precons print Spark Double as "Loki's Double"); (b) the
    cheapest paper printing, since a flavor-named skin is the same card and
    the plain version is often cheaper.

    Pass `canonical` when the exact Scryfall name is already known (the bulk
    price fetch returns it) — that skips the name-resolution request and
    halves the Scryfall traffic, which is what the rate limit actually bites.
    """
    if not _prints_cache_loaded:
        _load_prints_cache()
    key = name.lower()
    if key in _card_info_cache:
        return _card_info_cache[key]
    global _prints_cache_dirty, _prints_fetched
    _prints_cache_dirty = True
    _prints_fetched += 1
    if _prints_fetched == 1:
        print("Scryfall:  looking up printings (~0.5s per new card, cached 3 days)...")
    elif _prints_fetched % 10 == 0:
        print(f"Scryfall:  ...{_prints_fetched} cards looked up")
    info = {"aliases": {key}, "canonical": name, "cheapest": None,
            "ts": time.time()}
    if canonical is None:
        r = http("GET", SCRYFALL_NAMED, params={"exact": name})
        if r.status_code == 200:
            canonical = r.json()["name"]
        elif r.status_code != 404:  # throttled/5xx — don't cache the failure
            info["ts"] = 0
    if canonical is not None:
        info["canonical"] = canonical
        info["aliases"].add(canonical.lower())
        # Double-faced cards: let a front-face-only name in the collection
        # ("Malakir Rebirth") match the full "A // B" deck name
        if "//" in canonical:
            info["aliases"].add(canonical.split("//")[0].strip().lower())
        s = http("GET", SCRYFALL_SEARCH,
                 params={"q": f'!"{canonical}" game:paper',
                         "unique": "prints"})
        if s.status_code == 200:
            best = None
            for c in s.json().get("data", []):
                if c.get("flavor_name"):
                    info["aliases"].add(c["flavor_name"].lower())
                eur = float(c["prices"]["eur"]) if c["prices"].get("eur") else None
                usd = float(c["prices"]["usd"]) if c["prices"].get("usd") else None
                if eur is None and usd is None:
                    continue
                # EUR-first ranking: we buy on Cardmarket, so prefer a printing
                # that HAS a EUR price even if a USD-only one is nominally lower
                rank = (eur if eur is not None else float("inf"),
                        usd if usd is not None else float("inf"))
                if best is None or rank < best[0]:
                    best = (rank, {"eur": eur, "usd": usd, "set": c["set_name"],
                                   "printed_as": c.get("flavor_name") or c["name"],
                                   "img": scryfall_card_image(c)})
            if best:
                info["cheapest"] = best[1]
        elif s.status_code != 404:  # throttled/5xx — retry it next run instead
            info["ts"] = 0
    _card_info_cache[key] = info
    # Long runs can be interrupted (Ctrl-C, crashes) — checkpoint the cache
    # periodically so hundreds of slow lookups are never lost with the process
    if _prints_fetched % 50 == 0:
        _save_prints_cache()
    return info


def buy_report(decklist: list[tuple[int, str]], owned: dict[str, int],
               prices: dict[str, dict]) -> dict:
    """Which deck cards are missing from the collection, and what the gap
    costs (per-source totals use the already-fetched deck prices).
    """
    missing = []
    owned_rows = []  # fully-covered cards, shown as ✅ in the shopping table
    totals = {"eur": 0.0, "usd": 0.0, "mp": 0.0}
    unpriced = 0
    owned_unique = owned_copies = total_copies = 0
    for qty, name in decklist:
        total_copies += qty
        have = owned.get(name.lower(), 0)
        info = None
        if have < qty:
            # Not obviously owned — check flavor-name aliases before giving up
            info = card_prints_info(name,
                                    (prices.get(name.lower()) or {}).get("name"))
            have = sum(owned.get(a, 0) for a in info["aliases"])
        have = min(have, qty)
        owned_copies += have
        if have >= qty:
            owned_unique += 1
            owned_rows.append((qty, name))
            continue
        need = qty - have
        p = prices.get(name.lower()) or {}
        if p.get("eur") is None and p.get("usd") is None:
            unpriced += 1
        totals["eur"] += (p.get("eur") or 0) * need
        totals["usd"] += (p.get("usd") or 0) * need
        totals["mp"] += (p.get("mp") or 0) * need
        missing.append({"need": need, "have": have, "name": name,
                        "eur": p.get("eur"), "usd": p.get("usd"),
                        "mp": p.get("mp"), "info": info})
    missing.sort(key=lambda c: c["eur"] or 0, reverse=True)
    return {"missing": missing, "owned_rows": owned_rows, "totals": totals,
            "unpriced": unpriced,
            "owned_unique": owned_unique, "owned_copies": owned_copies,
            "unique": len(decklist), "total_copies": total_copies}


_last_scryfall_call = 0.0
SCRYFALL_MIN_INTERVAL = 0.25  # Scryfall documents ~10 req/s; stay well under
_scryfall_penalty = 0.0  # extra per-call gap, grown each time Scryfall 429s us


def http(method: str, url: str, **kwargs) -> requests.Response:
    """Request with polite backoff on 429/5xx and automatic pacing of Scryfall
    calls — one place enforces the rate limit so no call site can forget. A
    429 from Scryfall permanently widens this run's gap between calls (up to
    1s): trading a little steady-state speed beats eating 60s penalty waits.
    """
    global _last_scryfall_call, _scryfall_penalty
    throttle = "api.scryfall.com" in url
    r = None
    for attempt in range(5):
        if throttle:
            gap = time.time() - _last_scryfall_call
            min_gap = SCRYFALL_MIN_INTERVAL + _scryfall_penalty
            if gap < min_gap:
                time.sleep(min_gap - gap)
        r = SESSION.request(method, url, timeout=30, **kwargs)
        if throttle:
            _last_scryfall_call = time.time()
        if r.status_code == 429 or r.status_code >= 500:
            if throttle and r.status_code == 429:
                _scryfall_penalty = min(1.0, _scryfall_penalty + 0.25)
            host = url.split("/")[2]
            wait = float(r.headers.get("Retry-After", 2)) + attempt
            print(f"Throttled: {host} asked us to slow down — waiting {wait:.0f}s")
            time.sleep(wait)
            continue
        return r
    assert r is not None  # range(5) always runs, so r was assigned at least once
    return r


@lru_cache(maxsize=1)
def manapool_index() -> dict[str, float]:
    """Card name -> cheapest non-foil ManaPool listing in USD, at the
    condition set by MANAPOOL_CONDITION in .env (any | lp | nm; default lp).
    ManaPool's full catalog (~50 MB) is cached next to the script and only
    re-downloaded when the cache is older than 24 hours — repeat runs in the
    same day read the local file. Prices are live lowest listings on a US
    marketplace (shipping not included). Result is memoised for the run.
    """
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
            data = json.loads(r.content)["data"]  # already in memory — no re-read
        else:
            data = json.loads(MANAPOOL_CACHE.read_text(encoding="utf-8"))["data"]
    except (OSError, ValueError, KeyError, requests.RequestException) as exc:
        print(f"ManaPool:  unavailable ({exc}) — MP prices skipped this run")
        return {}
    index: dict[str, float] = {}
    for card in data:
        cents = card.get(field)
        if not cents:
            continue
        name = card["name"].lower()
        usd = cents / 100
        if name not in index or usd < index[name]:
            index[name] = usd
    return index


def _env_path(value: str) -> Path:
    """A path from .env: expand ~, and forgive shell-style '\\ ' escapes —
    .env values are taken literally, but a path pasted from a terminal often
    carries them (a real Windows path never contains backslash-space).
    """
    return Path(value.replace("\\ ", " ")).expanduser()


def collection_path(out_dir: Path) -> Path:
    custom = os.environ.get("COLLECTION_FILE")
    return _env_path(custom) if custom else out_dir / "_Collection.md"


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
    return _env_path(value)


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

    m = re.search(r"moxfield\.com/decks/([A-Za-z0-9_-]+)", url)
    if not m:
        sys.exit(f"Could not read a Moxfield deck id from: {url}")
    deck_id = m.group(1)
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
    r = http("GET", url)
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
                "name": card["name"],  # canonical spelling, saves a lookup later
                "eur": float(p["eur"]) if p.get("eur") else None,
                "usd": float(p["usd"]) if p.get("usd") else None,
                "tix": float(p["tix"]) if p.get("tix") else None,
                "mp": manapool_index().get(card["name"].lower()),
                "img": scryfall_card_image(card),
            }
            prices[card["name"].lower()] = entry
            # Let a front-face name find its double-faced card
            if "//" in card["name"]:
                prices.setdefault(card["name"].split("//")[0].strip().lower(), entry)

    # The collection endpoint returns one arbitrary printing per card, which
    # can be an online-only set with no paper prices (e.g. Tempest Remastered
    # Mox Diamond). For those, fall back to the cheapest paper printing — reuse
    # the disk-cached prints lookup so we never fetch the same search twice.
    for name in names:
        entry = prices.get(name.lower())
        if entry and (entry["eur"] is not None or entry["usd"] is not None):
            continue
        info = card_prints_info(name, (entry or {}).get("name"))
        cheap = info.get("cheapest")
        if cheap:
            prices[name.lower()] = {
                "name": info["canonical"],
                "eur": cheap.get("eur"),
                "usd": cheap.get("usd"),
                "tix": entry["tix"] if entry else None,
                "mp": manapool_index().get(name.lower()),
                "img": cheap.get("img") or (entry or {}).get("img"),
            }
    return prices


def fetch_card_images(names: list[str]) -> dict[str, str | None]:
    """Card image URLs only, via Scryfall's collection endpoint. Used by
    --reimport to (re)build galleries without pulling fresh market prices —
    the response carries prices too, but they are ignored here.
    """
    imgs: dict[str, str | None] = {}
    for i in range(0, len(names), 75):  # collection endpoint caps at 75 cards
        chunk = names[i:i + 75]
        r = http("POST", SCRYFALL_COLLECTION,
                 json={"identifiers": [{"name": n} for n in chunk]})
        r.raise_for_status()
        for card in r.json().get("data", []):
            img = scryfall_card_image(card)
            imgs[card["name"].lower()] = img
            if "//" in card["name"]:
                imgs.setdefault(card["name"].split("//")[0].strip().lower(), img)
    return imgs


@lru_cache(maxsize=1)
def fx_rates() -> dict | None:
    """Current ECB reference rates via frankfurter.dev: EUR→GBP and USD→GBP
    (derived through EUR). None if the rate API is unreachable. Memoised so a
    --recheck over many notes fetches the rate once.
    """
    try:
        r = SESSION.get(ECB_RATES, params={"base": "EUR", "symbols": "GBP,USD"},
                        timeout=15)
        r.raise_for_status()
        rates = r.json()["rates"]
        return {"eur_gbp": float(rates["GBP"]),
                "usd_gbp": float(rates["GBP"]) / float(rates["USD"])}
    except (requests.RequestException, ValueError, KeyError, ZeroDivisionError):
        return None


def price_report(decklist: list[tuple[int, str]], prices: dict[str, dict]) -> dict:
    totals = {"eur": 0.0, "usd": 0.0, "tix": 0.0, "mp": 0.0}
    coverage = {"eur": 0, "usd": 0, "tix": 0, "mp": 0}
    unpriced = []
    all_cards = []
    for qty, name in decklist:
        p = prices.get(name.lower())
        img = (p or {}).get("img")
        if not p or (p["eur"] is None and p["usd"] is None):
            unpriced.append(name)
            all_cards.append((qty, name, None, None, None, img))
            continue
        for src in ("eur", "usd", "tix", "mp"):
            if p.get(src) is not None:
                totals[src] += p[src] * qty
                coverage[src] += 1
        all_cards.append((qty, name, p["eur"], p["usd"], p.get("mp"), img))
    all_cards.sort(key=lambda c: c[2] or 0, reverse=True)
    return {"totals": totals, "coverage": coverage, "unique": len(decklist),
            "all": all_cards, "unpriced": unpriced,
            "rates": fx_rates()}


REVIEW_SECTIONS = ["🧠 First Impressions", "💪 Strengths", "⚠️ Weaknesses",
                   "🔄 Cards to Consider Swapping", "📝 Play Notes"]


def _eur_cell(amount):
    return f"€{amount:,.2f}" if amount is not None else "—"


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


def _callout(title: str, body: str, kind: str = "note") -> str:
    """An Obsidian collapsed callout (`> [!note]- Title`). Other GFM viewers
    render it as a plain blockquote, which still reads fine.
    """
    quoted = "\n".join(f"> {ln}" if ln else ">" for ln in body.splitlines())
    return f"> [!{kind}]- {title}\n>\n{quoted}"


def budget_choices(decklist: list[tuple[int, str]],
                   prices: dict[str, dict]) -> dict[str, dict]:
    """For every deck card, the cheapest functionally-identical version to
    buy: {lowercased deck name: {printed, set_name, set_code, eur, mp, img,
    deck_eur, changed}}. Cards under €0.50 keep the deck's own version (there
    is nothing meaningful to save on pennies) — the single source of truth
    used by the Shopping List, Cheaper Printings and Cheapest Build sections,
    so their totals always agree.
    """
    choices: dict[str, dict] = {}
    for _qty, name in decklist:
        p = prices.get(name.lower()) or {}
        deck_eur = p.get("eur")
        c = {"printed": name, "set_name": None, "set_code": None,
             "eur": deck_eur, "mp": p.get("mp"), "img": p.get("img"),
             "deck_eur": deck_eur, "changed": False}
        if (deck_eur or p.get("usd") or 0) > 0.50:
            ch = card_prints_info(name, p.get("name")).get("cheapest")
            if ch and _sane_cheaper(ch["eur"], deck_eur):
                printed = ch["printed_as"] \
                    if ch["printed_as"].lower() != name.lower() else name
                c.update(printed=printed, set_name=ch["set"],
                         set_code=set_code_map().get(ch["set"].lower()),
                         eur=ch["eur"], img=ch.get("img") or c["img"],
                         changed=True)
        choices[name.lower()] = c
    return choices


def _choice_line(qty: int, choice: dict | None, name: str) -> str:
    """A copy-paste decklist line for a chosen version — `(SET)` pins the
    exact printing (Moxfield's import format) when a code is known.
    """
    if not choice:
        return f"{qty} {name}"
    code = choice.get("set_code")
    return f"{qty} {choice['printed']} ({code.upper()})" if code \
        else f"{qty} {choice['printed']}"


def cheapest_buy(buy: dict, choices: dict[str, dict],
                 rates: dict | None) -> dict:
    """The missing cards priced at their cheapest versions: totals plus the
    copy-paste Budget Buy List lines (alphabetical, like a store checklist).
    """
    totals = {"eur": 0.0, "mp": 0.0, "best_gbp": 0.0}
    lines = []
    for m in sorted(buy["missing"], key=lambda m: m["name"].lower()):
        c = choices.get(m["name"].lower())
        eur = c["eur"] if c else m["eur"]
        mp = c["mp"] if c else m["mp"]
        gbp_candidates = []
        if rates:
            if eur is not None:
                gbp_candidates.append(eur * rates["eur_gbp"])
            if mp is not None:
                gbp_candidates.append(mp * rates["usd_gbp"])
        totals["eur"] += (eur or 0) * m["need"]
        totals["mp"] += (mp or 0) * m["need"]
        totals["best_gbp"] += (min(gbp_candidates) if gbp_candidates else 0) * m["need"]
        lines.append(_choice_line(m["need"], c, m["name"]))
    return {"totals": totals, "lines": lines}


def buy_frontmatter(buy: dict, rates: dict | None, cheap: dict | None) -> str:
    lines = [f"owned: {buy['owned_unique']}/{buy['unique']}",
             f"buy-eur: {buy['totals']['eur']:.2f}",
             f"buy-mp: {buy['totals']['mp']:.2f}"]
    if cheap:
        lines.append(f"buy-cheapest-eur: {cheap['totals']['eur']:.2f}")
    if rates:
        lines.append(f"buy-gbp: {buy['totals']['eur'] * rates['eur_gbp']:.2f}")
        if cheap:
            lines.append(
                f"buy-cheapest-gbp: {cheap['totals']['eur'] * rates['eur_gbp']:.2f}")
    return "\n".join(lines)


def _owned_cell(qty: int) -> str:
    return "✅ own" if qty == 1 else f"✅ ×{qty}"


def _buy_cell(m: dict) -> str:
    cell = f"🛒 {m['need']}"
    return f"{cell} (have {m['have']})" if m["have"] else cell


def render_buy_section(buy: dict, collection_name: str, rates: dict | None,
                       cheap: dict | None) -> str:
    """'Cards to Complete the Deck' — what the collection is missing, at the
    deck's own versions: one table with 🛒 rows (missing, dearest first) and
    ✅ rows (owned, off the totals), plus a copy-paste Buy List.
    """
    summary = (f"Compared against `{collection_name}` — you own "
               f"**{buy['owned_unique']}/{buy['unique']}** cards "
               f"({buy['owned_copies']}/{buy['total_copies']} copies).")
    if not buy["missing"]:
        return f"""## 🛒 Cards to Complete the Deck

{summary}
🎉 **You own every card in this deck — nothing to buy!**"""
    buy_gbp = _gbp_cell(buy["totals"]["eur"] * rates["eur_gbp"] if rates else None)
    cheap_hint = ""
    if cheap and cheap["totals"]["eur"] < buy["totals"]["eur"] - 0.005:
        cheap_hint = (f" — or ≈ **€{cheap['totals']['eur']:,.2f}** "
                      "at the cheapest versions (next section after the "
                      "💸 Cheapest Build)")
    unpriced_note = (
        f"\n\n> ⚠️ {buy['unpriced']} missing card(s) have no price and are not "
        "in the totals." if buy["unpriced"] else "")
    rows = [
        f"| {m['name']} | {_buy_cell(m)} | {_eur_cell(m['eur'])} | "
        f"{_usd_cell(m['usd'])} | {_usd_cell(m['mp'])} | "
        f"{_gbp_cell(_card_gbp(m['eur'], m['usd'], rates))} |"
        for m in buy["missing"]
    ] + [
        f"| {name} | {_owned_cell(qty)} | — | — | — | — |"
        for qty, name in sorted(buy["owned_rows"], key=lambda r: r[1].lower())
    ]
    table = "\n".join(rows)
    listing = "\n".join(
        f"{m['need']} {m['name']}"
        for m in sorted(buy["missing"], key=lambda m: m["name"].lower()))
    return f"""## 🛒 Cards to Complete the Deck

{summary}
Buy the **{len(buy['missing'])}** missing card(s) ≈ **€{buy["totals"]["eur"]:,.2f} · ${buy["totals"]["usd"]:,.2f} · MP ${buy["totals"]["mp"]:,.2f} · {buy_gbp}** at the deck's own versions{cheap_hint}. Prices are per copy; **✅ = pull it from your collection**, its price is off the totals.{unpriced_note}

| Card | Buy | EUR | USD | MP $ | ≈ GBP |
|------|:----|----:|----:|-----:|------:|
{table}

### 📋 Buy List (copy-paste)

Just the missing cards, any printing:

```
{listing}
```"""


def render_cheap_buy_section(buy: dict, cheap: dict,
                             choices: dict[str, dict],
                             rates: dict | None) -> str:
    """'Cards to Complete — Cheapest Build': the same missing cards at their
    cheapest functionally-identical versions, with per-card savings and a
    copy-paste Budget Buy List that pins printings with `(SET)` codes.
    """
    if not buy["missing"]:
        return ""
    saved = buy["totals"]["eur"] - cheap["totals"]["eur"]
    cheap_gbp = _gbp_cell(cheap["totals"]["best_gbp"] if rates else None)
    rows = []
    for m in buy["missing"]:
        c = choices.get(m["name"].lower())
        if c and c["changed"]:
            label = f"{c['printed']} ({c['set_name']})"
            eur, save = c["eur"], (m["eur"] or 0) - (c["eur"] or 0)
        else:
            label, eur, save = m["name"], m["eur"], None
        mp = c["mp"] if c else m["mp"]
        rows.append(
            f"| {label} | {_buy_cell(m)} | {_eur_cell(eur)} | {_usd_cell(mp)} | "
            f"{_gbp_cell(_card_gbp(eur, None, rates))} | "
            f"{f'€{save:,.2f}' if save else '—'} |")
    table = "\n".join(rows)
    listing = "\n".join(cheap["lines"])
    return f"""## 🛒 Cards to Complete — Cheapest Build

The same missing cards at their cheapest versions ≈ **€{cheap["totals"]["eur"]:,.2f} · MP ${cheap["totals"]["mp"]:,.2f} · best mix ≈ {cheap_gbp}** — saves **€{saved:,.2f}** over the deck's own versions. Universes Beyond skins are only art and printed-name swaps, so the plain version is functionally identical (and vice versa).

| Card (cheapest version) | Buy | EUR | MP $ | ≈ GBP | Save |
|-------------------------|:----|----:|-----:|------:|-----:|
{table}

### 📋 Budget Buy List (copy-paste)

The missing cards at their cheapest versions — `(SET)` pins the exact printing
(Moxfield understands this format); lines without a code use any printing.

```
{listing}
```"""


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
               reviews: dict[str, str], choices: dict[str, dict],
               deck_id: int) -> str:
    """The whole note. Reading order after the reviews: card prices &
    gallery, the deck list, what to buy to complete it, the Cheapest Build,
    and what to buy to complete that.
    """
    today = date.today().isoformat()
    commander_line = ", ".join(deck["commanders"])
    listing = "\n".join(f"{qty} {name}" for qty, name in decklist)
    rates = report["rates"]

    cheap = cheapest_buy(buy, choices, rates) if buy else None
    price_frontmatter = price_frontmatter_str(report)
    # buy and collection_name always arrive together (see import_deck)
    if buy is not None and collection_name is not None:
        price_frontmatter += "\n" + buy_frontmatter(buy, rates, cheap)
        buy_section = "\n\n" + render_buy_section(buy, collection_name, rates,
                                                  cheap)
        cheap_buy_section = render_cheap_buy_section(buy, cheap, choices, rates)
        cheap_buy_section = "\n\n" + cheap_buy_section if cheap_buy_section else ""
    else:
        buy_section = cheap_buy_section = ""
    review_block = "\n\n".join(
        f"## {heading}\n\n{reviews.get(heading, '-')}" for heading in REVIEW_SECTIONS
    )

    return f"""---
tags: [mtg, deck, commander]
created: {today}
commander: {commander_line}
deck-name: {deck["name"]}
deck-url: {deck_url}
deck-id: {deck_id}
{price_frontmatter}
price-date: {today}
---

# 🃏 {deck["name"]}

**Commander:** {commander_line}
**Format:** {deck["format"]}
**Source:** {deck["source_md"]}

{render_value_block(report, today, buy, cheap)}

![{commander_line}|290]({image_url})

{review_block}

{render_card_tables(report)}
## 📜 Deck List

```
{listing}
```{buy_section}

{render_budget_list(decklist, report, choices, cheap)}{cheap_buy_section}
"""


def render_value_block(report: dict, today: str, buy: dict | None = None,
                       cheap: dict | None = None) -> str:
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
    finish_line = ""
    if buy is not None:
        if buy["missing"]:
            t = buy["totals"]
            gbp = _gbp_cell(t["eur"] * rates["eur_gbp"] if rates else None)
            cheap_bit = ""
            if cheap and cheap["totals"]["eur"] < t["eur"] - 0.005:
                cheap_bit = (f" — or ≈ **€{cheap['totals']['eur']:,.2f}** at "
                             "the cheapest versions")
            finish_line = (
                f"\n🛒 **Your cost to finish** (own {buy['owned_unique']}/"
                f"{buy['unique']}): ≈ **€{t['eur']:,.2f} · ${t['usd']:,.2f} · "
                f"MP ${t['mp']:,.2f} · {gbp}**{cheap_bit} — see 🛒 Cards to "
                "Complete below.\n")
        else:
            finish_line = ("\n🎉 **You own every card in this deck** — "
                           "nothing to buy.\n")
    return f"""| Source | Value | ≈ GBP | Cards priced |
|--------|------:|------:|-------------:|
{value_rows}
{finish_line}
*💰 Standard (non-foil) cards. Cardmarket/TCGPlayer/tix: Scryfall daily snapshot ({today}); ManaPool: cheapest live listings (LP+ by default), US marketplace, shipping excluded. ≈ GBP is rough — ECB reference rates via frankfurter.dev; 1 tix ≈ $1.*"""


GALLERY_COLUMNS = 4
GALLERY_IMG_WIDTH = 146  # matches Scryfall's "small" image so it renders crisp


def render_gallery(cells: list[tuple[str | None, str]],
                   columns: int = GALLERY_COLUMNS) -> str:
    """A grid of card images as a markdown table so the whole deck is visible
    at a glance. `cells` is (image_url, caption); a missing image shows just
    the caption. HTML <img> tags render in Obsidian and any GFM viewer.
    """
    if not cells:
        return "_No cards to show._"

    def cell(img: str | None, caption: str) -> str:
        caption = caption.replace("|", "\\|")
        if img:
            return (f'<img src="{img}" width="{GALLERY_IMG_WIDTH}" '
                    f'alt="{caption}"><br>{caption}')
        return caption

    lines = ["| " + " | ".join([""] * columns) + " |",
             "|" + "|".join([":--:"] * columns) + "|"]
    for i in range(0, len(cells), columns):
        row = list(cells[i:i + columns])
        row += [(None, "")] * (columns - len(row))  # pad the last row
        lines.append("| " + " | ".join(cell(img, cap) for img, cap in row) + " |")
    return "\n".join(lines)


def render_card_tables(report: dict) -> str:
    rates = report["rates"]
    all_rows = "\n".join(
        f"| {name}{f' ×{qty}' if qty > 1 else ''} | {_eur_cell(eur)} | {_usd_cell(usd)} | {_usd_cell(mp)} | {_gbp_cell(_card_gbp(eur, usd, rates))} |"
        for qty, name, eur, usd, mp, _img in report["all"]
    )
    unpriced_note = (
        f"\n\n> ⚠️ No price found for {len(report['unpriced'])} card(s): "
        + ", ".join(report["unpriced"]) if report["unpriced"] else ""
    )
    gallery_cells = [
        (img, f"{name}{f' ×{qty}' if qty > 1 else ''}")
        for qty, name, _eur, _usd, _mp, img in report["all"]
    ]
    prices_body = f"""Every card, dearest first (×N marks multiples — basics etc.; the price shown is per copy).

| Card | EUR | USD | MP $ | ≈ GBP |
|------|----:|----:|-----:|------:|
{all_rows}"""
    return f"""## 💰 Card Prices

{_callout(f"💵 All Card Prices ({report['unique']})", prices_body)}{unpriced_note}

{render_card_gallery(gallery_cells)}
"""


def render_card_gallery(cells: list[tuple[str | None, str]]) -> str:
    """The 'Card Gallery' subsection — a grid of every card at its current
    printing. Shared by the full render and by --reimport, which rebuilds
    just this block from fresh images without touching prices.
    """
    return f"""### 🖼️ Card Gallery

Every card in the deck at its current printing.

{render_gallery(cells)}"""


SETS_CACHE = SCRIPT_DIR / ".cache" / "scryfall_sets.json"


@lru_cache(maxsize=1)
def set_code_map() -> dict[str, str]:
    """Set name -> set code (e.g. 'Commander 2021' -> 'c21'), cached weekly."""
    try:
        stale = (not SETS_CACHE.is_file()
                 or time.time() - SETS_CACHE.stat().st_mtime > 7 * 86400)
        if stale:
            r = http("GET", "https://api.scryfall.com/sets")
            r.raise_for_status()
            SETS_CACHE.parent.mkdir(exist_ok=True)
            SETS_CACHE.write_text(
                json.dumps({s["name"]: s["code"] for s in r.json()["data"]}),
                encoding="utf-8")
        return {k.lower(): v for k, v in
                json.loads(SETS_CACHE.read_text(encoding="utf-8")).items()}
    except (OSError, ValueError, KeyError, requests.RequestException):
        return {}


def render_budget_list(decklist: list[tuple[int, str]], report: dict,
                       choices: dict[str, dict],
                       cheap: dict | None) -> str:
    """Full deck list where every card is shown at its cheapest
    functionally-identical version (any printing/name, incl. ManaPool's
    cheapest live listing), with the detail tables collapsed into callouts.
    """
    rates = report["rates"]
    rows = []
    list_lines = []
    gallery_cells = []
    totals = {"eur": 0.0, "mp": 0.0, "best_gbp": 0.0}
    for qty, name in decklist:
        c = choices.get(name.lower()) or {}
        eur, mp, img = c.get("eur"), c.get("mp"), c.get("img")
        label = f"{c['printed']} ({c['set_name']})" if c.get("changed") else name
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
            f"{_eur_cell(eur)} | {_usd_cell(mp)} | {_gbp_cell(best_gbp)} |"
        )
        list_lines.append(_choice_line(qty, c or None, name))
        gallery_cells.append((img, f"{label}{f' ×{qty}' if qty > 1 else ''}"))
    body = "\n".join(rows)
    listing = "\n".join(list_lines)
    prices_body = f"""| Card (cheapest version) | EUR | MP $ | ≈ GBP |
|-------------------------|----:|-----:|------:|
{body}"""
    listing_body = f"""`(SET)` pins the exact printing (Moxfield understands
this format); lines without a code use any printing.

```
{listing}
```"""
    gallery_body = f"""Each card at the cheapest version chosen above.

{render_gallery(gallery_cells)}"""
    missing_line = ""
    if cheap and cheap["lines"]:
        missing_line = (
            f"\n🛒 Missing cards only ≈ **€{cheap['totals']['eur']:,.2f} · "
            f"MP ${cheap['totals']['mp']:,.2f} · "
            f"{_gbp_cell(cheap['totals']['best_gbp'] if rates else None)}** — "
            "see 🛒 Cards to Complete — Cheapest Build below.\n")
    return f"""## 💸 Cheapest Build

The whole deck with every card at its cheapest functionally-identical version
— other printings and Universes Beyond/plain-name swaps included. EUR is the
cheapest Cardmarket printing, MP $ the cheapest ManaPool listing, ≈ GBP the
cheaper of the two converted. Cards under €0.50 keep the deck's own version.

Whole deck at cheapest versions ≈ **€{totals["eur"]:,.2f} · MP ${totals["mp"]:,.2f} · best mix ≈ {_gbp_cell(totals["best_gbp"] if rates else None)}**.
{missing_line}
{_callout("💸 Cheapest-version prices (per card)", prices_body)}

{_callout("📋 Cheapest Build List (copy-paste)", listing_body)}

{_callout("🖼️ Cheapest Version Gallery", gallery_body)}"""


def price_frontmatter_str(report: dict) -> str:
    totals, rates = report["totals"], report["rates"]
    fm_prices = {"eur": totals["eur"]}
    if rates:
        fm_prices["gbp"] = totals["eur"] * rates["eur_gbp"]
    fm_prices["usd"] = totals["usd"]
    fm_prices["mp"] = totals["mp"]
    fm_prices["tix"] = totals["tix"]
    return "\n".join(f"price-{k}: {v:.2f}" for k, v in fm_prices.items())


DECK_ID_RE = re.compile(r"^deck-id: (\d+)$", re.M)


def read_deck_id(text: str) -> int | None:
    m = DECK_ID_RE.search(text)
    return int(m.group(1)) if m else None


def _insert_deck_id(text: str, did: int) -> str:
    """Add (or correct) the deck-id line in a note's frontmatter, right after
    deck-url so it sits with the other identity fields.
    """
    if DECK_ID_RE.search(text):
        return DECK_ID_RE.sub(f"deck-id: {did}", text, count=1)
    return re.sub(r"^(deck-url: .*)$", lambda m: f"{m.group(1)}\ndeck-id: {did}",
                  text, count=1, flags=re.M)


def deck_id_map(out_dir: Path) -> dict[int, Path]:
    """{deck-id: note path} for every deck note, assigning a fresh id to any
    note that lacks one (written into its frontmatter). Ids are sequential and
    start one past the highest already in use, so existing ids never shift.
    """
    notes = sorted(out_dir.glob("????-??-??_MTG_*.md"))
    ids: dict[int, Path] = {}
    missing: list[Path] = []
    for note in notes:
        did = read_deck_id(note.read_text(encoding="utf-8"))
        if did is None:
            missing.append(note)
        else:
            ids[did] = note
    nxt = (max(ids) + 1) if ids else 1
    for note in missing:
        text = _insert_deck_id(note.read_text(encoding="utf-8"), nxt)
        note.write_text(text, encoding="utf-8")
        ids[nxt] = note
        nxt += 1
    return ids


def next_deck_id(out_dir: Path) -> int:
    """The id to give a brand-new note: one past the highest already assigned.
    Does not touch existing notes (that is deck_id_map's job).
    """
    highest = 0
    for note in out_dir.glob("????-??-??_MTG_*.md"):
        did = read_deck_id(note.read_text(encoding="utf-8"))
        if did and did > highest:
            highest = did
    return highest + 1


def list_decks(out_dir: Path) -> None:
    """Print every deck's id and name so a number is on hand for --recheck /
    --reimport. Backfills ids into any note still missing one.
    """
    ids = deck_id_map(out_dir)
    if not ids:
        print(f"No deck notes found in {out_dir}")
        return
    for did in sorted(ids):
        text = ids[did].read_text(encoding="utf-8")
        name_m = (re.search(r"^deck-name: (.+)$", text, re.M)
                  or re.search(r"^# 🃏 (.+)$", text, re.M))
        name = name_m.group(1).strip() if name_m else ids[did].stem
        print(f"[{did:>3}] {name}  —  {ids[did].name}")


def recheck_all(out_dir: Path) -> None:
    """Refresh every deck note by re-importing it from its original source —
    a Moxfield/EDHREC URL or the archived .txt in the vault's imports/ folder
    — so deck edits,
    fresh prices and fresh galleries all land (commander art is reused from
    the note unless the commander changed). If a source
    can't be reached (dead link, file gone, offline), that deck falls back to
    a price/buy refresh from the list stored in the note. Review sections are
    preserved throughout.
    """
    collection = load_collection(out_dir)
    if not collection:
        sys.exit(f"No collection file found at {collection_path(out_dir)}")
    collection_name, owned = collection
    ids = deck_id_map(out_dir)  # backfill ids so every note is addressable
    if not ids:
        sys.exit(f"No deck notes found in {out_dir}")

    for did, note in sorted(ids.items()):
        url_m = re.search(r"^deck-url: (.+)$", note.read_text(encoding="utf-8"), re.M)
        source = url_m.group(1).strip() if url_m else None
        if source:
            try:
                import_deck(source, out_dir, force=True, own=False, deck_id=did)
                continue
            except (SystemExit, Exception) as exc:  # source unreachable → fall back
                print(f"[{did}] {note.name}: source refresh failed ({exc}) — "
                      "refreshing prices from the stored list")
        _recheck_from_stored(did, note, collection_name, owned)


def _recheck_from_stored(did: int, note: Path, collection_name: str,
                         owned: dict[str, int]) -> None:
    """Refresh a note's prices, buy sections and Cheapest Build from the deck
    list already stored in it — no site fetching, no art re-download. The
    --recheck fallback when the original source can't be reached. The note is
    rebuilt wholesale from its stored fields (header, deck list, reviews,
    commander image URL), which also migrates older notes to the current
    layout instead of patching sections in place.
    """
    text = note.read_text(encoding="utf-8")
    decklist = _note_decklist(text)
    if not decklist:
        print(f"[{did}] {note.name}: no deck list found — skipped")
        return

    def field(pattern: str, fallback: str = "") -> str:
        m = re.search(pattern, text, re.M)
        return m.group(1).strip() if m else fallback

    deck = {
        "name": field(r"^deck-name: (.+)$") or field(r"^# 🃏 (.+)$", note.stem),
        "format": field(r"^\*\*Format:\*\* (.+)$", "Commander"),
        "source_md": field(r"^\*\*Source:\*\* (.+)$"),
        # The frontmatter line already holds all commanders joined with ", "
        "commanders": [field(r"^commander: (.+)$", decklist[0][1])],
    }
    deck_url = field(r"^deck-url: (.+)$")
    image_url = field(r"!\[[^\]]*\|290\]\(([^)]*)\)")
    created = field(r"^created: (.+)$")

    prices = fetch_prices([n for _, n in decklist])
    report = price_report(decklist, prices)
    buy = buy_report(decklist, owned, prices)
    choices = budget_choices(decklist, prices)
    new_text = build_note(deck, decklist, image_url, deck_url, report, buy,
                          collection_name, extract_reviews(text), choices, did)
    if created:  # keep the original import date, not the refresh date
        new_text = re.sub(r"^created: .*$", f"created: {created}", new_text,
                          count=1, flags=re.M)
    note.write_text(new_text, encoding="utf-8")
    print(f"[{did}] {note.name}: value ~EUR {report['totals']['eur']:,.2f}"
          f" — own {buy['owned_unique']}/{buy['unique']}"
          f" — to buy {len(buy['missing'])} (~EUR {buy['totals']['eur']:,.2f})")


def resolve_out_dir() -> Path:
    out = output_dir()
    if not out.is_dir():
        sys.exit(f"Vault output folder does not exist: {out}")
    return out


def resolve_source(source: str, out_dir: Path) -> str:
    """Find the archived .txt behind a note's deck-url. Archives live in
    imports/ INSIDE the vault folder (synced across machines, unlike the
    repo), so every machine can fully re-import a .txt deck. Legacy locations
    still resolve: a path relative to the script, and the old script-local
    imports/ folder. PureWindowsPath parses the name out of either slash
    style ('imports/mill.txt', '.\\my deck.txt').
    """
    if not source.lower().endswith(".txt") or Path(source).is_file():
        return source
    name = PureWindowsPath(source.replace("/", "\\")).name
    for candidate in (out_dir / "imports" / name,
                      SCRIPT_DIR / source,
                      SCRIPT_DIR / "imports" / name):
        if candidate.is_file():
            return str(candidate)
    return source


def import_deck(source: str, out_dir: Path, *, force: bool, own: bool,
                deck_id: int | None = None) -> None:
    """Fetch a deck (URL or .txt), price it, download art, and write/refresh
    its note. deck_id pins the note's id when re-importing a known deck; for a
    new deck it is inherited from a matched note or freshly assigned.
    """
    deck_url = source
    deck = fetch_deck(resolve_source(source, out_dir))
    if not deck["commanders"]:
        sys.exit("No commander found on this deck — is it a Commander deck?")

    # Imported .txt files are archived to imports/ inside the vault after a
    # successful run — synced with the vault, so any machine can re-import;
    # the note's deck-url points at the archived copy
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

    # Keep the note's id: use the passed id, else the matched note's own, else
    # the next free one — so a re-import never renumbers an existing deck.
    if deck_id is None:
        deck_id = (read_deck_id(match.read_text(encoding="utf-8")) if match
                   else None) or next_deck_id(out_dir)

    attachments_dir = out_dir / "Attachments"
    attachments_dir.mkdir(exist_ok=True)
    image_path = attachments_dir / f"{stem}.jpg"
    # Re-imports reuse the existing commander art (hosted URL from the note,
    # jpg already on disk) as long as the commander hasn't changed — the image
    # is static, so --recheck shouldn't re-download it. --reimport is the
    # explicit "refresh my art" switch and always re-fetches.
    image_url = None
    if match and image_path.is_file():
        old_text = match.read_text(encoding="utf-8")
        same_commander = re.search(
            rf"^commander: {re.escape(', '.join(deck['commanders']))}\s*$",
            old_text, re.M)
        img_m = re.search(r"!\[[^\]]*\|290\]\((https?://[^)]+)\)", old_text)
        if same_commander and img_m:
            image_url = img_m.group(1)
    if image_url is None:
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

    choices = budget_choices(decklist, prices)
    note_path.write_text(
        build_note(deck, decklist, image_url, deck_url, report, buy,
                   collection_name, reviews, choices, deck_id),
        encoding="utf-8",
    )

    if txt_src:
        imports_dir = out_dir / "imports"  # in the vault → syncs to every machine
        imports_dir.mkdir(exist_ok=True)
        dest = imports_dir / txt_src.name
        if txt_src != dest.resolve():
            shutil.move(str(txt_src), dest)  # move() survives cross-volume vaults
            print(f"Archived:  {dest}")

    total = sum(qty for qty, _ in decklist)
    totals = report["totals"]
    print(f"Deck:      {deck['name']} (id {deck_id})")
    print(f"Commander: {', '.join(deck['commanders'])}")
    print(f"Cards:     {total} ({len(decklist)} unique)")
    rates = report["rates"]
    gbp = f" / GBP {totals['eur'] * rates['eur_gbp']:,.2f}" if rates else ""
    print(f"Value:     ~EUR {totals['eur']:,.2f}{gbp} / USD {totals['usd']:,.2f}"
          f" / TIX {totals['tix']:,.2f}"
          + (f"  ({len(report['unpriced'])} unpriced)" if report["unpriced"] else ""))
    if buy is not None:
        cheap = cheapest_buy(buy, choices, rates)
        print(f"Owned:     {buy['owned_unique']}/{buy['unique']} cards"
              f" — to buy: {len(buy['missing'])} (~EUR {buy['totals']['eur']:,.2f}"
              f" / ~EUR {cheap['totals']['eur']:,.2f} at cheapest versions)")
    else:
        print("Owned:     no _Collection.md found — skipped the Cards to Complete sections")
    print(f"Note:      {note_path}")
    print(f"Artwork:   {image_path}")


def recheck_one(out_dir: Path, deck_id: int) -> None:
    """Full re-import of a single deck by id: re-fetch its list from source,
    fresh prices, fresh art — the whole pipeline, scoped to one note.
    """
    ids = deck_id_map(out_dir)
    note = ids.get(deck_id)
    if not note:
        sys.exit(f"No deck with id {deck_id}. Run --list to see the ids "
                 f"(known: {', '.join(map(str, sorted(ids))) or 'none'}).")
    m = re.search(r"^deck-url: (.+)$", note.read_text(encoding="utf-8"), re.M)
    if not m:
        sys.exit(f"{note.name}: no deck-url stored, so it can't be re-imported.")
    import_deck(m.group(1).strip(), out_dir, force=True, own=False,
                deck_id=deck_id)


def _note_decklist(text: str) -> list[tuple[int, str]] | None:
    """The deck's card list as stored in its note — commanders first, exactly
    as written. The --reimport fallback when the live source can't be fetched.
    (Names aren't split out of the commander frontmatter: a single commander
    name can itself contain a comma, e.g. 'Atraxa, Praetors' Voice'.)
    """
    block = re.search(r"## 📜 Deck List\s*```\n(.*?)```", text, re.S)
    if not block:
        return None
    decklist = [p for p in map(parse_card_line, block.group(1).splitlines()) if p]
    return decklist or None


def reimport(out_dir: Path, deck_id: int | None) -> None:
    """Refresh deck lists and card art without touching prices. For each note
    (or one, by id): re-fetch the list from its source (falling back to the
    list stored in the note if that fails), re-download the commander art, and
    rebuild the card gallery from fresh images. Price, buy and Cheapest Build
    sections are left exactly as they are — run --recheck to refresh those.
    """
    ids = deck_id_map(out_dir)
    if deck_id is not None:
        if deck_id not in ids:
            sys.exit(f"No deck with id {deck_id}. Run --list to see the ids.")
        targets = {deck_id: ids[deck_id]}
    else:
        targets = ids
    if not targets:
        sys.exit(f"No deck notes found in {out_dir}")

    for did, note in sorted(targets.items()):
        text = note.read_text(encoding="utf-8")
        url_m = re.search(r"^deck-url: (.+)$", text, re.M)
        if not url_m:
            print(f"[{did}] {note.name}: no deck-url — skipped")
            continue
        deck_url = url_m.group(1).strip()

        deck = None
        try:
            deck = fetch_deck(resolve_source(deck_url, out_dir))
        except (SystemExit, Exception) as exc:  # any failure → stored-list fallback
            print(f"[{did}] {note.name}: live fetch failed ({exc}) — "
                  "using the list stored in the note")
        if deck and deck.get("commanders"):
            mainboard = sorted(deck["mainboard"], key=lambda c: c[1].lower())
            decklist = [(1, n) for n in deck["commanders"]] + mainboard
            primary = deck["commanders"][0]
        else:
            decklist = _note_decklist(text)
            if not decklist:
                print(f"[{did}] {note.name}: no stored list to fall back on — skipped")
                continue
            primary = decklist[0][1]  # listing puts the commander first

        # Prices/buy sections are left untouched, so warn if the live list
        # drifted from what those sections were priced against.
        list_changed = set(decklist) != set(_note_decklist(text) or [])

        # Commander banner: refresh the local jpg and swap just the URL,
        # leaving the existing alt text (and everything else) alone.
        image_path = out_dir / "Attachments" / f"{note.stem}.jpg"
        image_path.parent.mkdir(exist_ok=True)
        image_url = fetch_commander_art(primary, image_path)
        text = re.sub(r"(!\[[^\]]*\|290\]\()[^)]*(\))",
                      lambda mm: f"{mm.group(1)}{image_url}{mm.group(2)}",
                      text, count=1)

        # Deck List block — replace with the (possibly updated) list
        listing = "\n".join(f"{qty} {name}" for qty, name in decklist)
        text = re.sub(r"(## 📜 Deck List\s*```\n).*?(```)",
                      lambda m: f"{m.group(1)}{listing}\n{m.group(2)}",
                      text, count=1, flags=re.S)

        # Card gallery — fresh images only, no price lookups
        imgs = fetch_card_images([n for _, n in decklist])
        cells = [(imgs.get(name.lower()), f"{name}{f' ×{qty}' if qty > 1 else ''}")
                 for qty, name in decklist]
        gallery = render_card_gallery(cells)
        if "### 🖼️ Card Gallery" in text:
            text = re.sub(r"### 🖼️ Card Gallery\n.*?(?=\n## 📜 Deck List)",
                          lambda _: gallery, text, count=1, flags=re.S)
        else:
            text = text.replace("## 📜 Deck List", f"{gallery}\n\n## 📜 Deck List", 1)

        note.write_text(text, encoding="utf-8")
        changed = (f" — list changed, run --recheck {did} to refresh prices"
                   if list_changed else "")
        print(f"[{did}] {note.name}: list + art refreshed "
              f"({len(decklist)} cards){changed}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mtg_deck_importer.py",
        description="Turn a Moxfield/EDHREC deck URL or a .txt decklist into an "
                    "Obsidian deck note with prices, a buy list and card galleries.",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "source", nargs="?",
        help="Moxfield/EDHREC deck URL, or a path to a .txt decklist")
    parser.add_argument(
        "--force", action="store_true",
        help="regenerate an existing note (your review sections are kept)")
    parser.add_argument(
        "--own", action="store_true",
        help="append the whole deck to _Collection.md as owned before comparing")
    parser.add_argument(
        "--recheck", nargs="?", const="__all__", default=None, metavar="ID",
        help="no ID: refresh every note's prices/buy list from the lists "
             "already in the notes (no site fetching). With an ID (see --list): "
             "full re-import of just that deck (list + prices + art)")
    parser.add_argument(
        "--reimport", nargs="?", const="__all__", default=None, metavar="ID",
        help="refresh deck lists and card art from source WITHOUT new prices; "
             "all notes, or one by ID (see --list)")
    parser.add_argument(
        "--list", action="store_true", dest="list_ids",
        help="list every deck's id and name, then exit")
    return parser


def _parse_id(val: str) -> int:
    try:
        return int(val)
    except ValueError:
        if "://" in val or val.lower().endswith(".txt"):
            sys.exit(f"--recheck/--reimport take a deck id, not a source.\n"
                     f"To re-import that deck from its source run:\n"
                     f"  python mtg_deck_importer.py --force \"{val}\"\n"
                     "or find its id with --list and use --recheck <id>.")
        sys.exit(f"Deck id must be a number, got {val!r} — run --list to see ids.")


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.list_ids:
        list_decks(resolve_out_dir())
        return
    if args.recheck is not None:
        if args.source:
            parser.error("--recheck takes an optional deck id, not a URL/file.")
        out_dir = resolve_out_dir()
        if args.recheck == "__all__":
            recheck_all(out_dir)
        else:
            recheck_one(out_dir, _parse_id(args.recheck))
        return
    if args.reimport is not None:
        if args.source:
            parser.error("--reimport takes an optional deck id, not a URL/file.")
        out_dir = resolve_out_dir()
        reimport(out_dir, None if args.reimport == "__all__"
                 else _parse_id(args.reimport))
        return
    if not args.source:
        parser.error("a deck URL or .txt decklist path is required "
                     "(or use --recheck / --reimport / --list)")
    import_deck(args.source, resolve_out_dir(), force=args.force, own=args.own)


if __name__ == "__main__":
    main()
