"""MTG Deck Importer — deck source -> Obsidian vault deck note.

Takes a Moxfield URL, an EDHREC deckpreview URL or a local .txt decklist and
writes a markdown deck note: prices, ownership comparison, buy lists, a
Cheapest Build, deck-shape stats and card galleries. Run --help for the flags;
each one documents itself there, which is why this docstring does not list
them.

Sources
    Moxfield  headed Chrome (Cloudflare blocks plain requests and headless)
    EDHREC    plain HTTP
    .txt      one "1 Card Name" per line, first card is the commander, the
              filename becomes the deck name. Archived to imports/ in the
              vault on success so any machine can re-import it.

Prices
    Deck notes are priced against Cardmarket (EUR) with a converted ≈ GBP
    column, plus TCGPlayer USD and Cardhoarder tix for reference — all from
    Scryfall's daily snapshot. The Cheapest Build picks each card's cheapest
    Cardmarket printing, so the pinned (SET) 123 printing and its price always
    describe the same card. ManaPool's live US listings are used only by
    --collection-value.

Reference DB
    Every paper printing lives in a local SQLite file rebuilt daily from
    Scryfall's bulk export (.cache/scryfall.sqlite3, ~60 MB). Printing lookups
    are indexed queries rather than one rate-limited search per card, which is
    what used to make a --recheck take minutes. --refresh-db rebuilds it on
    demand; --no-bulk skips it and uses the API.

Contract with the vault
    VAULT_OUTPUT_DIR (a .env file next to this script — see .env.example) sets
    the output folder. Every deck note carries a stable deck-id in its
    frontmatter so a deck can be targeted by number; --list shows them.
    Whatever you write under the review headings is preserved through every
    rebuild — the generated data around it is what gets refreshed.

    Output note:  <Commander> <colour emojis>.md   (e.g. "Krenko, Mob Boss 🔴")
    Output image: Attachments/<same stem>.jpg
    The trailing ⚪🔵⚫🔴🟢 circles are the commander's colour identity; they
    also end the deck-name frontmatter and the H1. --colorize stamps them onto
    decks imported before the feature existed.
"""

import argparse
import atexit
import gzip
import json
import os
import re
import shutil
import sqlite3
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
ARCHIDEKT_API = "https://archidekt.com/api/decks/{deck_id}/"

# Windows-illegal filename characters (commas are fine and kept)
ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')

# Commander colour identity -> coloured circle emoji, appended to the end of a
# deck's name (note filename, deck-name frontmatter and H1) in WUBRG order —
# the colour wheel order printed on the back of every Magic card.
COLOR_EMOJI = {"W": "⚪", "U": "🔵", "B": "⚫", "R": "🔴", "G": "🟢"}
COLORLESS_EMOJI = "◇"  # colourless decks (Kozilek et al.) still get a marker
COLOR_SUFFIX_RE = re.compile(r"\s*[⚪🔵⚫🔴🟢◇]+\s*$")


def strip_colors(name: str) -> str:
    """A deck name without its trailing colour-emoji suffix — the canonical
    name every match/compare runs on, so a suffixed and an unsuffixed spelling
    of the same deck are still the same deck.
    """
    return COLOR_SUFFIX_RE.sub("", name).rstrip()


def color_suffix_of(name: str) -> str:
    """The trailing colour-emoji run of a deck name, or '' if it has none."""
    m = COLOR_SUFFIX_RE.search(name)
    return m.group().strip() if m else ""

# Moxfield export decorations after a card name: foil/etched markers (*F*),
# collector info like (PLST) 123 — strip so names match Scryfall
NAME_DECORATIONS = re.compile(r"\s*(\*[A-Za-z]\*|\([A-Z0-9]{2,6}\)\s*[\w-]*)\s*$")


# A foil is the same printing on shiny stock, so it shares a collector number
# and needs its own marker. ✨ is the friendly one to type; *F* is what Moxfield
# and most exports emit. Both are accepted, anywhere on the line.
FOIL_MARKER = re.compile(r"\s*(?:\*[A-Za-z]\*|✨)\s*")
SET_SUFFIX = re.compile(r"\s*\(([A-Za-z0-9]{2,6})\)\s*([\w-]*)\s*$")
# Archidekt exports end lines with a hand-authored role tag — `… [Removal]`.
# Card names never contain square brackets, so a trailing [] is always a tag.
CATEGORY_SUFFIX = re.compile(r"\s*\[([^\[\]]+)\]\s*$")


def parse_card_line_full(
        line: str,
) -> tuple[int, str, str | None, str | None, bool, str | None] | None:
    """(quantity, name, set code, collector number, is foil, category) from a
    card line.

    The set/number suffix — `1 Island (FIN) 298` — pins the exact printing,
    ✨ (or *F*) marks it as the foil of that printing, and a trailing
    `[Removal]` is an Archidekt category tag. Deck matching ignores all three,
    since any Island plays the same; deck notes keep the pin and foil so the
    note prices the version you actually chose, and the category as reference.
    """
    line = line.strip()
    if not line:
        return None
    m = re.match(r"(\d+)[xX]?\s+(.+)", line)
    qty, rest = (int(m.group(1)), m.group(2)) if m else (1, line)
    category = None
    c = CATEGORY_SUFFIX.search(rest)  # strip first: it trails the set/number
    if c:
        category = c.group(1).strip()
        rest = rest[:c.start()].strip()
    foil = bool(FOIL_MARKER.search(rest))
    rest = FOIL_MARKER.sub(" ", rest).strip()
    set_code = number = None
    s = SET_SUFFIX.search(rest)
    if s:
        set_code = s.group(1).lower()
        number = (s.group(2) or "").strip() or None
        rest = rest[:s.start()].strip()
    return (qty, rest, set_code, number, foil, category) if rest else None


def read_collection_entries(path: Path) -> list[dict]:
    """Every card line in a collection file, keeping the detail the name-only
    view throws away: which printing, and whether it's foil.
    """
    entries: list[dict] = []
    if not path.is_file():
        return entries
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not re.match(r"\d", line.strip()):
            continue
        p = parse_card_line_full(line)
        if p:
            entries.append({"qty": p[0], "name": p[1], "set": p[2],
                            "num": p[3], "foil": p[4]})
    return entries


def parse_card_line(line: str) -> tuple[int, str] | None:
    """(quantity, name) — the printing-agnostic view used everywhere a card is
    just a card (deck lists, ownership counts, price lookups).
    """
    parsed = parse_card_line_full(line)
    return (parsed[0], parsed[1]) if parsed else None


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


# The three states a collection file can be in. "empty" is its own case
# because a file full of prose but no card lines reads as owning nothing —
# silently comparing against it would mark a whole deck as unowned.
COLL_MISSING, COLL_EMPTY, COLL_READY = "missing", "empty", "ready"


def collection_state(out_dir: Path) -> tuple[str, Path, dict[str, int]]:
    """(state, path, owned cards) — the one place that decides whether there is
    a usable collection to compare decks against.
    """
    path = collection_path(out_dir)
    loaded = load_collection(out_dir)
    if loaded is None:
        return COLL_MISSING, path, {}
    owned = loaded[1]
    return (COLL_READY if owned else COLL_EMPTY), path, owned


def report_collection_state(out_dir: Path, consequence: str) -> dict[str, int]:
    """Print the collection's state and, when it can't be used, spell out what
    that costs and how to fix it — so a run is never silently missing the
    ownership comparison. Returns the owned cards ({} when unusable).
    """
    state, path, owned = collection_state(out_dir)
    if state == COLL_READY:
        copies = sum(owned.values())
        print(f"Collection: {path.name} — {len(owned)} unique cards "
              f"({copies} copies)")
        return owned
    why = (f"no collection file at {path}" if state == COLL_MISSING
           else f"{path.name} has no 'N Card Name' lines yet")
    print(f"Collection: ⚠️  {why}")
    print(f"           → {consequence}")
    print("           → to fix: python mtg_deck_importer.py --collection "
          "\"your-cards.txt\"  (or create the file by hand)")
    return {}


_card_info_cache: dict[str, dict] = {}
_prints_fetched = 0

# ---------------------------------------------------------------------------
# Scryfall bulk reference DB
#
# Every printing of every paper card in one local SQLite file, rebuilt from
# Scryfall's daily "default_cards" export. This replaces a per-card search
# request (~0.5s each, rate-limited, and the thing that made a --recheck take
# minutes) with an indexed query.
#
# It also fixes a correctness problem the old per-card cache could not: the
# cheapest printing and the deck's own price now come from the same snapshot,
# and every printing is considered rather than the first page of 175.
# ---------------------------------------------------------------------------

SCRYFALL_DB = SCRIPT_DIR / ".cache" / "scryfall.sqlite3"
BULK_DATA_URL = "https://api.scryfall.com/bulk-data"
DB_TTL = 24 * 3600  # Scryfall regenerates the export daily

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
  id               TEXT PRIMARY KEY,
  name             TEXT NOT NULL,
  face_name        TEXT,
  flavor_name      TEXT,
  set_code         TEXT NOT NULL,
  set_name         TEXT NOT NULL DEFAULT '',
  set_type         TEXT NOT NULL DEFAULT '',
  collector_number TEXT NOT NULL DEFAULT '',
  rarity           TEXT NOT NULL DEFAULT '',
  type_line        TEXT NOT NULL DEFAULT '',
  cmc              REAL,
  oracle_text      TEXT NOT NULL DEFAULT '',
  image_uri        TEXT,
  eur              REAL,
  eur_foil         REAL,
  usd              REAL,
  usd_foil         REAL,
  tix              REAL,
  finishes         TEXT NOT NULL DEFAULT '[]',
  released_at      TEXT,
  layout           TEXT NOT NULL DEFAULT 'normal',
  frame_effects    TEXT NOT NULL DEFAULT '[]',
  full_art         INTEGER NOT NULL DEFAULT 0,
  border_color     TEXT NOT NULL DEFAULT '',
  promo            INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

DB_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_name ON cards (name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_face ON cards (face_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_flavor ON cards (flavor_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_set ON cards (set_code);
"""

_bulk_enabled = True   # --no-bulk turns the DB off and falls back to the API
_bulk_force = False    # --refresh-db rebuilds even if the DB is fresh


def _price(p: dict, key: str) -> float | None:
    v = p.get(key)
    try:
        return float(v) if v else None
    except (TypeError, ValueError):
        return None


def _bulk_row(c: dict) -> tuple | None:
    """One Scryfall card object -> a row, or None for cards we never price
    (digital-only printings can't be bought in paper).
    """
    games = c.get("games") or ["paper"]
    if "paper" not in games:
        return None
    name = c["name"]
    faces = c.get("card_faces") or []
    uris = c.get("image_uris") or (faces[0].get("image_uris") if faces else None)
    p = c.get("prices") or {}
    return (
        c["id"], name,
        name.split("//")[0].strip() if "//" in name else None,
        c.get("flavor_name"),
        c.get("set", "").lower(), c.get("set_name", ""),
        c.get("set_type", ""),
        str(c.get("collector_number", "")), c.get("rarity", ""),
        c.get("type_line", ""), c.get("cmc"),
        c.get("oracle_text") or " // ".join(
            f.get("oracle_text", "") for f in faces),
        (uris or {}).get("small"),
        _price(p, "eur"), _price(p, "eur_foil"),
        _price(p, "usd"), _price(p, "usd_foil"), _price(p, "tix"),
        json.dumps(c.get("finishes") or []), c.get("released_at"),
        c.get("layout", "normal"), json.dumps(c.get("frame_effects") or []),
        1 if c.get("full_art") else 0, c.get("border_color", ""),
        1 if c.get("promo") else 0,
    )


def _bulk_download_info() -> tuple[str, str]:
    """(download URI, updated_at) for the default_cards export. Scryfall moved
    the payload to gzipped JSONL and the listing to pointers, so follow the
    entry's own uri when the download fields aren't inlined.
    """
    r = http("GET", BULK_DATA_URL)
    r.raise_for_status()
    entry = next((d for d in r.json()["data"] if d["type"] == "default_cards"),
                 None)
    if entry is None:
        raise RuntimeError("no default_cards entry in Scryfall's bulk-data listing")
    if not (entry.get("jsonl_download_uri") or entry.get("download_uri")):
        detail = http("GET", entry["uri"])
        detail.raise_for_status()
        entry = detail.json()
    uri = entry.get("jsonl_download_uri") or entry.get("download_uri")
    if not uri:
        raise RuntimeError("no download URI in Scryfall's default_cards entry")
    return uri, entry.get("updated_at", "")


def _build_scryfall_db() -> None:
    """Download the daily export and rebuild the reference DB.

    Built to a .tmp file and renamed at the end, so an existing DB keeps
    working if this dies halfway.
    """
    uri, updated_at = _bulk_download_info()
    SCRYFALL_DB.parent.mkdir(exist_ok=True)
    raw = SCRYFALL_DB.with_suffix(".download")
    print("Scryfall:  downloading the daily card export (~80 MB, once a day)...")
    with SESSION.get(uri, stream=True, timeout=300) as resp:
        resp.raise_for_status()
        got = 0
        with raw.open("wb") as fh:
            for chunk in resp.iter_content(1 << 20):
                fh.write(chunk)
                got += len(chunk)
                if got % (20 << 20) < (1 << 20):
                    print(f"Scryfall:  ...{got / 1e6:,.0f} MB")

    tmp = SCRYFALL_DB.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    try:
        # Throwaway build — if it dies we rebuild from scratch, so durability
        # buys nothing and costs a lot of time.
        db.execute("PRAGMA journal_mode = OFF")
        db.execute("PRAGMA synchronous = OFF")
        db.executescript(DB_SCHEMA)
        insert = ("INSERT OR REPLACE INTO cards VALUES ("
                  + ",".join("?" * 25) + ")")
        batch, total = [], 0
        opener = gzip.open if uri.endswith(".gz") else open
        with opener(raw, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip().rstrip(",")
                if not line.startswith("{"):
                    continue  # the legacy format wraps rows in a JSON array
                row = _bulk_row(json.loads(line))
                if row is None:
                    continue
                batch.append(row)
                if len(batch) >= 5000:
                    db.executemany(insert, batch)
                    total += len(batch)
                    batch = []
        if batch:
            db.executemany(insert, batch)
            total += len(batch)
        db.executescript(DB_INDEXES)
        db.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)",
                       [("updated_at", updated_at),
                        ("built_at", str(int(time.time()))),
                        ("cards", str(total))])
        db.commit()
    finally:
        db.close()
    raw.unlink(missing_ok=True)
    SCRYFALL_DB.unlink(missing_ok=True)
    tmp.replace(SCRYFALL_DB)
    print(f"Scryfall:  reference DB built — {total:,} paper printings")


def _db_is_stale() -> bool:
    if not SCRYFALL_DB.is_file():
        return True
    try:
        db = sqlite3.connect(f"file:{SCRYFALL_DB}?mode=ro", uri=True)
        try:
            row = db.execute(
                "SELECT value FROM meta WHERE key = 'built_at'").fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return True
    if not row:
        return True
    # A built_at in the future means a corrupted or hand-edited value; treat it
    # as stale rather than letting it pin the DB as fresh forever.
    age = time.time() - float(row[0])
    return not (0 <= age < DB_TTL)


@lru_cache(maxsize=1)
def scryfall_db() -> sqlite3.Connection | None:
    """The reference DB, rebuilt if missing or over a day old. Returns None
    when --no-bulk is set or the build fails — every caller has an API path.
    """
    if not _bulk_enabled:
        return None
    try:
        if _bulk_force or _db_is_stale():
            _build_scryfall_db()
    except (OSError, ValueError, RuntimeError, sqlite3.Error,
            requests.RequestException) as exc:
        if not SCRYFALL_DB.is_file():
            print(f"Scryfall:  bulk DB unavailable ({exc}) — falling back to "
                  "the API for this run")
            return None
        print(f"Scryfall:  bulk refresh failed ({exc}) — using the existing DB")
    try:
        db = sqlite3.connect(f"file:{SCRYFALL_DB}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        return db
    except sqlite3.Error:
        return None


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


def _buyable(p: dict) -> bool:
    """Is this a price you could actually turn up and pay?

    Cheapest means cheapest — collector printings included. Collectors'
    Edition, World Championship decks and 30th Anniversary are all fair game
    if they're the cheapest way to get the card; whether one suits a given
    playgroup is a call for the person buying, not for this script.

    The one thing still filtered is a price with nothing behind it. Summer
    Magic is a 1994 test print worth thousands, yet Cardmarket shows €3.00 for
    its Birds of Paradise. What gives those away is a EUR price with no USD
    price at all — one lonely European listing and no second market to
    corroborate it. Requiring both is blunt, but it is the difference between
    a bargain and a card nobody is selling.
    """
    return p.get("eur") is not None and p.get("usd") is not None


def _pick_cheapest(priced: list[dict]) -> dict | None:
    """The cheapest printing anyone could actually buy and play, from a list
    sorted by EUR ascending.

    Prefer a printing that passes _buyable. If none does — a card only ever
    printed in an untraded set — fall back to the cheapest of what's left
    rather than reporting no price at all.
    """
    if not priced:
        return None
    return next((p for p in priced if _buyable(p)), priced[0])


def _prints_from_db(name: str, canonical: str | None) -> dict | None:
    """Aliases and the cheapest printing straight out of the reference DB.

    Every printing is considered — no page limit, and no price floor, because
    an indexed query over 800 printings costs the same as one over 3.
    """
    db = scryfall_db()
    if db is None:
        return None
    # Resolve to the canonical name first: a collection line may use a
    # Universes Beyond skin name ("Loki's Double") or a double-faced card's
    # front face ("Malakir Rebirth"), and we want ALL printings of the card
    # behind it, not just the ones bearing that name.
    if canonical is None:
        row = db.execute(
            "SELECT name FROM cards WHERE name = ? COLLATE NOCASE "
            "OR flavor_name = ? COLLATE NOCASE OR face_name = ? COLLATE NOCASE "
            "LIMIT 1", (name, name, name)).fetchone()
        if row is None:
            return None
        canonical = row["name"]
    rows = db.execute(
        "SELECT name, flavor_name, set_name, set_code, set_type, "
        "collector_number, eur, usd, image_uri, border_color "
        "FROM cards WHERE name = ? COLLATE NOCASE", (canonical,)).fetchall()
    if not rows:
        return None
    info = {"aliases": {name.lower(), canonical.lower()}, "canonical": canonical,
            "cheapest": None, "ts": time.time()}
    if "//" in canonical:
        info["aliases"].add(canonical.split("//")[0].strip().lower())
    priced = []
    for r in rows:
        if r["flavor_name"]:
            info["aliases"].add(r["flavor_name"].lower())
        if r["eur"] is None:
            continue
        priced.append({"eur": r["eur"], "usd": r["usd"], "set": r["set_name"],
                       # the row knows its own set code, so the pin never
                       # depends on a set-name lookup that could miss
                       "set_code": r["set_code"], "set_type": r["set_type"],
                       "num": r["collector_number"],
                       "printed_as": r["flavor_name"] or r["name"],
                       "img": r["image_uri"], "border": r["border_color"]})
    priced.sort(key=lambda c: c["eur"])
    info["cheapest"] = _pick_cheapest(priced)
    return info


def card_prints_info(name: str, canonical: str | None = None) -> dict:
    """One prints lookup per card, reused for two jobs: (a) every name the
    card can appear under — canonical plus Universes Beyond flavor names
    (the Marvel precons print Spark Double as "Loki's Double"); (b) the
    cheapest paper printing, since a flavor-named skin is the same card and
    the plain version is often cheaper.

    Served from the local reference DB. The API path below is the fallback for
    a card newer than the last DB refresh (or --no-bulk), and unlike the DB it
    reads only Scryfall's first page of 175 printings.
    """
    key = name.lower()
    if key in _card_info_cache:
        return _card_info_cache[key]
    info = _prints_from_db(name, canonical)
    if info is not None:
        _card_info_cache[key] = info
        return info

    global _prints_fetched
    _prints_fetched += 1
    if _prints_fetched == 1:
        print("Scryfall:  looking up printings via the API (~0.5s per card)...")
    elif _prints_fetched % 10 == 0:
        print(f"Scryfall:  ...{_prints_fetched} cards looked up")
    info = {"aliases": {key}, "canonical": name, "cheapest": None,
            "ts": time.time()}
    if canonical is None:
        r = http("GET", SCRYFALL_NAMED, params={"exact": name})
        if r.status_code == 200:
            canonical = r.json()["name"]
    if canonical is not None:
        info["canonical"] = canonical
        info["aliases"].add(canonical.lower())
        # Double-faced cards: let a front-face-only name in the collection
        # ("Malakir Rebirth") match the full "A // B" deck name
        if "//" in canonical:
            info["aliases"].add(canonical.split("//")[0].strip().lower())
        url = SCRYFALL_SEARCH
        params: dict | None = {"q": f'!"{canonical}" game:paper',
                               "unique": "prints"}
        priced = []
        while url:
            s = http("GET", url, params=params)
            if s.status_code != 200:
                break
            j = s.json()
            for c in j.get("data", []):
                if c.get("flavor_name"):
                    info["aliases"].add(c["flavor_name"].lower())
                eur = float(c["prices"]["eur"]) if c["prices"].get("eur") else None
                if eur is None:
                    continue
                priced.append({
                    "eur": eur,
                    "usd": float(c["prices"]["usd"]) if c["prices"].get("usd") else None,
                    "set": c["set_name"], "set_code": c.get("set"),
                    "set_type": c.get("set_type", ""),
                    "num": c["collector_number"],
                    "printed_as": c.get("flavor_name") or c["name"],
                    "img": scryfall_card_image(c),
                    "border": c.get("border_color", "")})
            url, params = j.get("next_page"), None
        priced.sort(key=lambda c: c["eur"])
        info["cheapest"] = _pick_cheapest(priced)
    _card_info_cache[key] = info
    return info


def buy_report(decklist: list[tuple[int, str]], owned: dict[str, int],
               prices: dict[str, dict]) -> dict:
    """Which deck cards are missing from the collection, and what the gap
    costs (per-source totals use the already-fetched deck prices).
    """
    missing = []
    owned_rows = []  # fully-covered cards, shown as ✅ in the shopping table
    totals = {"eur": 0.0, "usd": 0.0}
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
        missing.append({"need": need, "have": have, "name": name,
                        "eur": p.get("eur"), "usd": p.get("usd"),
                        "set": p.get("set"), "num": p.get("num"),
                        "foil": bool(p.get("foil")), "info": info})
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

    Used only by --collection-value, which values the exact printings you own
    against a live marketplace. Deck notes price against Cardmarket, so a deck
    run never touches this and never downloads the catalog.

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


BASIC_LAND_NAMES = {"plains", "island", "swamp", "mountain", "forest", "wastes"}


def read_card_list(list_path: str) -> dict[str, list]:
    """Parse any card-list file (a store/Moxfield export, a deck list, a plain
    list) into {lowercased name: [total qty, display name]}, merging duplicate
    rows — exports split the same card across printings, and those copies are
    all still copies you own.
    """
    src = Path(list_path)
    if not src.is_file():
        sys.exit(f"List file not found: {src.resolve()}")
    cards: dict[str, list] = {}
    for line in src.read_text(encoding="utf-8-sig").splitlines():
        parsed = parse_card_line(line)
        if parsed:
            qty, disp = parsed
            entry = cards.setdefault(disp.lower(), [0, disp])
            entry[0] += qty
    if not cards:
        sys.exit(f"No cards found in {src}")
    return cards


def create_collection(out_dir: Path, list_path: str, force: bool = False) -> None:
    """--collection: build the collection file from a card-list export. The
    output is deliberately plain — a short header and one 'N Card Name' per
    line, alphabetical — so it stays easy to eyeball, diff and hand-edit. Any
    extra structure (precon sections, value tables, notes) is yours to add
    afterwards; the parser ignores everything that isn't a card line.

    Refuses to clobber a collection that already lists cards unless forced,
    because that file is hand-curated and not reproducible from the export.
    """
    cards = read_card_list(list_path)
    path = collection_path(out_dir)
    state, _, existing = collection_state(out_dir)
    if state == COLL_READY and not force:
        sys.exit(
            f"{path.name} already lists {len(existing)} cards — not overwriting it.\n"
            f"  • to add the new cards to it instead:  "
            f"python mtg_deck_importer.py --merge-collection \"{list_path}\"\n"
            f"  • to replace it wholesale (loses any notes/sections you added): "
            f"add --force")
    listing = "\n".join(f"{qty} {disp}"
                        for _key, (qty, disp) in sorted(cards.items()))
    copies = sum(qty for qty, _ in cards.values())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""---
tags: [mtg, collection]
created: {date.today().isoformat()}
project: "{PROJECT_LINK}"
---

# 🗃️ My Card Collection

**Project:** {PROJECT_LINK}

One card per line as `N Card Name`. Only those lines are read — add headings or
notes anywhere you like and they'll be ignored.

{listing}
""", encoding="utf-8")
    replaced = " (replaced)" if state == COLL_READY else ""
    print(f"Collection: wrote {path}{replaced}")
    print(f"           {len(cards)} unique cards ({copies} copies) "
          f"from {Path(list_path).name}")
    refresh_collection_value(out_dir)
    print("Tip:       run --recheck to rebuild every deck's buy lists against it.")


def merge_collection(out_dir: Path, list_path: str) -> None:
    """--merge-collection: diff a full owned-cards export against
    _Collection.md and append what's missing. Append-only — nothing is ever
    removed; cards present in the collection but absent from the new list are
    reported (as prose the parser ignores) for you to prune by hand.
    """
    src = Path(list_path)
    new_cards = read_card_list(list_path)  # key -> [qty, display name]

    collection = load_collection(out_dir)
    owned = collection[1] if collection else {}
    # Original casing for the manual-review report (owned keys are lowercased)
    display: dict[str, str] = {}
    coll_file = collection_path(out_dir)
    if coll_file.is_file():
        for line in coll_file.read_text(encoding="utf-8-sig").splitlines():
            parsed = parse_card_line(line)
            if parsed and re.match(r"\d", line.strip()):
                display.setdefault(parsed[1].lower(), parsed[1])
    additions = []  # (display, qty to add)
    increases = covered = 0
    for key, (qty, disp) in sorted(new_cards.items()):
        have = owned.get(key, 0)
        if have <= 0:
            additions.append((disp, qty))
        elif qty > have:
            additions.append((disp, qty - have))
            increases += 1
        else:
            covered += 1
    only_current = sorted(
        k for k in owned
        if k not in new_cards and k not in BASIC_LAND_NAMES
        and not k.startswith("snow-covered ")
    )

    today = date.today().isoformat()
    section = [f"\n## 📦 Merged from {src.name} ({today})", ""]
    if additions:
        section.extend(f"{qty} {disp}" for disp, qty in additions)
    else:
        section.append("Nothing new — the collection already covered this list.")
    if only_current:
        section.append("")
        section.append(f"⚠️ In the collection but NOT in {src.name} "
                       f"({len(only_current)}) — remove by hand if truly gone:")
        section.append(", ".join(display.get(k, k) for k in only_current))
    section.append("")
    with open(coll_file, "a", encoding="utf-8") as f:
        f.write("\n".join(section))

    print(f"Merged:    {src.name} -> {coll_file.name}")
    print(f"           new cards: {len(additions) - increases}"
          f" | quantity top-ups: {increases}"
          f" | already covered: {covered}")
    if only_current:
        print(f"           in collection only: {len(only_current)}"
              " (kept — listed in the new section for manual review)")
    refresh_collection_value(out_dir)
    print("Tip:       run --recheck to refresh every deck's buy lists.")


def collection_value(out_dir: Path, quiet: bool = False) -> None:
    """Price the whole collection (--collection-value): totals per market and
    a top-20 table, printed to the console and written into _Collection.md as
    a '💰 Collection Value' section (replaced in place on re-runs). Basic
    lands are excluded — the 999-copy sentinel entries would be nonsense.
    """
    state, path, owned = collection_state(out_dir)
    if state != COLL_READY:
        why = ("No collection file at" if state == COLL_MISSING
               else "No 'N Card Name' lines yet in")
        sys.exit(f"{why} {path}\n"
                 "There is nothing to price without it. Create one from a card "
                 "export with:\n"
                 "  python mtg_deck_importer.py --collection \"your-cards.txt\"")
    fname = path.name
    names = [n for n in owned
             if n not in BASIC_LAND_NAMES and not n.startswith("snow-covered ")]
    if not names:
        sys.exit("The collection has no non-basic cards to price.")
    # Price the cards you actually own: the exact printing where the line names
    # one, and the foil price where it's marked ✨ — a foil is often several
    # times its non-foil twin, so pricing everything non-foil undervalues a
    # collection badly.
    entries = [e for e in read_collection_entries(path)
               if e["name"].lower() not in BASIC_LAND_NAMES
               and not e["name"].lower().startswith("snow-covered ")]
    pinned = [e for e in entries if e["set"] and e["num"]]
    if not quiet:
        print(f"Pricing:   {len(names)} unique cards from {fname}"
              f" ({len(pinned)} pinned to an exact printing,"
              f" {sum(1 for e in entries if e['foil'])} foil)...")
    prices = fetch_prices(names)
    exact = fetch_printing_prices(pinned)
    rates = fx_rates()

    totals = {"eur": 0.0, "usd": 0.0, "mp": 0.0}
    rows = []
    unpriced = []
    copies = 0
    for e in entries:
        qty, key = e["qty"], e["name"].lower()
        p = exact.get((e["set"], e["num"])) if e["set"] and e["num"] else None
        p = p or prices.get(key) or {}
        disp = p.get("name", e["name"])
        suffix = "eur_foil" if e["foil"] else "eur"
        eur = p.get(suffix) if e["foil"] else p.get("eur")
        usd = p.get("usd_foil" if e["foil"] else "usd")
        if eur is None and usd is None:  # no foil price listed → fall back
            eur, usd = p.get("eur"), p.get("usd")
        if eur is None and usd is None:
            unpriced.append(disp)
            continue
        copies += qty
        if eur is not None:
            totals["eur"] += eur * qty
        if usd is not None:
            totals["usd"] += usd * qty
        if p.get("mp") is not None:
            totals["mp"] += p["mp"] * qty
        rows.append((qty, disp + (" ✨" if e["foil"] else ""), eur, p.get("mp")))
    rows.sort(key=lambda r: (r[2] or 0) * r[0], reverse=True)

    today = date.today().isoformat()
    market_rows = "\n".join(
        f"| {label} | {native} | {_gbp_cell(gbp)} |"
        for label, native, gbp in [
            ("🇪🇺 Cardmarket", f"€{totals['eur']:,.2f}",
             totals["eur"] * rates["eur_gbp"] if rates else None),
            ("🇺🇸 TCGPlayer", f"${totals['usd']:,.2f}",
             totals["usd"] * rates["usd_gbp"] if rates else None),
            ("🛍️ ManaPool", f"${totals['mp']:,.2f}",
             totals["mp"] * rates["usd_gbp"] if rates else None),
        ])
    def _rows_table(items):
        body = "\n".join(
            f"| {disp}{f' ×{qty}' if qty > 1 else ''} | {_eur_cell(eur)} | "
            f"{_eur_cell((eur or 0) * qty)} | {_gbp_cell((eur or 0) * qty * rates['eur_gbp'] if rates and eur else None)} |"
            for qty, disp, eur, _mp in items)
        return ("| Card | Each | Value | ≈ GBP |\n"
                "|------|-----:|------:|------:|\n" + body)

    unpriced_note = (f"\n\n> ⚠️ No price found for {len(unpriced)} card(s): "
                     + ", ".join(unpriced[:15])
                     + ("…" if len(unpriced) > 15 else "")
                     if unpriced else "")
    headline = (f"£{totals['eur'] * rates['eur_gbp']:,.2f}" if rates
                else f"€{totals['eur']:,.2f}")
    section = f"""## 💰 Collection Value — {headline}

*Cardmarket, priced {today}. {len(rows)}/{len(entries)} non-basic entries priced, {copies} copies across {len(names)} distinct cards. Exact printings and foil prices used where a line records them.*

| Market | Value | ≈ GBP |
|--------|------:|------:|
{market_rows}

### 🏆 Top 5 most valuable

{_rows_table(rows[:5])}

{_callout("📋 Top 20", _rows_table(rows[:20]))}{unpriced_note}
"""
    path = collection_path(out_dir)
    text = path.read_text(encoding="utf-8-sig")
    # Drop any previous block wherever it sat, then place the fresh one at the
    # top — the total is the thing you want to see on opening the note, not
    # something to scroll 400 card lines to reach.
    text = re.sub(r"\n*## 💰 Collection Value[^\n]*\n.*?(?=\n## |\Z)", "",
                  text, count=1, flags=re.S)
    m = re.search(r"^## ", text, re.M)
    body = section.rstrip("\n")
    if m:
        head = text[:m.start()].rstrip("\n")
        text = f"{head}\n\n{body}\n\n{text[m.start():]}"
    else:
        text = text.rstrip("\n") + "\n\n" + body + "\n"
    path.write_text(re.sub(r"\n{3,}", "\n\n", text), encoding="utf-8",
                    newline="\n")

    gbp = f" / GBP {totals['eur'] * rates['eur_gbp']:,.2f}" if rates else ""
    if quiet:  # auto-refresh after an import — one line is enough
        print(f"Value:     collection now ~EUR {totals['eur']:,.2f}{gbp}"
              f" ({path.name} updated)")
        return
    print(f"Value:     ~EUR {totals['eur']:,.2f}{gbp}"
          f" / USD {totals['usd']:,.2f} / MP USD {totals['mp']:,.2f}")
    for q, d, e, _ in rows[:5]:
        print(f"           {d}{f' ×{q}' if q > 1 else ''}"
              f" — ~EUR {(e or 0) * q:,.2f}")
    print(f"Updated:   💰 Collection Value at the top of {path.name}")


def refresh_collection_value(out_dir: Path) -> None:
    """Re-price the collection after the app has changed it, so the value block
    at the top of the note is never stale. Best-effort: an offline or throttled
    run must not fail the import that just succeeded.
    """
    try:
        collection_value(out_dir, quiet=True)
    except SystemExit:
        pass
    except Exception as exc:
        print(f"Value:     could not refresh the value block ({exc})")


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


def fetch_archidekt(url: str) -> dict:
    """Archidekt's deck JSON is public and un-walled — plain HTTP, no browser.

    It carries everything the text export does and more: each card's exact
    printing (edition + collector number), foil modifier and hand-authored
    category, plus which category IS the command zone (isPremier) — so the
    commander needs no first-line convention and the note prices the exact
    versions chosen on the site. Maybeboard/sideboard categories are excluded
    via their includedInDeck flag.
    """
    m = re.search(r"archidekt\.com/decks/(\d+)", url)
    if not m:
        sys.exit(f"Could not read an Archidekt deck id from: {url}")
    r = http("GET", ARCHIDEKT_API.format(deck_id=m.group(1)))
    r.raise_for_status()
    d = r.json()
    cats = {c["name"]: c for c in d.get("categories") or []}

    commanders: list[str] = []
    mainboard: dict[str, int] = {}
    pins: dict[str, dict] = {}
    tokens: list[dict] = []
    for c in d.get("cards") or []:
        name = c["card"]["oracleCard"]["name"]
        card_cats = c.get("categories") or []
        ed = c["card"].get("edition") or {}
        pin = {"set": (ed.get("editioncode") or "").lower() or None,
               "num": str(c["card"].get("collectorNumber") or "") or None,
               # Etched foils get the plain foil price — Scryfall's EUR data
               # doesn't price etched separately, and close beats absent
               "foil": c.get("modifier") in ("Foil", "Etched"),
               "cat": card_cats[0] if card_cats else None}
        # Token cards (the site's "Tokens & Extras") aren't part of the 100 —
        # Archidekt flags their category includedInDeck: False — but they ARE
        # real purchasable cards, so they get their own off-totals section
        if c["card"]["oracleCard"].get("layout") in (
                "token", "double_faced_token", "emblem"):
            tokens.append({"qty": c.get("quantity", 1), "name": name,
                           "set": pin["set"], "num": pin["num"]})
            continue
        # A card's FIRST category is its home — a Maybeboard card stays out
        # of the deck even when it also carries other tags (the site's deck
        # size counts it that way); uncategorised cards are in the deck
        primary = cats.get(card_cats[0], {}) if card_cats else {}
        if not primary.get("includedInDeck", True):
            continue
        if primary.get("isPremier"):
            commanders.append(name)
        else:
            mainboard[name] = mainboard.get(name, 0) + c.get("quantity", 1)
        if (pin["set"] and pin["num"]) or pin["foil"] or pin["cat"]:
            pins.setdefault(name.lower(), pin)
    if not commanders:
        sys.exit("No commander found on this Archidekt deck — is a category "
                 "marked as the command zone on the site?")
    return {
        "name": d.get("name") or f"Archidekt deck {m.group(1)}",
        "format": "Commander",
        "source_md": f"[Archidekt]({url})",
        "commanders": sorted(commanders),
        "mainboard": [(q, n) for n, q in mainboard.items()],
        "pins": pins,
        "tokens": sorted(tokens, key=lambda t: t["name"].lower()),
    }


def fetch_textfile(path_str: str) -> dict:
    """A local decklist in the standard export format: one 'N Card Name' per
    line, first card is the commander. Deck name = file name without .txt.

    Lines may carry Archidekt-style decorations — `1x Name (set) 405 *F*
    [Removal]` — which are kept as "pins": the note then prices and pictures
    the exact printing the file names (at its foil price when marked *F*)
    instead of Scryfall's default, and the category tag rides along as
    reference. Bare `1 Name` lines behave exactly as before.
    """
    path = Path(path_str)
    cards: list[tuple[int, str]] = []
    pins: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        parsed = parse_card_line_full(line)
        if not parsed:
            continue
        qty, name, set_code, num, foil, cat = parsed
        cards.append((qty, name))
        if (set_code and num) or foil or cat:
            # First line wins when an export splits a card across printings
            pins.setdefault(name.lower(), {"set": set_code, "num": num,
                                           "foil": foil, "cat": cat})
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
        "pins": pins,
        "txt_path": str(path),
    }


FETCHERS = [
    (re.compile(r"moxfield\.com/decks/"), fetch_moxfield),
    (re.compile(r"edhrec\.com/deckpreview"), fetch_edhrec),
    (re.compile(r"archidekt\.com/decks/"), fetch_archidekt),
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

def commander_color_suffix(commanders: list[str]) -> str:
    """The deck's colour identity as emoji circles — the union of every
    commander's color_identity, in WUBRG order. Looked up live (the bulk DB
    doesn't store colour identity; two small requests at most). A failed
    lookup returns '' loudly rather than aborting a whole import over a
    cosmetic suffix.
    """
    identity: set[str] = set()
    for name in commanders:
        card = None
        # Full name first — 'SP//dr, Piloted by Peni' has // INSIDE its name —
        # then the front face, which is how double-faced legends resolve.
        for ask in dict.fromkeys((name, name.split("//")[0].strip())):
            r = http("GET", SCRYFALL_NAMED, params={"exact": ask})
            if r.ok:
                card = r.json()
                break
        if card is None:
            print(f"Colours:   Scryfall lookup failed for '{name}' — "
                  "no colour suffix added")
            return ""
        identity.update(card.get("color_identity") or [])
    return ("".join(COLOR_EMOJI[c] for c in "WUBRG" if c in identity)
            or COLORLESS_EMOJI)


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

    No ManaPool here: deck notes price against Cardmarket, so a deck run never
    pays for the ~50 MB catalog download. --collection-value still uses it (see
    fetch_printing_prices), where an exact-printing US listing is the point.
    """
    prices: dict[str, dict] = {}
    for i in range(0, len(names), 75):  # collection endpoint caps at 75 cards
        chunk = names[i:i + 75]
        # The collection endpoint matches a FACE name, not the combined
        # "Front // Back" one a decklist carries — asking for the full name puts
        # every double-faced card, transforming legend and adventure creature in
        # not_found, which used to drop them out of 📊 Deck Shape and leave
        # --brief with no oracle text for them. Ask by front face for those.
        ask = [n.split("//")[0].strip() if "//" in n else n for n in chunk]
        r = http("POST", SCRYFALL_COLLECTION,
                 json={"identifiers": [{"name": n} for n in ask]})
        r.raise_for_status()
        # front-face name (as asked) -> full decklist name (as written)
        asked_as = {a.lower(): n for a, n in zip(ask, chunk) if a != n}
        for card in r.json().get("data", []):
            p = card.get("prices", {})
            faces = card.get("card_faces") or []
            oracle = card.get("oracle_text") or " // ".join(
                f.get("oracle_text", "") for f in faces)
            entry = {
                "name": card["name"],  # canonical spelling, saves a lookup later
                "eur": float(p["eur"]) if p.get("eur") else None,
                "usd": float(p["usd"]) if p.get("usd") else None,
                "tix": float(p["tix"]) if p.get("tix") else None,
                # which printing this price is for, so a buy list can name it
                "set": card.get("set"),
                "num": str(card.get("collector_number", "")),
                "img": scryfall_card_image(card),
                # for 📊 Deck Shape and --brief — no extra requests
                "type": card.get("type_line", ""),
                "cmc": card.get("cmc"),
                "oracle": oracle,
                "released": card.get("released_at", ""),
            }
            prices[card["name"].lower()] = entry
            # Let a front-face name find its double-faced card
            if "//" in card["name"]:
                front = card["name"].split("//")[0].strip().lower()
                prices.setdefault(front, entry)
                # ...and let the name the decklist actually wrote find it too,
                # even when that differs from Scryfall's combined name
                if front in asked_as:
                    prices.setdefault(asked_as[front].lower(), entry)

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
            # Merge over the original entry, never replace it: the cheapest-print
            # lookup only knows prices, so a rebuilt-from-scratch dict would lose
            # type/cmc/oracle and quietly drop the card out of 📊 Deck Shape's
            # type counts, mana curve and role tags. Basics are the common case
            # (their collection printing is usually unpriced), which is how a
            # 38-land deck came to report "10 lands".
            prices[name.lower()] = {
                **(entry or {}),
                "name": info["canonical"],
                "eur": cheap.get("eur"),
                "usd": cheap.get("usd"),
                "tix": entry["tix"] if entry else None,
                "set": cheap.get("set_code"), "num": cheap.get("num"),
                "img": cheap.get("img") or (entry or {}).get("img"),
            }
    return prices


def _pin_entry(entry: dict, pin: dict, row: dict) -> None:
    """Overwrite one deck price entry with its pinned printing's numbers.
    Foil pins take the foil price where one is listed — a foil often costs
    several times its twin, and the Cheapest Build's savings should show what
    the shiny copy really costs. A foil with no listed foil price falls back
    to the non-foil price rather than reading as unpriced.
    """
    foil = bool(pin.get("foil"))
    eur = (row.get("eur_foil") if foil and row.get("eur_foil") is not None
           else row.get("eur"))
    usd = (row.get("usd_foil") if foil and row.get("usd_foil") is not None
           else row.get("usd"))
    if eur is None and usd is None and \
            (entry.get("eur") is not None or entry.get("usd") is not None):
        # The pinned printing has no market price at all — usually a
        # digital-only version or a site-internal promo id (Archidekt's
        # `(prm) 82852`-style). A silently unpriced row helps nobody, so keep
        # the default printing's price and say so.
        print(f"Pin:       {entry.get('name', '?')} ({pin['set'].upper()}) "
              f"{pin['num']} has no market price — keeping the default printing")
        return
    entry.update({"eur": eur, "usd": usd, "set": pin["set"],
                  "num": str(pin["num"]), "foil": foil,
                  "img": row.get("image_uri") or entry.get("img")})


def apply_pins(prices: dict[str, dict], pins: dict[str, dict] | None) -> None:
    """Re-point deck prices at the exact printings an import named.

    Where an import pinned a card — `1x Blade of Selves (c15) 51` — the
    deck's own-version price, gallery image and Buy List pin describe THAT
    printing, not Scryfall's default (which is just the newest reprint and
    silently changes art every time a card is reprinted). Cards without a pin
    keep the default behaviour, and the Cheapest Build is untouched either
    way — it still hunts every printing.
    """
    if not pins:
        return
    want = {n: p for n, p in pins.items()
            if p.get("set") and p.get("num") and n in prices}
    # A foil marker without a printing pin can't be priced as a specific foil,
    # but the buy list should still say the deck wants the shiny one
    for n, p in pins.items():
        if p.get("foil") and n in prices and n not in want:
            prices[n]["foil"] = True
    db = scryfall_db()
    misses: list[tuple[str, dict]] = []
    for name, pin in want.items():
        row = db.execute(
            "SELECT name, eur, usd, eur_foil, usd_foil, image_uri "
            "FROM cards WHERE set_code = ? AND collector_number = ?",
            (pin["set"].lower(), str(pin["num"]))).fetchone() \
            if db is not None else None
        if row is None:
            misses.append((name, pin))
        else:
            _pin_entry(prices[name], pin, dict(row))
    # API fallback: --no-bulk runs, or a printing newer than the bulk snapshot
    for i in range(0, len(misses), 75):
        chunk = misses[i:i + 75]
        r = http("POST", SCRYFALL_COLLECTION,
                 json={"identifiers": [
                     {"set": p["set"], "collector_number": str(p["num"])}
                     for _, p in chunk]})
        if r.status_code != 200:
            continue  # those pins keep their by-name default prices
        by_key = {(c["set"].lower(), str(c["collector_number"])): c
                  for c in r.json().get("data", [])}
        for name, pin in chunk:
            card = by_key.get((pin["set"].lower(), str(pin["num"])))
            if card is None:
                print(f"Pin:       {prices[name]['name']} "
                      f"({pin['set'].upper()}) {pin['num']} not found — "
                      "using the default printing")
                continue
            p = card.get("prices", {})
            _pin_entry(prices[name], pin, {
                "eur": float(p["eur"]) if p.get("eur") else None,
                "usd": float(p["usd"]) if p.get("usd") else None,
                "eur_foil": float(p["eur_foil"]) if p.get("eur_foil") else None,
                "usd_foil": float(p["usd_foil"]) if p.get("usd_foil") else None,
                "image_uri": scryfall_card_image(card),
            })


def fetch_printing_prices(entries: list[dict]) -> dict[tuple[str, str], dict]:
    """Prices for exact printings, keyed (set code, collector number).

    Served from the reference DB, which is keyed on exactly this pair. The API
    path below is the fallback: its collection endpoint takes set/collector-
    number identifiers, so a whole shelf costs one request per 75.
    """
    out: dict[tuple[str, str], dict] = {}
    want = sorted({(e["set"], e["num"]) for e in entries})
    db = scryfall_db()
    if db is not None:
        for set_code, num in want:
            r = db.execute(
                "SELECT name, eur, usd, eur_foil, usd_foil FROM cards "
                "WHERE set_code = ? AND collector_number = ?",
                (set_code.lower(), str(num))).fetchone()
            if r is None:
                continue
            out[(set_code.lower(), str(num))] = {
                "name": r["name"], "eur": r["eur"], "usd": r["usd"],
                "eur_foil": r["eur_foil"], "usd_foil": r["usd_foil"],
                "mp": manapool_index().get(r["name"].lower()),
            }
        want = [w for w in want if (w[0].lower(), str(w[1])) not in out]
        if not want:
            return out
    for i in range(0, len(want), 75):
        chunk = want[i:i + 75]
        r = http("POST", SCRYFALL_COLLECTION,
                 json={"identifiers": [{"set": s, "collector_number": n}
                                       for s, n in chunk]})
        if r.status_code != 200:
            continue  # fall back to the by-name prices for this chunk
        for card in r.json().get("data", []):
            p = card.get("prices", {})
            out[(card["set"].lower(), str(card["collector_number"]))] = {
                "name": card["name"],
                "eur": float(p["eur"]) if p.get("eur") else None,
                "usd": float(p["usd"]) if p.get("usd") else None,
                "eur_foil": float(p["eur_foil"]) if p.get("eur_foil") else None,
                "usd_foil": float(p["usd_foil"]) if p.get("usd_foil") else None,
                "mp": manapool_index().get(card["name"].lower()),
            }
    return out


def fetch_card_images(names: list[str],
                      pins: dict[str, dict] | None = None
                      ) -> dict[str, str | None]:
    """Card image URLs only, via Scryfall's collection endpoint. Used by
    --reimport to (re)build galleries without pulling fresh market prices —
    the response carries prices too, but they are ignored here. Pinned cards
    are requested by set/collector number so the gallery shows the printing
    the import named, not Scryfall's default.
    """
    idents = []
    for n in names:
        pin = (pins or {}).get(n.lower()) or {}
        if pin.get("set") and pin.get("num"):
            idents.append({"set": pin["set"],
                           "collector_number": str(pin["num"])})
        else:
            idents.append({"name": n})
    imgs: dict[str, str | None] = {}
    for i in range(0, len(idents), 75):  # collection endpoint caps at 75 cards
        chunk = idents[i:i + 75]
        r = http("POST", SCRYFALL_COLLECTION, json={"identifiers": chunk})
        r.raise_for_status()
        for card in r.json().get("data", []):
            img = scryfall_card_image(card)
            imgs[card["name"].lower()] = img
            if "//" in card["name"]:
                imgs.setdefault(card["name"].split("//")[0].strip().lower(), img)
    return imgs


FX_CACHE = SCRIPT_DIR / ".cache" / "fx_rates.json"


@lru_cache(maxsize=1)
def fx_rates() -> dict | None:
    """Current ECB reference rates via frankfurter.dev: EUR→GBP and USD→GBP
    (derived through EUR). Memoised so a --recheck over many notes fetches once.

    The last good rates are cached on disk and reused when the API is down. A
    transient outage otherwise silently strips every £ figure from every note
    and leaves the _Decks.md totals reading '£0.00' — badly wrong rather than
    merely absent. Rates move by fractions of a percent a day, so a stale one
    is far better than none. Returns None only if the API fails with no cache.
    """
    try:
        r = SESSION.get(ECB_RATES, params={"base": "EUR", "symbols": "GBP,USD"},
                        timeout=15)
        r.raise_for_status()
        rates = r.json()["rates"]
        fresh = {"eur_gbp": float(rates["GBP"]),
                 "usd_gbp": float(rates["GBP"]) / float(rates["USD"]),
                 "date": r.json().get("date", date.today().isoformat())}
        try:
            FX_CACHE.parent.mkdir(exist_ok=True)
            FX_CACHE.write_text(json.dumps(fresh), encoding="utf-8")
        except OSError:
            pass
        return fresh
    except (requests.RequestException, ValueError, KeyError, ZeroDivisionError):
        pass
    try:
        cached = json.loads(FX_CACHE.read_text(encoding="utf-8"))
        print(f"FX rates:  frankfurter.dev unreachable — using cached rates "
              f"from {cached.get('date', 'an earlier run')}")
        return {"eur_gbp": float(cached["eur_gbp"]),
                "usd_gbp": float(cached["usd_gbp"]),
                "date": cached.get("date")}
    except (OSError, ValueError, KeyError):
        print("FX rates:  unavailable and never cached — £ columns will show "
              "as '—' this run")
        return None


def price_report(decklist: list[tuple[int, str]], prices: dict[str, dict]) -> dict:
    totals = {"eur": 0.0, "usd": 0.0, "tix": 0.0}
    coverage = {"eur": 0, "usd": 0, "tix": 0}
    unpriced = []
    all_cards = []
    for qty, name in decklist:
        p = prices.get(name.lower())
        img = (p or {}).get("img")
        if not p or (p["eur"] is None and p["usd"] is None):
            unpriced.append(name)
            all_cards.append((qty, name, None, None, img))
            continue
        for src in ("eur", "usd", "tix"):
            if p.get(src) is not None:
                totals[src] += p[src] * qty
                coverage[src] += 1
        all_cards.append((qty, name, p["eur"], p["usd"], img))
    all_cards.sort(key=lambda c: c[2] or 0, reverse=True)
    return {"totals": totals, "coverage": coverage, "unique": len(decklist),
            "all": all_cards, "unpriced": unpriced,
            "rates": fx_rates()}


REVIEW_SECTIONS = ["🧠 First Impressions", "💪 Strengths", "⚠️ Weaknesses",
                   "🔄 Cards to Consider Swapping", "📝 Play Notes",
                   "🧭 Deck Guide"]

# Retired stubs: no longer created on a fresh note (the 🧭 Deck Guide covers
# this ground), but still read back and re-emitted in place if an existing note
# has prose under them, so a rebuild never eats what you wrote.
RETIRED_REVIEW_SECTIONS = {"💪 Strengths", "⚠️ Weaknesses",
                           "🔄 Cards to Consider Swapping", "📝 Play Notes"}

# Analysis prose — written per deck (by you, or by Claude via the
# /analyse-deck skill) and preserved across rebuilds exactly like reviews.
ANALYSIS_SECTIONS = ["🎮 Play Pattern", "🏆 Win Conditions",
                     "⚠️ Interactions & Warnings"]

# 🗂️ Contents — a table of the note's own sections, rebuilt from the finished
# note on every write, so it can never list a section that isn't there. Sits
# directly under the commander art, above the first section.
TOC_HEADING = "🗂️ Contents"
TOC_BLURBS = {
    "🧠 First Impressions": "Your first read on the deck",
    "💪 Strengths": "What it does well",
    "⚠️ Weaknesses": "Where it falls over",
    "🔄 Cards to Consider Swapping": "Swap ideas",
    "📝 Play Notes": "What actually happened at the table",
    "🧭 Deck Guide": "Full strategy guide: every card by role, play pattern, "
                     "bracket, budget swaps, upgrade path",
    "📊 Deck Shape": "Type counts, mana curve, role tags, bracket checklist "
                     "*(computed — refreshes on every recheck)*",
    "🎮 Play Pattern": "How the turns go",
    "🏆 Win Conditions": "How it actually closes a game",
    "⚠️ Interactions & Warnings": "Rules traps and anti-synergies",
    "💰 Card Prices": "Every card dearest-first, plus the full card gallery",
    "🎟️ Tokens & Extras": "Tokens the deck needs — kept off the deck total",
    "📜 Deck List": "The list as built, for pasting back into a deck site",
    "🛒 Cards to Complete the Deck": "What's still missing, at the deck's own "
                                     "printings",
    "💸 Cheapest Build": "The same deck with every card at its cheapest "
                         "printing",
    "🛒 Cards to Complete — Cheapest Build": "What's still missing, at those "
                                             "cheapest printings",
    "💰 Collection Value": "What the collection is worth",
}


def render_toc(text: str) -> str:
    """Build the 🗂️ Contents table by reading the finished note's own `##`
    headings, so optional sections (🎟️ Tokens & Extras, the 🛒 buy lists) are
    listed only when the note actually has them. Hand-written sections that are
    still `-` are marked empty — that's the part worth seeing at a glance.
    """
    written_by_hand = set(REVIEW_SECTIONS) | set(ANALYSIS_SECTIONS)
    rows = []
    for m in re.finditer(r"^## (.+)$", text, re.M):
        heading = m.group(1).strip()
        if heading == TOC_HEADING:
            continue
        blurb = TOC_BLURBS.get(heading, "")
        if heading in written_by_hand:
            rest = text[m.end():]
            end = re.search(r"\n## ", rest)
            body = (rest[:end.start()] if end else rest).strip()
            if not body or body == "-":
                blurb = f"{blurb} — ✍️ *empty*" if blurb else "✍️ *empty*"
        rows.append(f"| [[#{heading}]] | {blurb} |")
    if not rows:
        return ""
    return (f"## {TOC_HEADING}\n\n| Section | What's in it |\n"
            "|---------|--------------|\n" + "\n".join(rows))


def insert_toc(text: str) -> str:
    """Drop any existing 🗂️ Contents block and place a freshly built one under
    the commander art, above the first section. Idempotent, so every write path
    can call it — including the `--reimport` splice, which never rebuilds the
    note from the template.
    """
    # \r?-tolerant throughout: build_note passes LF text, but a caller working on
    # a note already written to disk gets CRLF (write_text translates newlines on
    # Windows). An LF-only pattern silently fails to find the old block there and
    # leaves the note with two Contents tables.
    text = re.sub(rf"(\r?\n)*## {re.escape(TOC_HEADING)}\r?\n.*?(?=\r?\n## |\Z)",
                  "", text, count=1, flags=re.S)
    toc = render_toc(text)
    first = re.search(r"^## ", text, re.M)
    if not toc or not first:
        return text
    # Join with whatever the text already uses, so a CRLF note doesn't come back
    # with an LF block spliced into the middle of it
    nl = "\r\n" if "\r\n" in text else "\n"
    toc = toc.replace("\n", nl)
    head = text[:first.start()].rstrip("\r\n")
    return f"{head}{nl}{nl}{toc}{nl}{nl}{text[first.start():]}"

# WotC's Commander Bracket "Game Changers" list — best-effort snapshot
# (April 2025 update); refresh from the official list when brackets change.
GAME_CHANGERS = {n.lower() for n in [
    "Ancient Tomb", "Aura Shards", "Bolas's Citadel", "Chrome Mox",
    "Cyclonic Rift", "Demonic Tutor", "Drannith Magistrate",
    "Enlightened Tutor", "Expropriate", "Field of the Dead",
    "Fierce Guardianship", "Force of Will", "Gaea's Cradle", "Glacial Chasm",
    "Grand Arbiter Augustin IV", "Grim Monolith", "Humility", "Imperial Seal",
    "Intuition", "Jeska's Will", "Jin-Gitaxias, Core Augur",
    "Kinnan, Bonder Prodigy", "Lion's Eye Diamond", "Mana Drain", "Mana Vault",
    "Mishra's Workshop", "Mox Diamond", "Mystical Tutor",
    "Narset, Parter of Veils", "Necropotence", "Notion Thief",
    "Opposition Agent", "Orcish Bowmasters", "Rhystic Study",
    "Serra's Sanctum", "Smothering Tithe", "Survival of the Fittest",
    "Teferi's Protection", "Tergrid, God of Fright", "Thassa's Oracle",
    "The One Ring", "The Tabernacle at Pendrell Vale", "Underworld Breach",
    "Urza, Lord High Artificer", "Vampiric Tutor", "Vorinclex, Voice of Hunger",
    "Winota, Joiner of Forces", "Yuriko, the Tiger's Shadow",
]}

# Oracle-text keyword heuristics for the Deck Shape role table — first match
# wins, lands are never tagged. Crude by design (~85% right, zero cost).
ROLE_PATTERNS = [
    ("🔁 Blink", r"exile[^.]*return[^.]*battlefield"),
    ("💥 Board wipes", r"(destroy|exile|return) (all|each)"),
    ("🚫 Counterspells", r"counter target"),
    ("🛃 Removal", r"(destroy|exile) target"),
    ("📚 Draw", r"draw (a|one|two|three|that many) card"),
    ("💎 Mana rocks", r"\{t\}: add \{"),
    ("🌱 Ramp", r"search your library for .* land"),
    ("🛡️ Protection", r"(hexproof|indestructible|protection from)"),
]


def _eur_cell(amount):
    return f"€{amount:,.2f}" if amount is not None else "—"


def _gbp_cell(amount):
    return f"£{amount:,.2f}" if amount is not None else "—"


def _usd_cell(amount):
    return f"${amount:,.2f}" if amount is not None else "—"


def _sane_cheaper(cheap_eur, deck_eur):
    """Is this printing actually worth swapping to? Junk-data protection now
    lives in _pick_cheapest, which judges a printing against its siblings
    rather than against the deck's version — so all that's left here is
    "cheaper by more than rounding".
    """
    if cheap_eur is None:
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
    buy: {lowercased deck name: {printed, set_name, set_code, eur, img,
    deck_eur, changed}} — the single source of truth used by the Shopping
    List, Cheaper Printings and Cheapest Build sections, so their totals
    always agree.

    Every card is checked, however cheap. The old €0.50 floor existed because
    each lookup cost an API call; against the local reference DB a €0.02
    Guildgate costs the same as a €30 fetchland, and skipping the cheap ones
    was quietly leaving money on the table.

    Everything here is Cardmarket EUR: it is the market the pinned `(SET) 123`
    printing is actually priced in, so the chosen version and its price always
    describe the same card.
    """
    choices: dict[str, dict] = {}
    for _qty, name in decklist:
        p = prices.get(name.lower()) or {}
        deck_eur = p.get("eur")
        c = {"printed": name, "set_name": None, "set_code": None,
             "eur": deck_eur, "img": p.get("img"),
             "deck_eur": deck_eur, "changed": False}
        ch = card_prints_info(name, p.get("name")).get("cheapest")
        if ch:
            # Always pin the printing, even when it is the one the deck's own
            # price already referred to. A bare "1 Smoke" sends you to a search
            # listing Alpha at €499 and Beta at €90 — the €4.90 Fourth Edition
            # this line is quoting is only findable by its id.
            printed = ch["printed_as"] \
                if ch["printed_as"].lower() != name.lower() else name
            c.update(printed=printed, set_name=ch["set"],
                     set_code=(ch.get("set_code")
                               or set_code_map().get(ch["set"].lower())),
                     num=ch.get("num"),
                     eur=ch["eur"], img=ch.get("img") or c["img"],
                     # "changed" drives the swap labelling and the Save column,
                     # so it stays about price, not about whether we pinned
                     changed=_sane_cheaper(ch["eur"], deck_eur))
        choices[name.lower()] = c
    return choices


def _choice_line(qty: int, choice: dict | None, name: str) -> str:
    """A copy-paste decklist line for a chosen version — `(SET) 123` pins the
    exact printing in MTG Arena syntax, which Moxfield and most store
    decklist finders understand. Without a collector number, bare `(SET)`
    confuses store parsers, so the code is only added when both are known.
    """
    if not choice:
        return f"{qty} {name}"
    code, num = choice.get("set_code"), choice.get("num")
    if code and num:
        return f"{qty} {choice['printed']} ({code.upper()}) {num}"
    return f"{qty} {choice['printed']}"


def cheapest_buy(buy: dict, choices: dict[str, dict],
                 rates: dict | None) -> dict:
    """The missing cards priced at their cheapest versions: totals plus the
    copy-paste Budget Buy List lines (alphabetical, like a store checklist).

    `unpriced` counts cards Cardmarket has no price for at all — they
    contribute nothing to the total, so the sections say so rather than
    quietly understating what finishing the deck costs.
    """
    totals = {"eur": 0.0, "gbp": 0.0}
    lines = []
    unpriced = 0
    for m in sorted(buy["missing"], key=lambda m: m["name"].lower()):
        c = choices.get(m["name"].lower())
        eur = c["eur"] if c else m["eur"]
        if eur is None:
            unpriced += 1
        totals["eur"] += (eur or 0) * m["need"]
        if rates and eur is not None:
            totals["gbp"] += eur * rates["eur_gbp"] * m["need"]
        lines.append(_choice_line(m["need"], c, m["name"]))
    return {"totals": totals, "lines": lines, "unpriced": unpriced}


HISTORY_FILE = ".price-history.json"  # lives in the vault → syncs everywhere


def _load_history(out_dir: Path) -> dict:
    try:
        return json.loads((out_dir / HISTORY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record_history(out_dir: Path, deck_id: int, deck_name: str, report: dict,
                   buy: dict | None, cheap: dict | None,
                   choices: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """Append today's price snapshot for a deck to the vault's history file
    (one entry per day — a same-day re-run overwrites) and compare against
    the previous check: returns (all entries, alert lines). Alerts flag a
    notable drop in the cost to finish and per-card crashes that usually
    mean a reprint — the 'time to buy' signals.
    """
    hist = _load_history(out_dir)
    key = str(deck_id)
    prior = hist.get(key, {})
    old_entries = prior.get("entries", [])
    watch_prev = prior.get("watch", {})
    today = date.today().isoformat()

    entry = {"date": today, "value_eur": round(report["totals"]["eur"], 2)}
    if buy is not None:
        entry["buy_eur"] = round(buy["totals"]["eur"], 2)
    if cheap is not None:
        entry["cheapest_eur"] = round(cheap["totals"]["eur"], 2)
    prev = next((e for e in reversed(old_entries) if e["date"] != today), None)
    entries = [e for e in old_entries if e["date"] != today] + [entry]

    # Watch the pricey missing cards so a reprint-driven crash is noticed
    watch: dict[str, dict] = {}
    if buy is not None:
        for m in buy["missing"]:
            c = choices.get(m["name"].lower()) or {}
            eur = c.get("eur") or m["eur"]
            if eur and eur >= 3:
                watch[m["name"]] = {"eur": round(eur, 2),
                                    "set": c.get("set_name")}
        watch = dict(sorted(watch.items(),
                            key=lambda kv: -kv[1]["eur"])[:15])

    alerts = []
    if prev and prev.get("cheapest_eur") and entry.get("cheapest_eur") is not None:
        old, new = prev["cheapest_eur"], entry["cheapest_eur"]
        diff = new - old
        if abs(diff) >= 0.01:
            alerts.append(f"History:   cost to finish (cheapest) €{old:,.2f} → "
                          f"€{new:,.2f} ({diff:+,.2f}) since {prev['date']}")
        if diff <= -5 and new <= old * 0.95:
            alerts.append(f"📉 Notable drop — this deck is €{-diff:,.2f} cheaper "
                          "to finish than last check!")
    for name, w in watch_prev.items():
        now = watch.get(name)
        if now and now["eur"] <= w["eur"] * 0.75 and w["eur"] - now["eur"] >= 3:
            setmsg = (f" — now cheapest in {now['set']}"
                      if now.get("set") and now["set"] != w.get("set") else "")
            alerts.append(f"💥 {name}: cheapest €{w['eur']:,.2f} → "
                          f"€{now['eur']:,.2f}{setmsg} (reprint or price crash?)")

    hist[key] = {"name": deck_name, "entries": entries[-60:], "watch": watch}
    try:
        (out_dir / HISTORY_FILE).write_text(json.dumps(hist), encoding="utf-8")
    except OSError:
        pass
    return entries, alerts


def render_history(entries: list[dict]) -> str:
    """A collapsed '📉 Price History' callout: one row per price check, with
    the overall cost-to-finish trend in the title.
    """
    if not entries:
        return ""

    def cell(e, k):
        return f"€{e[k]:,.2f}" if e.get(k) is not None else "—"

    rows = "\n".join(
        f"| {e['date']} | {cell(e, 'value_eur')} | {cell(e, 'buy_eur')} | "
        f"{cell(e, 'cheapest_eur')} |" for e in entries[-8:])
    trend = ""
    first, last = entries[0], entries[-1]
    if len(entries) >= 2 and first.get("cheapest_eur") and \
            last.get("cheapest_eur") is not None:
        pct = (last["cheapest_eur"] - first["cheapest_eur"]) \
            / first["cheapest_eur"] * 100
        trend = f" — cheapest finish {pct:+.0f}% since {first['date']}"
    n = len(entries)
    body = f"""Deck value and cost to finish at each price check (last 8 shown, newest last).

| Date | Deck € | Finish € | Cheapest € |
|------|-------:|---------:|-----------:|
{rows}"""
    return _callout(f"📉 Price History ({n} check{'s' if n != 1 else ''}{trend})",
                    body)


def deck_shape(decklist: list[tuple[int, str]],
               prices: dict[str, dict]) -> dict:
    """Locally computed deck stats from the card data the price fetch already
    returned: type counts, mana curve, keyword role buckets and the bracket
    checklist facts. No extra network, no AI — pure counting.
    """
    types: dict[str, int] = {}
    curve: dict[int, int] = {}
    roles: dict[str, list[str]] = {label: [] for label, _ in ROLE_PATTERNS}
    gc, extra_turns, land_denial = [], [], []
    untyped: list[str] = []
    for qty, name in decklist:
        p = prices.get(name.lower()) or {}
        tline = (p.get("type") or "").split("//")[0]
        if not tline:
            # No type line means this card is invisible to every count below.
            # Say so rather than shipping a quietly wrong "Types:" line.
            untyped.append(f"{qty}x {name}")
        for major in ("Creature", "Planeswalker", "Battle", "Instant",
                      "Sorcery", "Artifact", "Enchantment", "Land"):
            if major in tline:
                types[major] = types.get(major, 0) + qty
                break
        canon = p.get("name", name)
        if canon.lower() in GAME_CHANGERS or name.lower() in GAME_CHANGERS:
            gc.append(canon)
        if "Land" in tline:
            continue
        if p.get("cmc") is not None:
            b = min(int(p["cmc"]), 7)
            curve[b] = curve.get(b, 0) + qty
        text = (p.get("oracle") or "").lower()
        if "extra turn" in text:
            extra_turns.append(canon)
        if re.search(r"destroy all lands|lands don't untap", text):
            land_denial.append(canon)
        for label, pat in ROLE_PATTERNS:
            if re.search(pat, text):
                roles[label].append(name)
                break
    if untyped:
        print(f"Shape:     ⚠️  {len(untyped)} card(s) had no type line and are "
              f"missing from the Deck Shape counts: {', '.join(untyped)}")
    return {"types": types, "curve": curve, "roles": roles, "gc": gc,
            "extra_turns": extra_turns, "land_denial": land_denial}


def render_deck_shape(shape: dict) -> str:
    types = shape["types"]
    type_line = " · ".join(
        f"{types[t]} {t.lower()}{'s' if types[t] != 1 else ''}"
        for t in ("Creature", "Instant", "Sorcery", "Artifact", "Enchantment",
                  "Planeswalker", "Battle", "Land") if types.get(t))
    curve = shape["curve"]
    cols = range(0, 8)
    curve_head = " | ".join(str(c) if c < 7 else "7+" for c in cols)
    curve_row = " | ".join(str(curve.get(c, 0)) for c in cols)
    role_rows = "\n".join(
        f"| {label} | {len(cards)} | {', '.join(cards[:10])}"
        f"{'…' if len(cards) > 10 else ''} |"
        for label, cards in shape["roles"].items() if cards)
    gc = shape["gc"]
    checklist = [
        ("✅" if not gc else "⚠️") + f" **{len(gc)} Game Changer(s)**"
        + (f": {', '.join(gc)}" if gc else "")
        + " *(best-effort snapshot of the official list)*",
        ("✅ No extra-turn cards" if not shape["extra_turns"] else
         f"⚠️ Extra turns: {', '.join(shape['extra_turns'])}"),
        ("✅ No mass land denial detected" if not shape["land_denial"] else
         f"⚠️ Possible mass land denial: {', '.join(shape['land_denial'])}"),
    ]
    checks = "\n".join(f"- {c}" for c in checklist)
    return f"""## 📊 Deck Shape

*Computed from card data — refreshes with every recheck. Role tags are oracle-text keyword matches (~85% right); lands untagged.*

**Types:** {type_line}

| Mana value | {curve_head} |
|------------|{"|".join(["---:"] * 8)}|
| Cards | {curve_row} |

| Role | # | Cards |
|------|--:|-------|
{role_rows}

**Bracket checklist** *(guideline, not a ruling — combos/tutors need human judgement)*:
{checks}"""


# ---------------------------------------------------------------------------
# Set collection tracker (--set) — collecting a whole set, one card at a time
#
# This is deliberately NOT a deck: no commander, no curve, no bracket. It is a
# long-lived checklist, so the note stores progress as Obsidian checkboxes and
# a refresh preserves every tick while re-pricing everything around them.
#
# One line per distinct card (cheapest printing anywhere in the chosen sets),
# not one per printing: collecting every borderless/showcase/foil variant of a
# premium set runs to five figures, which isn't a collection, it's a mortgage.
# ---------------------------------------------------------------------------

# Shorthands for "the whole of product X", because nobody should have to retype
# eight set codes to refresh a list. Key → (set codes, default label).
SET_PRESETS = {
    "ff": (["fin", "fic", "fca", "pfin", "afin", "afic", "tfin", "tfic"],
           "Final Fantasy"),
    "marvel": (["msh", "msc", "mar", "lmar", "amsh", "fmsc", "tmsh", "tmsc"],
               "Marvel Super Heroes"),
    "spiderman": (["spm", "spe", "pspm", "aspm", "tspm"],
                  "Marvel's Spider-Man"),
}
SET_PRESETS["finalfantasy"] = SET_PRESETS["final-fantasy"] = SET_PRESETS["ff"]
SET_PRESETS["msh"] = SET_PRESETS["superheroes"] = SET_PRESETS["marvel"]
SET_PRESETS["spider-man"] = SET_PRESETS["spm"] = SET_PRESETS["spiderman"]


def collection_notes(out_dir: Path) -> list[Path]:
    """Every set-collection checklist note in the vault, found by its
    `set-codes:` frontmatter rather than a filename pattern — so notes rename
    freely, including a leading `_Collection - ` a user adds to sort them to the
    top. Index/living files (`_Decks.md`, `_Collection.md`, `_To-Buy.md`) carry
    no `set-codes:`, so they're never mistaken for collections.
    """
    return [n for n in sorted(out_dir.glob("*.md"))
            if re.search(r"^set-codes:", n.read_text(encoding="utf-8"), re.M)]


def _note_set_codes(note: Path) -> list[str]:
    """The set codes a checklist note was built from, stored in its frontmatter
    so a refresh never needs them typed again.
    """
    m = re.search(r"^set-codes: (.+)$", note.read_text(encoding="utf-8"), re.M)
    return [c.strip() for c in m.group(1).split(",") if c.strip()] if m else []


def _note_label(note: Path) -> str:
    """The checklist note's label — its filename minus any organising prefix:
    a leading `_Collection - ` (added to sort it to the top) or a legacy dated
    `_MTG-Collection_`.
    """
    stem = note.stem.split("_MTG-Collection_", 1)[-1]
    return re.sub(r"^_Collection\s*-\s*", "", stem)


def resolve_set_target(out_dir: Path, arg: str) -> tuple[list[str], str | None]:
    """Turn whatever the user typed after --set into (set codes, label).

    Accepts, in order of preference: the label of a checklist note that already
    exists (so `--set "Final Fantasy"` refreshes it using the codes it was built
    from), a preset name, or a raw comma-separated list of Scryfall set codes.
    """
    for note in collection_notes(out_dir):
        if _note_label(note).lower() == arg.lower():
            codes = _note_set_codes(note)
            if codes:
                return codes, _note_label(note)
    key = arg.lower().replace(" ", "")
    if key in SET_PRESETS:
        codes, label = SET_PRESETS[key]
        return list(codes), label
    return [c.strip().lower() for c in arg.split(",") if c.strip()], None


PRODUCT_ORDER = ["fin", "fic", "fca", "pfin", "afin", "afic", "tfin", "tfic",
                 "msh", "msc", "mar"]

# Treatment blocks, in the order a collector thinks about them: the base set
# first, then the fancy pulls. Within a block cards run in collector-number
# order, which is how a binder is laid out — scan a card, read its number, go
# straight to the slot.
TREATMENT_ORDER = ["Regular", "Showcase", "Borderless", "Full-art",
                   "Extended-art", "Etched", "Promo", "Token", "Art card"]


def _treatment(c: dict) -> str:
    """Which block a printing belongs in — how the card actually looks, which
    is what tells two copies of the same card apart in a binder.
    """
    layout = c.get("layout", "normal")
    if "token" in layout or layout == "emblem":
        return "Token"
    if layout == "art_series":
        return "Art card"
    fx = set(c.get("frame_effects") or [])
    if c.get("full_art"):
        return "Full-art"
    if "extendedart" in fx:
        return "Extended-art"
    if "etched" in fx:
        return "Etched"
    if "inverted" in fx:
        return "Showcase"
    if c.get("border_color") == "borderless":
        return "Borderless"
    if c.get("promo"):
        return "Promo"
    return "Regular"


def _num_key(num: str):
    """Sort collector numbers naturally: 2 before 10, and 551a before 551b."""
    m = re.match(r"(\d+)(.*)", str(num))
    return (int(m.group(1)), m.group(2)) if m else (10 ** 9, str(num))


def fetch_set_printings(codes: list[str]) -> list[dict]:
    """Every individual PRINTING across the given sets — one entry per physical
    card you could slot into a binder, each with its own set/collector-number id.

    Foil and non-foil are NOT split: they share a collector number, so they are
    the same slot. Only a genuinely different printing (different art, border or
    frame) earns its own line.
    """
    out: list[dict] = []
    db = scryfall_db()
    for code in codes:
        if db is not None:
            rows = db.execute(
                "SELECT name, set_code, collector_number, rarity, finishes, "
                "eur, usd, layout, frame_effects, full_art, border_color, promo "
                "FROM cards WHERE set_code = ?", (code.lower(),)).fetchall()
            if rows:
                for r in rows:
                    disp = r["name"]
                    if " // " in disp:
                        faces = [f.strip() for f in disp.split(" // ")]
                        if len(set(faces)) == 1:
                            disp = faces[0]
                    finishes = json.loads(r["finishes"])
                    out.append({
                        "name": disp,
                        "set": r["set_code"],
                        "num": str(r["collector_number"]),
                        "treatment": _treatment({
                            "layout": r["layout"],
                            "frame_effects": json.loads(r["frame_effects"]),
                            "full_art": bool(r["full_art"]),
                            "border_color": r["border_color"],
                            "promo": bool(r["promo"])}),
                        "rarity": r["rarity"] or "common",
                        "foil_only": finishes == ["foil"],
                        "gbp": _card_gbp(r["eur"], r["usd"], fx_rates()),
                    })
                print(f"Set:       {code} — {len(rows)} printings")
                continue
            print(f"Set:       '{code}' not in the reference DB — asking the API")
        url: str | None = SCRYFALL_SEARCH
        params: dict | None = {"q": f"set:{code}", "unique": "prints",
                               "include_extras": "true",
                               "include_variations": "true"}
        found = 0
        while url:
            r = http("GET", url, params=params)
            if r.status_code == 404:
                print(f"Set:       '{code}' has no cards on Scryfall — skipped")
                break
            r.raise_for_status()
            data = r.json()
            for c in data.get("data", []):
                if "paper" not in (c.get("games") or ["paper"]):
                    continue
                found += 1
                disp = c["name"]
                if " // " in disp:
                    faces = [f.strip() for f in disp.split(" // ")]
                    if len(set(faces)) == 1:
                        disp = faces[0]
                p = c.get("prices", {})
                out.append({
                    "name": disp,
                    "set": c["set"].lower(),
                    "num": str(c.get("collector_number", "")),
                    "treatment": _treatment(c),
                    "rarity": c.get("rarity", "common"),
                    "foil_only": c.get("finishes") == ["foil"],
                    "gbp": _card_gbp(
                        float(p["eur"]) if p.get("eur") else None,
                        float(p["usd"]) if p.get("usd") else None, fx_rates()),
                })
            url, params = data.get("next_page"), None
        if found:
            print(f"Set:       {code} — {found} printings")
    return out


TICK_RE = re.compile(r"^- \[([ xX])\]\s+`([A-Z0-9]+ [\w-]+)`")


def _existing_ticks(note: Path) -> set[str]:
    """Every already-ticked printing id, so a refresh re-prices the list without
    wiping progress. Keyed on the id alone — it is globally unique, so no
    section tracking is needed and reorganising the blocks can never orphan a
    tick.
    """
    if not note.is_file():
        return set()
    ticked = set()
    for line in note.read_text(encoding="utf-8").splitlines():
        m = TICK_RE.match(line.strip())
        if m and m.group(1).lower() == "x":
            ticked.add(m.group(2).upper())
    return ticked


def set_collection(out_dir: Path, codes: list[str], label: str | None = None,
                   reset: bool = False) -> None:
    """--set: build or refresh a printing-by-printing collection checklist.

    One line per printing, grouped by product then treatment and ordered by
    collector number, so a card in your hand maps to exactly one line. A ticked
    box means you have that printing; ticks come from your collection file
    (where it records the id) or from you, and survive every refresh. The note
    is located by the set codes in its frontmatter, so it can be renamed freely.
    """
    printings = fetch_set_printings(codes)
    if not printings:
        sys.exit(f"No cards found for: {', '.join(codes)}")
    # Find the note by the set codes in its frontmatter, NOT by its filename —
    # renaming a note (adding an emoji, say) must never orphan it and spawn a
    # duplicate, which would silently abandon every tick it holds.
    note = None
    for prior in collection_notes(out_dir):
        if set(_note_set_codes(prior)) == set(codes):
            note = prior
            if not label:
                label = _note_label(prior)
            break
    name = label or ", ".join(c.upper() for c in codes)
    if note is None:
        safe = ILLEGAL_FILENAME_CHARS.sub("", name)
        note = out_dir / f"_Collection - {safe}.md"
    ticked = set() if reset else _existing_ticks(note)

    _, coll_path, _owned = collection_state(out_dir)
    owned_ids: set[tuple[str, str]] = set()
    owned_names: set[str] = set()
    owned_foil: set[tuple[str, str]] = set()
    for e in read_collection_entries(coll_path):
        if e["set"] and e["num"]:
            owned_ids.add((e["set"], e["num"]))
            if e["foil"]:
                owned_foil.add((e["set"], e["num"]))
        owned_names.add(e["name"].lower())

    # A bare name can only identify a printing when the card has exactly one
    # printing across these sets — otherwise "Sol Ring" could be any of four arts.
    name_counts: dict[str, int] = {}
    for pr in printings:
        key = pr["name"].lower()
        name_counts[key] = name_counts.get(key, 0) + 1

    by_id = by_name = 0
    for pr in printings:
        pid = f"{pr['set'].upper()} {pr['num']}"
        if pid in ticked:
            continue
        if (pr["set"], pr["num"]) in owned_ids:
            ticked.add(pid)
            by_id += 1
        elif name_counts[pr["name"].lower()] == 1 and pr["name"].lower() in owned_names:
            ticked.add(pid)
            by_name += 1
    needs_id = sum(1 for n, cnt in name_counts.items()
                   if cnt > 1 and n in owned_names)

    def pid_of(x):
        return f"{x['set'].upper()} {x['num']}"

    done = sum(1 for pr in printings if pid_of(pr) in ticked)
    total_cost = sum(pr["gbp"] or 0 for pr in printings)
    left_cost = sum(pr["gbp"] or 0 for pr in printings if pid_of(pr) not in ticked)
    distinct = len({pr["name"] for pr in printings})
    pct = 100 * done / len(printings)
    bar = "█" * round(pct / 5) + "░" * (20 - round(pct / 5))

    groups: dict[str, dict[str, list]] = {}
    for pr in printings:
        groups.setdefault(pr["set"], {}).setdefault(pr["treatment"], []).append(pr)

    prods = sorted(groups, key=lambda s: (PRODUCT_ORDER.index(s)
                                          if s in PRODUCT_ORDER else 99, s))
    blocks, summary = [], []
    for prod in prods:
        tre = groups[prod]
        p_items = [x for v in tre.values() for x in v]
        p_done = sum(1 for x in p_items if pid_of(x) in ticked)
        p_cost = sum(x["gbp"] or 0 for x in p_items if pid_of(x) not in ticked)
        summary.append(f"| **{prod.upper()}** | **{p_done}/{len(p_items)}** | "
                       f"**£{p_cost:,.2f}** |")
        blocks.append(f"## {prod.upper()} — {p_done}/{len(p_items)} "
                      f"· £{p_cost:,.2f} to go")
        for t in TREATMENT_ORDER + [k for k in tre if k not in TREATMENT_ORDER]:
            items = tre.get(t)
            if not items:
                continue
            items.sort(key=lambda x: _num_key(x["num"]))
            t_done = sum(1 for x in items if pid_of(x) in ticked)
            t_cost = sum(x["gbp"] or 0 for x in items if pid_of(x) not in ticked)
            summary.append(f"| &emsp;{t} | {t_done}/{len(items)} | "
                           f"£{t_cost:,.2f} |")
            lines = []
            for x in items:
                mark = "x" if pid_of(x) in ticked else " "
                price = f"£{x['gbp']:,.2f}" if x["gbp"] is not None else "—"
                if (x["set"], x["num"]) in owned_foil:
                    tag = " ✨"          # you have this one in foil
                elif x["foil_only"]:
                    tag = " *(foil only)*"
                else:
                    tag = ""
                lines.append(f"- [{mark}] `{pid_of(x)}` {x['name']}{tag} — {price}")
            body = "\n".join(lines)
            blocks.append(f"### {t} — {t_done}/{len(items)} "
                          f"· £{t_cost:,.2f} to go\n\n{body}")

    already = done - by_id - by_name   # ticks the note already held
    warn = (f" ⚠️ {needs_id} cards are in your collection by name but have several "
            f"printings here — they need an id before they can count."
            if needs_id else "")
    summary_rows = "\n".join(summary)
    block_body = "\n\n".join(blocks)
    note.write_text(f"""---
tags: [mtg, collection, set-target]
updated: {date.today().isoformat()}
set-codes: {", ".join(codes)}
printings-total: {len(printings)}
printings-owned: {done}
distinct-cards: {distinct}
cost-remaining-gbp: {left_cost:.2f}
project: "{PROJECT_LINK}"
---

# 🎯 {name} — collection target

**Project:** {PROJECT_LINK}

`{bar}` **{done}/{len(printings)}** printings ({pct:.0f}%) · **£{left_cost:,.2f}** still to buy of £{total_cost:,.2f}

Every printing has its own line, because a different art is a different card to own — {len(printings)} printings across {distinct} distinct cards. Foil and non-foil share a line: they share a collector number, so they're the same slot. Lines marked *(foil only)* exist in no other finish.

**To tick a box:** record the card in `_Collection.md` with its id — `1 Sol Ring (FIC) 357` — and the matching box ticks itself on the next `--set`. Or tick it here by hand; ticks are never removed by a refresh.

**Ticked: {done} of {len(printings)}** — {by_id} newly matched by id, {by_name} by name (cards with only one printing), {already} already ticked before this run.{warn}

| Product | Have | Left to buy |
|---------|-----:|------------:|
{summary_rows}

{block_body}
""", encoding="utf-8")

    print(f"Set:       {name} — {len(printings)} printings "
          f"({distinct} distinct cards)")
    print(f"Progress:  {done}/{len(printings)} ticked ({pct:.0f}%)"
          f" — £{left_cost:,.2f} of £{total_cost:,.2f} still to buy")
    print(f"           {by_id} new by id, {by_name} new by name,"
          f" {already} already ticked"
          + (f", {needs_id} need an id" if needs_id else ""))
    print(f"Note:      {note}")


BRIEFS_DIR = "_analysis-briefs"
RECENT_CUTOFF = "2025-09-01"  # cards newer than this get oracle text in briefs


def write_brief(out_dir: Path, deck_id: int, note: Path) -> Path | None:
    """A compact analysis brief for one deck (--brief): everything a model
    needs to write the 🎮/🏆/⚠️ sections — deck shape, role groups, and
    oracle text ONLY for recent cards it may not know. Token-lean by design.
    """
    text = note.read_text(encoding="utf-8")
    decklist = _note_decklist(text)
    if not decklist:
        print(f"[{deck_id}] {deck_name_of(note)}: no deck list — skipped")
        return None
    name_m = re.search(r"^deck-name: (.+)$", text, re.M)
    deck_name = _fm_unquote(name_m.group(1)) if name_m else note.stem
    prices = fetch_prices([n for _, n in decklist])
    shape = deck_shape(decklist, prices)

    commander = decklist[0][1]
    listing = "\n".join(f"{qty} {n}" for qty, n in decklist)
    recent = []
    for _qty, n in decklist:
        p = prices.get(n.lower()) or {}
        if p.get("released", "") >= RECENT_CUTOFF and p.get("oracle"):
            recent.append(f"### {p.get('name', n)} — {p.get('type')}\n"
                          f"{p['oracle']}")
    recent_block = ("\n\n## 🆕 Recent cards (oracle text — the model may not "
                    "know these)\n\n" + "\n\n".join(recent)) if recent else ""
    todo = [h for h in ANALYSIS_SECTIONS
            if not re.search(rf"## {re.escape(h)}\n\n(?!-\n)\S", text)]
    brief = f"""# Analysis brief — [{deck_id}] {deck_name}

Commander: {commander}
Note file: [[{note.stem}|{note.name}]]
Sections still empty: {", ".join(todo) if todo else "none — already analysed"}

{render_deck_shape(shape)}

## 📜 Deck List

```
{listing}
```{recent_block}
"""
    briefs = out_dir / BRIEFS_DIR
    briefs.mkdir(exist_ok=True)
    # Colour-free filename: colour emojis in the deck name are display-only,
    # and a suffixed filename would orphan the pre-colour brief on re-run
    dest = briefs / (f"{deck_id:02d} - "
                     f"{ILLEGAL_FILENAME_CHARS.sub('', strip_colors(deck_name))}.md")
    dest.write_text(brief, encoding="utf-8")
    print(f"[{deck_id}] brief → {dest.name}"
          + (" (already analysed)" if not todo else ""))
    return dest


def buy_frontmatter(buy: dict, rates: dict | None, cheap: dict | None) -> str:
    lines = [f"owned: {buy['owned_unique']}/{buy['unique']}",
             f"buy-eur: {buy['totals']['eur']:.2f}"]
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
        f"{_usd_cell(m['usd'])} | "
        f"{_gbp_cell(_card_gbp(m['eur'], m['usd'], rates))} |"
        for m in buy["missing"]
    ] + [
        f"| {name} | {_owned_cell(qty)} | — | — | — |"
        for qty, name in sorted(buy["owned_rows"], key=lambda r: r[1].lower())
    ]
    table = "\n".join(rows)
    # Pin the printing these prices are for. Without it "1 Smoke" sends you to
    # a search where the cheapest hit is nothing like the €3.05 quoted here.
    # *F* marks lines priced as foils, so the shop basket matches the quote;
    # category tags never appear here — this is a shopping list, not notes.
    listing = "\n".join(
        _choice_line(m["need"], {"printed": m["name"], "set_code": m.get("set"),
                                 "num": m.get("num")}, m["name"])
        + (" *F*" if m.get("foil") else "")
        for m in sorted(buy["missing"], key=lambda m: m["name"].lower()))
    return f"""## 🛒 Cards to Complete the Deck

{summary}
Buy the **{len(buy['missing'])}** missing card(s) ≈ **€{buy["totals"]["eur"]:,.2f} · ${buy["totals"]["usd"]:,.2f} · {buy_gbp}** at the deck's own versions{cheap_hint}. Prices are per copy; **✅ = pull it from your collection**, its price is off the totals.{unpriced_note}

| Card | Buy | EUR | USD | ≈ GBP |
|------|:----|----:|----:|------:|
{table}

### 📋 Buy List (copy-paste)

The missing cards at the printings priced above — `(SET) 123` pins each one,
so what you're quoted is what you find:

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
    cheap_gbp = _gbp_cell(cheap["totals"]["gbp"] if rates else None)
    unpriced_note = (
        f"\n\n> ⚠️ {cheap['unpriced']} card(s) have no Cardmarket price — they "
        "are not in the totals above." if cheap["unpriced"] else "")
    rows = []
    for m in buy["missing"]:
        c = choices.get(m["name"].lower())
        if c and c["changed"]:
            label = f"{c['printed']} ({c['set_name']})"
            eur, save = c["eur"], (m["eur"] or 0) - (c["eur"] or 0)
        else:
            label, eur, save = m["name"], m["eur"], None
        rows.append(
            f"| {label} | {_buy_cell(m)} | {_eur_cell(eur)} | "
            f"{_gbp_cell(eur * rates['eur_gbp'] if rates and eur is not None else None)} | "
            f"{f'€{save:,.2f}' if save else '—'} |")
    table = "\n".join(rows)
    listing = "\n".join(cheap["lines"])
    return f"""## 🛒 Cards to Complete — Cheapest Build

The same missing cards at their cheapest versions ≈ **€{cheap["totals"]["eur"]:,.2f} · {cheap_gbp}** — saves **€{saved:,.2f}** over the deck's own versions. Universes Beyond skins are only art and printed-name swaps, so the plain version is functionally identical (and vice versa).{unpriced_note}

| Card (cheapest version) | Buy | EUR | ≈ GBP | Save |
|-------------------------|:----|----:|------:|-----:|
{table}

### 📋 Budget Buy List (copy-paste)

The missing cards at their cheapest versions — `(SET) 123` pins the exact
printing each price refers to (MTG Arena syntax — Moxfield and most store
decklist finders understand it). Search a name without its id and you'll be
quoted the wrong printing.

```
{listing}
```"""


def extract_reviews(text: str) -> dict[str, str]:
    """Pull hand-written review AND analysis content out of an existing note
    so a --force regeneration refreshes the data without destroying your
    thoughts (or Claude's /analyse-deck prose).
    """
    reviews = {}
    for heading in REVIEW_SECTIONS + ANALYSIS_SECTIONS:
        m = re.search(rf"## {re.escape(heading)}\n(.*?)(?=\n## )", text, re.S)
        if m:
            body = m.group(1).strip()
            if body and body != "-":
                reviews[heading] = body
    return reviews


def render_deck_listing(decklist: list[tuple[int, str]],
                        pins: dict[str, dict] | None = None) -> str:
    """The 📜 Deck List block's lines, at full fidelity. A pinned card keeps
    its `(SET) 123` (still valid Arena/Moxfield syntax), its *F* foil marker
    and its `[Category]` tag — so nothing an import stated is lost, and a
    stored-list rebuild (--recheck with the source unreachable) re-reads all
    of it via _note_pins. Bare cards render bare, exactly as before.
    """
    lines = []
    for qty, name in decklist:
        pin = (pins or {}).get(name.lower()) or {}
        parts = [f"{qty} {name}"]
        if pin.get("set") and pin.get("num"):
            parts.append(f"({pin['set'].upper()}) {pin['num']}")
        if pin.get("foil"):
            parts.append("*F*")
        if pin.get("cat"):
            parts.append(f"[{pin['cat']}]")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _token_rows(tokens: list[dict]) -> list[dict]:
    """Price and picture each token at its pinned printing — reference DB
    first, Scryfall API for anything the bulk snapshot doesn't know. Tokens
    rarely carry a market price (Cardmarket seldom lists recent ones
    individually), so rows keep None prices rather than being dropped: the
    section's job is the pinned what-to-grab checklist, prices are a bonus.
    """
    rows = []
    db = scryfall_db()
    misses = []
    for t in tokens:
        row = None
        if db is not None and t.get("set") and t.get("num"):
            row = db.execute(
                "SELECT eur, usd, image_uri FROM cards "
                "WHERE set_code = ? AND collector_number = ?",
                (t["set"].lower(), str(t["num"]))).fetchone()
        if row is None:
            misses.append(t)
            rows.append({**t, "eur": None, "usd": None, "img": None})
        else:
            rows.append({**t, "eur": row["eur"], "usd": row["usd"],
                         "img": row["image_uri"]})
    if misses:
        idents = [{"set": t["set"], "collector_number": str(t["num"])}
                  for t in misses if t.get("set") and t.get("num")]
        if idents:
            r = http("POST", SCRYFALL_COLLECTION, json={"identifiers": idents})
            if r.status_code == 200:
                by_key = {(c["set"].lower(), str(c["collector_number"])): c
                          for c in r.json().get("data", [])}
                for row in rows:
                    card = by_key.get(((row.get("set") or "").lower(),
                                      str(row.get("num") or "")))
                    if card is not None and row["img"] is None:
                        p = card.get("prices", {})
                        row.update(
                            eur=float(p["eur"]) if p.get("eur") else None,
                            usd=float(p["usd"]) if p.get("usd") else None,
                            img=scryfall_card_image(card))
    return rows


def render_tokens_section(tokens: list[dict], rates: dict | None) -> str:
    """🎟️ Tokens & Extras — the physical token cards the deck's spells
    create. Real purchasable cards, but not part of the 100, so everything
    here stays OFF the deck value, buy totals and deck shape. Only written
    when an import carried a token list (Archidekt URLs do).
    """
    if not tokens:
        return ""
    rows = _token_rows(tokens)
    table = "\n".join(
        f"| {r['name']} | {_pin_cell(r)} | {_eur_cell(r['eur'])} | "
        f"{_gbp_cell(_card_gbp(r['eur'], r['usd'], rates))} |"
        for r in rows)
    gallery = _callout("🖼️ Token Gallery",
                       render_gallery([(r["img"], r["name"]) for r in rows]))
    listing = "\n".join(
        _choice_line(r["qty"], {"printed": r["name"], "set_code": r.get("set"),
                                "num": r.get("num")}, r["name"])
        for r in rows)
    priced = sum(1 for r in rows if r["eur"] is not None or r["usd"] is not None)
    body = f"""The physical token cards this deck's spells create — real cards worth grabbing with the order, but **not part of the 100**: nothing here counts toward the deck value, buy lists or deck shape. Printings are the ones chosen on the deck site. Tokens are rarely listed individually ({priced}/{len(rows)} priced here) — any token of the same name and stats does the job.

| Token | Version | EUR | ≈ GBP |
|-------|---------|----:|------:|
{table}

{gallery}

> [!note]- 📋 Token List (copy-paste)
>
> ```
{_quoted_block(listing)}
> ```"""
    return f"\n\n## 🎟️ Tokens & Extras\n\n{body}"


def _pin_cell(r: dict) -> str:
    return (f"({r['set'].upper()}) {r['num']}"
            if r.get("set") and r.get("num") else "—")


def _eur_cell(eur: float | None) -> str:
    return f"€{eur:,.2f}" if eur is not None else "—"


def _quoted_block(text: str) -> str:
    return "\n".join(f"> {ln}" for ln in text.splitlines())


def _note_tokens(text: str) -> list[dict]:
    """The token list stored in a note's 🎟️ section, so a stored-list
    rebuild keeps it without refetching the site.
    """
    block = re.search(
        r"## 🎟️ Tokens & Extras.*?📋 Token List \(copy-paste\)\s*\n>\s*\n> ```\n(.*?)> ```",
        text, re.S)
    if not block:
        return []
    tokens = []
    for line in block.group(1).splitlines():
        p = parse_card_line_full(line.lstrip("> ").strip())
        if p:
            tokens.append({"qty": p[0], "name": p[1], "set": p[2], "num": p[3]})
    return tokens


def build_note(deck: dict, decklist: list[tuple[int, str]],
               image_url: str, deck_url: str, report: dict,
               buy: dict | None, collection_name: str | None,
               reviews: dict[str, str], choices: dict[str, dict],
               deck_id: int, history: list[dict] | None = None,
               shape_section: str = "", colors: str = "") -> str:
    """The whole note. Reading order after the reviews: card prices &
    gallery, the deck list, what to buy to complete it, the Cheapest Build,
    and what to buy to complete that. colors is the commander colour-emoji
    suffix, shown at the end of the deck's display name (frontmatter + H1)
    but never stored in deck["name"] itself — matching and history stay on
    the plain name.
    """
    today = date.today().isoformat()
    display_name = (f"{strip_colors(deck['name'])} {colors}" if colors
                    else deck["name"])
    commander_line = ", ".join(deck["commanders"])
    listing = render_deck_listing(decklist, deck.get("pins"))
    rates = report["rates"]
    tokens_section = render_tokens_section(deck.get("tokens") or [], rates)

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
        f"## {heading}\n\n{reviews.get(heading, '-')}"
        for heading in REVIEW_SECTIONS
        if reviews.get(heading) or heading not in RETIRED_REVIEW_SECTIONS
    )
    # A written 🧭 Deck Guide already covers play pattern, win conditions and
    # rules traps in its own ### blocks, so don't also emit empty stubs for them
    # — /deck-guide deletes those, and a rebuild shouldn't put them back. A
    # section that HAS prose (from /analyse-deck, or by hand) always survives.
    guide_written = bool(reviews.get("🧭 Deck Guide"))
    analysis_parts = [
        f"## {heading}\n\n{reviews.get(heading, '-')}"
        for heading in ANALYSIS_SECTIONS
        if reviews.get(heading) or not guide_written
    ]
    if shape_section:  # joined here so dropping every stub leaves no blank run
        analysis_parts.insert(0, shape_section)
    analysis_block = "\n\n".join(analysis_parts)
    history_block = f"\n{render_history(history)}\n" if history else ""

    return insert_toc(f"""---
tags: [mtg, deck, commander]
created: {today}
commander: {commander_line}
deck-name: {_fm_scalar(display_name)}
deck-url: {deck_url}
deck-id: {deck_id}
project: "{PROJECT_LINK}"
{price_frontmatter}
price-date: {today}
---

# 🃏 {display_name}

**Commander:** {commander_line}
**Format:** {deck["format"]}
**Source:** {deck["source_md"]}
**Project:** {PROJECT_LINK}

{render_value_block(report, today, buy, cheap)}
{history_block}
![{commander_line}|290]({image_url})

{review_block}

{analysis_block}

{render_card_tables(report)}
## 📜 Deck List

```
{listing}
```{tokens_section}{buy_section}

{render_budget_list(decklist, report, choices, cheap)}{cheap_buy_section}
""")


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
                f"{gbp}**{cheap_bit} — see 🛒 Cards to Complete below.\n")
        else:
            finish_line = ("\n🎉 **You own every card in this deck** — "
                           "nothing to buy.\n")
    return f"""| Source | Value | ≈ GBP | Cards priced |
|--------|------:|------:|-------------:|
{value_rows}
{finish_line}
*💰 Standard (non-foil) cards, from Scryfall's daily snapshot ({today}). ≈ GBP is rough — ECB reference rates via frankfurter.dev; 1 tix ≈ $1.*"""


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
        f"| {name}{f' ×{qty}' if qty > 1 else ''} | {_eur_cell(eur)} | {_usd_cell(usd)} | {_gbp_cell(_card_gbp(eur, usd, rates))} |"
        for qty, name, eur, usd, _img in report["all"]
    )
    unpriced_note = (
        f"\n\n> ⚠️ No price found for {len(report['unpriced'])} card(s): "
        + ", ".join(report["unpriced"]) if report["unpriced"] else ""
    )
    gallery_cells = [
        (img, f"{name}{f' ×{qty}' if qty > 1 else ''}")
        for qty, name, _eur, _usd, img in report["all"]
    ]
    prices_body = f"""Every card, dearest first (×N marks multiples — basics etc.; the price shown is per copy).

| Card | EUR | USD | ≈ GBP |
|------|----:|----:|------:|
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
    functionally-identical version (any printing or Universes Beyond/plain-name
    swap), with the detail tables collapsed into callouts.
    """
    rates = report["rates"]
    rows = []
    list_lines = []
    gallery_cells = []
    totals = {"eur": 0.0, "gbp": 0.0}
    unpriced = 0
    for qty, name in decklist:
        c = choices.get(name.lower()) or {}
        eur, img = c.get("eur"), c.get("img")
        label = f"{c['printed']} ({c['set_name']})" if c.get("changed") else name
        gbp = eur * rates["eur_gbp"] if rates and eur is not None else None
        if eur is None:
            unpriced += 1
        totals["eur"] += (eur or 0) * qty
        totals["gbp"] += (gbp or 0) * qty
        rows.append(
            f"| {label}{f' ×{qty}' if qty > 1 else ''} | "
            f"{_eur_cell(eur)} | {_gbp_cell(gbp)} |"
        )
        list_lines.append(_choice_line(qty, c or None, name))
        gallery_cells.append((img, f"{label}{f' ×{qty}' if qty > 1 else ''}"))
    body = "\n".join(rows)
    listing = "\n".join(list_lines)
    prices_body = f"""| Card (cheapest version) | EUR | ≈ GBP |
|-------------------------|----:|------:|
{body}"""
    listing_body = f"""`(SET) 123` pins the exact printing this list is
costed against (MTG Arena syntax — Moxfield and most store decklist finders
understand it). Search a name without its id and you'll be quoted the wrong
printing. A line appears bare only when no printing could be priced.

```
{listing}
```"""
    gallery_body = f"""Each card at the cheapest version chosen above.

{render_gallery(gallery_cells)}"""
    missing_line = ""
    if cheap and cheap["lines"]:
        missing_line = (
            f"\n🛒 Missing cards only ≈ **€{cheap['totals']['eur']:,.2f} · "
            f"{_gbp_cell(cheap['totals']['gbp'] if rates else None)}** — "
            "see 🛒 Cards to Complete — Cheapest Build below.\n")
    unpriced_note = (
        f"\n> ⚠️ {unpriced} card(s) have no Cardmarket price — they are not in "
        "the total above.\n" if unpriced else "")
    return f"""## 💸 Cheapest Build

The whole deck with every card at its cheapest functionally-identical version
— other printings and Universes Beyond/plain-name swaps included. EUR is the
cheapest Cardmarket printing, ≈ GBP that price converted. Cards under €0.50
keep the deck's own version.

Whole deck at cheapest versions ≈ **€{totals["eur"]:,.2f} · {_gbp_cell(totals["gbp"] if rates else None)}**.
{missing_line}{unpriced_note}
{_callout("💸 Cheapest-version prices (per card)", prices_body)}

{_callout("📋 Cheapest Build List (copy-paste)", listing_body)}

{_callout("🖼️ Cheapest Version Gallery", gallery_body)}"""


def price_frontmatter_str(report: dict) -> str:
    totals, rates = report["totals"], report["rates"]
    fm_prices = {"eur": totals["eur"]}
    if rates:
        fm_prices["gbp"] = totals["eur"] * rates["eur_gbp"]
    fm_prices["usd"] = totals["usd"]
    fm_prices["tix"] = totals["tix"]
    return "\n".join(f"price-{k}: {v:.2f}" for k, v in fm_prices.items())


DECK_ID_RE = re.compile(r"^deck-id: (\d+)$", re.M)


def read_deck_id(text: str) -> int | None:
    m = DECK_ID_RE.search(text)
    return int(m.group(1)) if m else None


def _is_deck_note(text: str) -> bool:
    """A generated deck note always carries a deck-url (its identity) and a
    deck-id. Hand-written notes that merely share the MTG filename pattern — a
    shopping list, a strategy scratchpad — have neither, and must never be
    treated as decks by --list/--reindex/--delete or the index.
    """
    return bool(re.search(r"^deck-(url|id):", text, re.M))


def deck_notes(out_dir: Path) -> list[Path]:
    """Every real deck note in the vault, in filename order — the notes that are
    actually decks (see _is_deck_note), identified by their frontmatter rather
    than any filename pattern, so a note keeps its identity whatever it's called.
    Index / living files (leading `_`) are skipped up front; collection notes
    fall out naturally because they carry no `deck-url`/`deck-id` frontmatter.
    """
    return [n for n in sorted(out_dir.glob("*.md"))
            if not n.name.startswith("_")
            and _is_deck_note(n.read_text(encoding="utf-8"))]


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
    notes = deck_notes(out_dir)
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


DECKS_INDEX = "_Decks.md"

# Every generated note links back to the project dashboard so nothing floats
# unlinked in the vault graph. Full path — other projects have a
# "_Current State.md" too, so a bare link would be ambiguous.
PROJECT_LINK = "[[02 - Projects/MTG Deck Importer/_Current State|MTG Deck Importer]]"


def update_deck_index(out_dir: Path) -> None:
    """Regenerate _Decks.md — the master index: one row per deck note with an
    Obsidian link, commander, value, owned count and cost to finish (from the
    frontmatter already in each note). Rebuilt wholesale after every import
    and recheck so it can never drift; not a deck note itself (leading _).
    """
    ids = deck_id_map(out_dir)
    if not ids:
        return

    # Totals are built from the EUR fields, which every priced note has, and
    # converted for display. Reading price-gbp instead made the whole index
    # read "£0.00" whenever the FX API had been down at import time.
    rates = fx_rates()
    rows = []
    tot_val = tot_buy = 0.0

    def _gbp(eur: str, gbp: str) -> float | None:
        if gbp:
            return float(gbp)
        if eur and rates:
            return float(eur) * rates["eur_gbp"]
        return None

    for did in sorted(ids):
        text = ids[did].read_text(encoding="utf-8")

        def fm(key: str) -> str:
            m = re.search(rf"^{key}: (.+)$", text, re.M)
            return m.group(1).strip() if m else ""

        name = _fm_unquote(fm("deck-name")) or ids[did].stem
        buy = _gbp(fm("buy-cheapest-eur") or fm("buy-eur"),
                   fm("buy-cheapest-gbp") or fm("buy-gbp"))
        val = _gbp(fm("price-eur"), fm("price-gbp"))
        tot_val += val or 0.0
        tot_buy += buy or 0.0
        owned = fm("owned")
        finish = ("🎉 owned" if owned and buy == 0
                  else f"£{buy:,.2f}" if buy is not None else "—")
        rows.append(
            f"| {did} | [[{ids[did].stem}\\|{name}]] | {fm('commander')} | "
            f"{f'£{val:,.2f}' if val is not None else '—'} | {owned or '—'} | "
            f"{finish} | {fm('price-date') or '—'} |")
    body = "\n".join(rows)
    today = date.today().isoformat()
    if not rates:
        totals_line = ("£ totals unavailable — the exchange-rate API could not "
                       "be reached and no rate has been cached yet.")
    else:
        totals_line = (f"total value ≈ **£{tot_val:,.2f}** · cost to finish them "
                       f"all (cheapest versions) ≈ **£{tot_buy:,.2f}**")
    (out_dir / DECKS_INDEX).write_text(f"""---
tags: [mtg, index, moc]
updated: {today}
project: "{PROJECT_LINK}"
---

# 🃏 Deck Index

Auto-generated by the {PROJECT_LINK} after every import/recheck — don't edit,
it will be overwritten. **{len(ids)} decks** · {totals_line}

| # | Deck | Commander | Value | Own | To finish | Priced |
|--:|------|-----------|------:|:---:|----------:|--------|
{body}
""", encoding="utf-8")


def _relink(out_dir: Path, renames: list[tuple[str, str]]) -> None:
    """Rewrite [[wiki-links]] (and commander-art .jpg references) that point at
    renamed notes, in every markdown file under the vault folder. The lookahead
    stops a shorter stem hijacking a longer one that it prefixes ('Krenko, Mob
    Boss' vs 'Krenko, Mob Boss - $100 …'): the next char after a whole link
    target is always ], |, \\ or #, never more name.
    """
    for md in out_dir.rglob("*.md"):
        text = md.read_text(encoding="utf-8")
        new = text
        for old, new_stem in renames:
            new = re.sub(r"\[\[" + re.escape(old) + r"(?=[\]|\\#])",
                         f"[[{new_stem}", new)
            new = new.replace(f"{old}.jpg", f"{new_stem}.jpg")
        if new != text:
            md.write_text(new, encoding="utf-8")
            print(f"Relinked:  {md.relative_to(out_dir)}")


def colorize_decks(out_dir: Path) -> None:
    """One-shot backfill (--colorize): stamp every existing deck note with its
    commander colour identity — the note filename, commander art, deck-name
    frontmatter and H1 all gain the emoji suffix that new imports now get at
    creation. Wiki-links under the vault folder are rewritten to the renamed
    notes. Idempotent: an already-suffixed deck is left alone (or re-suffixed,
    if its commander's colours have changed).
    """
    ids = deck_id_map(out_dir)
    if not ids:
        sys.exit(f"No deck notes found in {out_dir}")
    renames: list[tuple[str, str]] = []
    for did in sorted(ids):
        note = ids[did]
        text = note.read_text(encoding="utf-8")
        cmd_m = re.search(r"^commander: (.+)$", text, re.M)
        if not cmd_m:
            print(f"[{did}] {deck_name_of(note)}: no commander line — skipped")
            continue
        colors = commander_color_suffix([cmd_m.group(1).strip()])
        if not colors:
            print(f"[{did}] {deck_name_of(note)}: colours unknown — skipped")
            continue

        new_text = re.sub(
            r"^deck-name: (.+)$",
            lambda m: "deck-name: " + _fm_scalar(
                f"{strip_colors(_fm_unquote(m.group(1)))} {colors}"),
            text, count=1, flags=re.M)
        new_text = re.sub(
            r"^# 🃏 (.+)$",
            lambda m: f"# 🃏 {strip_colors(m.group(1))} {colors}",
            new_text, count=1, flags=re.M)
        if new_text != text:
            note.write_text(new_text, encoding="utf-8")

        new_stem = f"{strip_colors(note.stem)} {colors}"
        if new_stem == note.stem:
            print(f"[{did}] {note.stem}: already suffixed")
            continue
        art = out_dir / "Attachments" / f"{note.stem}.jpg"
        if art.is_file():
            art.rename(art.with_name(f"{new_stem}.jpg"))
        note.rename(note.with_name(f"{new_stem}.md"))
        renames.append((note.stem, new_stem))
        print(f"[{did}] {note.stem} → {new_stem}")

    if renames:
        _relink(out_dir, renames)
    update_deck_index(out_dir)
    print(f"Updated:   {DECKS_INDEX}"
          + (f" — {len(renames)} note(s) renamed, wiki-links rewritten"
             if renames else " — nothing needed renaming"))


def next_deck_id(out_dir: Path) -> int:
    """The id to give a brand-new note: one past the highest already assigned.
    Does not touch existing notes (that is deck_id_map's job).
    """
    highest = 0
    for note in deck_notes(out_dir):
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
        print(f"[{did:>3}] {deck_name_of(ids[did])}")


def _fm_scalar(s: str) -> str:
    """A string as a YAML frontmatter scalar, double-quoted only when a bare
    value would be mis-parsed — an embedded ': ', a trailing ':', or a leading
    YAML indicator char. Obsidian rejects the ENTIRE frontmatter block on one
    bad value, so a deck name like 'Super Shredder: The Rise of Oroku Saki'
    (note the ': ') must be quoted or the whole note renders as raw text.
    """
    s = s.rstrip()
    if s and (": " in s or s.endswith(":") or s[0] in "\"'#&*!|>%@`[]{},?-"):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _fm_unquote(v: str) -> str:
    """Inverse of _fm_scalar: strip surrounding double quotes and unescape,
    leaving a bare value untouched. Every reader of a quotable field (deck-name)
    runs its captured value through this so a re-import still matches by name.
    """
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return v


def _deck_name(text: str, fallback: str) -> str:
    """A deck's display name from its note text: deck-name frontmatter, else the
    H1, else the caller's fallback.
    """
    m = (re.search(r"^deck-name: (.+)$", text, re.M)
         or re.search(r"^# 🃏 (.+)$", text, re.M))
    return _fm_unquote(m.group(1)) if m else fallback


def deck_name_of(note: Path) -> str:
    """Display name for a deck note on disk. Every listing and progress line
    identifies decks this way — the dated filename is an implementation detail,
    the deck name is what you actually recognise.
    """
    try:
        return _deck_name(note.read_text(encoding="utf-8"), note.stem)
    except OSError:
        return note.stem


def _deck_files(out_dir: Path, deck_id: int, note: Path) -> list[Path]:
    """Every on-disk artefact one deck owns: its note, the commander art, the
    archived .txt it was imported from (only .txt imports have one) and its
    analysis brief. The price-history entry lives inside a shared JSON file, so
    it isn't a path here — reindex drops it once the note is gone.
    """
    text = note.read_text(encoding="utf-8")
    files = [note, out_dir / "Attachments" / f"{note.stem}.jpg"]
    url_m = re.search(r"^deck-url: (.+)$", text, re.M)
    if url_m and url_m.group(1).strip().startswith("imports/"):
        files.append(out_dir / url_m.group(1).strip())
    files += sorted((out_dir / BRIEFS_DIR).glob(f"{deck_id:02d} - *.md"))
    return [f for f in files if f.is_file()]


def _remap_briefs(out_dir: Path, moves: list[tuple[int, int]]) -> None:
    """Rename analysis briefs to match renumbered decks (their filename is
    prefixed with the deck id). Best-effort — briefs are a regenerable cache,
    so any hiccup is ignored rather than aborting the reindex.
    """
    briefs = out_dir / BRIEFS_DIR
    if not briefs.is_dir():
        return
    for old, new in moves:
        for src in briefs.glob(f"{old:02d} - *.md"):
            dest = src.with_name(f"{new:02d} - {src.name.split(' - ', 1)[1]}")
            try:
                src.replace(dest)
            except OSError:
                pass


def delete_deck(out_dir: Path, deck_id: int, assume_yes: bool = False) -> bool:
    """Remove a single deck and everything it owns on disk. Confirms first
    (unless --yes) with the exact file list, since this hard-deletes files.
    Returns True if the deck was deleted. The caller reindexes afterwards so
    the freed id number is reused and the index/history stay consistent.
    """
    ids = deck_id_map(out_dir)
    note = ids.get(deck_id)
    if note is None:
        sys.exit(f"No deck with id {deck_id}. Run --list to see the ids.")
    name = _deck_name(note.read_text(encoding="utf-8"), note.stem)
    files = _deck_files(out_dir, deck_id, note)
    print(f"About to delete deck [{deck_id}] {name}:")
    for f in files:
        print(f"  - {f.relative_to(out_dir)}")
    print("  - its price-history entry")
    print("(Recoverable from Dropbox version history if this is a mistake.)")
    if not assume_yes:
        try:
            reply = input("Delete these? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("Cancelled — nothing deleted.")
            return False
    for f in files:
        try:
            f.unlink()
        except OSError as exc:
            print(f"           could not delete {f.name}: {exc}")
    print(f"Deleted:   [{deck_id}] {name}")
    return True


def choose_deck_to_delete(out_dir: Path) -> int | None:
    """--delete with no id: print the deck list and ask which one to remove.
    Returns the chosen id, or None if the user cancels (blank input).
    """
    ids = deck_id_map(out_dir)
    if not ids:
        print(f"No deck notes found in {out_dir}")
        return None
    print("Decks:")
    for did in sorted(ids):
        name = _deck_name(ids[did].read_text(encoding="utf-8"), ids[did].stem)
        print(f"  [{did:>3}] {name}")
    try:
        reply = input("Which deck id to delete? (blank to cancel) ").strip()
    except EOFError:
        reply = ""
    if not reply:
        print("Cancelled — nothing deleted.")
        return None
    if reply not in {str(d) for d in ids}:
        sys.exit(f"No deck with id {reply!r}. Run --list to see the ids.")
    return int(reply)


def _remap_history(out_dir: Path, remap: dict[int, int]) -> None:
    """Rewrite .price-history.json keys after a reindex so every deck keeps its
    price history under its new id. Rebuilt from scratch off `remap`, so a
    shuffle like 4→3, 5→4 can't clobber and any key with no surviving deck
    (a just-deleted one) is dropped.
    """
    hist = _load_history(out_dir)
    if not hist:
        return
    new_hist = {str(new): hist[str(old)]
                for old, new in remap.items() if str(old) in hist}
    try:
        (out_dir / HISTORY_FILE).write_text(json.dumps(new_hist),
                                            encoding="utf-8")
    except OSError:
        pass


def reindex(out_dir: Path, announce: bool = True) -> list[tuple]:
    """Renumber every deck note to a gap-free, unique 1..N deck-id sequence,
    fixing any missing or duplicated ids. Order is preserved by existing id
    (notes lacking one sort last) with the filename as a stable tie-breaker, so
    duplicates resolve deterministically and a normal run only closes gaps.
    Price history is remapped to the new ids and the index regenerated.
    Returns (old_id, new_id, note) for every deck.
    """
    notes = deck_notes(out_dir)
    if not notes:
        if announce:
            print(f"No deck notes found in {out_dir}")
        return []
    entries = []
    for note in notes:
        text = note.read_text(encoding="utf-8")
        entries.append((note, text, read_deck_id(text)))
    entries.sort(key=lambda e: (e[2] if e[2] is not None else float("inf"),
                                e[0].name))
    counts: dict[int, int] = {}
    for _note, _text, old in entries:
        if old is not None:
            counts[old] = counts.get(old, 0) + 1
    remap: dict[int, int] = {}
    changes = []
    for new_id, (note, text, old) in enumerate(entries, start=1):
        if old != new_id:
            note.write_text(_insert_deck_id(text, new_id), encoding="utf-8")
        changes.append((old, new_id, note))
        if old is not None and counts[old] == 1:
            remap[old] = new_id
    _remap_history(out_dir, remap)
    _remap_briefs(out_dir, [(o, n) for o, n in remap.items() if o != n])
    update_deck_index(out_dir)
    if announce:
        moved = [(o, n, note) for o, n, note in changes if o != n]
        if moved:
            print(f"Reindex:   renumbered {len(moved)} of {len(changes)} deck(s)")
            for o, n, note in moved:
                print(f"           {o if o is not None else '—'} → {n}  "
                      f"{deck_name_of(note)}")
        else:
            print(f"Reindex:   all {len(changes)} deck id(s) already sequential")
    return changes


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
    owned = report_collection_state(
        out_dir, "every note is refreshed WITHOUT its 🛒 Cards to Complete "
                 "sections (prices, galleries and deck lists still update)")
    collection_name = collection_path(out_dir).name if owned else None
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
                print(f"[{did}] {deck_name_of(note)}: source refresh failed ({exc}) — "
                      "refreshing prices from the stored list")
        _recheck_from_stored(did, note, collection_name, owned)


def _recheck_from_stored(did: int, note: Path, collection_name: str | None,
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
        print(f"[{did}] {deck_name_of(note)}: no deck list found — skipped")
        return

    def field(pattern: str, fallback: str = "") -> str:
        m = re.search(pattern, text, re.M)
        return _fm_unquote(m.group(1)) if m else fallback

    deck = {
        "name": field(r"^deck-name: (.+)$") or field(r"^# 🃏 (.+)$", note.stem),
        "format": field(r"^\*\*Format:\*\* (.+)$", "Commander"),
        "source_md": field(r"^\*\*Source:\*\* (.+)$"),
        # The frontmatter line already holds all commanders joined with ", "
        "commanders": [field(r"^commander: (.+)$", decklist[0][1])],
        "pins": _note_pins(text),
        "tokens": _note_tokens(text),
    }
    # The stored name already carries the colour suffix (if the note has one);
    # peel it off so matching/history stay on the plain name, and hand it back
    # to build_note for display — no network needed in this offline fallback.
    colors = color_suffix_of(deck["name"])
    deck["name"] = strip_colors(deck["name"])
    deck_url = field(r"^deck-url: (.+)$")
    image_url = field(r"!\[[^\]]*\|290\]\(([^)]*)\)")
    created = field(r"^created: (.+)$")

    prices = fetch_prices([n for _, n in decklist])
    apply_pins(prices, deck["pins"])
    report = price_report(decklist, prices)
    # No usable collection → no ownership comparison; the note is rebuilt
    # without its 🛒 Cards to Complete sections (see report_collection_state)
    buy = buy_report(decklist, owned, prices) if collection_name else None
    choices = budget_choices(decklist, prices)
    cheap = cheapest_buy(buy, choices, report["rates"]) if buy else None
    history, alerts = record_history(note.parent, did, deck["name"], report,
                                     buy, cheap, choices)
    shape_section = render_deck_shape(deck_shape(decklist, prices))
    new_text = build_note(deck, decklist, image_url, deck_url, report, buy,
                          collection_name, extract_reviews(text), choices, did,
                          history, shape_section, colors)
    if created:  # keep the original import date, not the refresh date
        new_text = re.sub(r"^created: .*$", f"created: {created}", new_text,
                          count=1, flags=re.M)
    note.write_text(new_text, encoding="utf-8")
    update_deck_index(note.parent)
    ownership = (f" — own {buy['owned_unique']}/{buy['unique']}"
                 f" — to buy {len(buy['missing'])}"
                 f" (~EUR {buy['totals']['eur']:,.2f})" if buy else
                 " — ownership comparison skipped (no collection)")
    print(f"[{did}] {deck['name']}: value ~EUR {report['totals']['eur']:,.2f}"
          f"{ownership}")
    for line in alerts:
        print(f"[{did}] {line}")


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
    colors = commander_color_suffix(deck["commanders"])

    # A commander can have several builds (precon + enhanced, etc.). A note is
    # the SAME deck if its deck-url or deck-name matches; same-commander notes
    # for a different build get a " - <deck name>" suffix instead of colliding.
    # Notes are matched by frontmatter, never by filename, so a renamed note is
    # found rather than orphaned into a duplicate. deck-url is exact and unique,
    # so it wins outright; deck-name is only a fallback (two different decks can
    # share a name, and without the old commander-scoped filename to lean on a
    # name clash must never hijack the match).
    match = url_match = name_match = None
    for candidate in deck_notes(out_dir):
        text = candidate.read_text(encoding="utf-8")
        url_m = re.search(r"^deck-url: (.+)$", text, re.M)
        name_m = (re.search(r"^deck-name: (.+)$", text, re.M)
                  or re.search(r"^# 🃏 (.+)$", text, re.M))
        if url_m and url_m.group(1).strip() == deck_url:
            url_match = candidate
            break
        if name_match is None and name_m and \
           strip_colors(_fm_unquote(name_m.group(1))).lower() == \
           strip_colors(deck["name"]).lower():
            name_match = candidate
    match = url_match or name_match
    if match and not force:
        sys.exit(f"Note already exists (use --force to overwrite): {match}")
    if match:
        note_path = match
    else:
        others = list(out_dir.glob(f"{safe_name}*.md"))
        base = safe_name if not others else \
            f"{safe_name} - {ILLEGAL_FILENAME_CHARS.sub('', deck['name'])}"
        if colors:  # new notes carry the colour identity in the filename
            base = f"{base} {colors}"
        note_path = out_dir / f"{base}.md"
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
    apply_pins(prices, deck.get("pins"))
    report = price_report(decklist, prices)

    collection_name, buy = None, None
    owned = report_collection_state(
        out_dir, "this note is written WITHOUT its 🛒 Cards to Complete "
                 "sections — no owned counts, no buy list, no cost to finish")
    if owned:
        collection_name = collection_path(out_dir).name
        buy = buy_report(decklist, owned, prices)

    reviews = extract_reviews(note_path.read_text(encoding="utf-8")) \
        if note_path.exists() else {}
    if reviews:
        print(f"Preserved: your written content in {len(reviews)} review section(s)")

    choices = budget_choices(decklist, prices)
    cheap = cheapest_buy(buy, choices, report["rates"]) if buy else None
    history, alerts = record_history(out_dir, deck_id, deck["name"], report,
                                     buy, cheap, choices)
    shape_section = render_deck_shape(deck_shape(decklist, prices))
    new_text = build_note(deck, decklist, image_url, deck_url, report, buy,
                          collection_name, reviews, choices, deck_id, history,
                          shape_section, colors)
    # build_note stamps created: today. On a refresh of an existing note
    # (--force / --recheck) keep the ORIGINAL created date instead of resetting
    # it — matching --reimport and _recheck_from_stored.
    if note_path.exists():
        old_created = re.search(r"^created: (.+)$",
                                note_path.read_text(encoding="utf-8"), re.M)
        if old_created:
            new_text = re.sub(r"^created: .*$",
                              f"created: {old_created.group(1).strip()}",
                              new_text, count=1, flags=re.M)
    note_path.write_text(new_text, encoding="utf-8")

    if txt_src:
        imports_dir = out_dir / "imports"  # in the vault → syncs to every machine
        imports_dir.mkdir(exist_ok=True)
        dest = imports_dir / txt_src.name
        if txt_src != dest.resolve():
            shutil.move(str(txt_src), dest)  # move() survives cross-volume vaults
            print(f"Archived:  {dest}")
    update_deck_index(out_dir)

    total = sum(qty for qty, _ in decklist)
    totals = report["totals"]
    print(f"Deck:      {deck['name']} (id {deck_id})")
    print(f"Commander: {', '.join(deck['commanders'])}")
    print(f"Colours:   {colors or 'unknown'}")
    print(f"Cards:     {total} ({len(decklist)} unique)")
    rates = report["rates"]
    gbp = f" / GBP {totals['eur'] * rates['eur_gbp']:,.2f}" if rates else ""
    print(f"Value:     ~EUR {totals['eur']:,.2f}{gbp} / USD {totals['usd']:,.2f}"
          f" / TIX {totals['tix']:,.2f}"
          + (f"  ({len(report['unpriced'])} unpriced)" if report["unpriced"] else ""))
    if buy is not None and cheap is not None:
        print(f"Owned:     {buy['owned_unique']}/{buy['unique']} cards"
              f" — to buy: {len(buy['missing'])} (~EUR {buy['totals']['eur']:,.2f}"
              f" / ~EUR {cheap['totals']['eur']:,.2f} at cheapest versions)")
    else:
        print("Owned:     not compared — see the Collection line above")
    for line in alerts:
        print(line)
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


def _note_pins(text: str) -> dict[str, dict]:
    """Pins stored in a note's 📜 Deck List block — `(SET) 123`, `*F*`,
    `[Category]`. The block keeps an import's full-fidelity lines, so a
    stored-list rebuild re-prices the exact versions the import named rather
    than quietly reverting to Scryfall's defaults.
    """
    block = re.search(r"## 📜 Deck List\s*```\n(.*?)```", text, re.S)
    pins: dict[str, dict] = {}
    if not block:
        return pins
    for line in block.group(1).splitlines():
        p = parse_card_line_full(line)
        if p and ((p[2] and p[3]) or p[4] or p[5]):
            pins.setdefault(p[1].lower(), {"set": p[2], "num": p[3],
                                           "foil": p[4], "cat": p[5]})
    return pins


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
            print(f"[{did}] {deck_name_of(note)}: no deck-url — skipped")
            continue
        deck_url = url_m.group(1).strip()

        deck = None
        try:
            deck = fetch_deck(resolve_source(deck_url, out_dir))
        except (SystemExit, Exception) as exc:  # any failure → stored-list fallback
            print(f"[{did}] {deck_name_of(note)}: live fetch failed ({exc}) — "
                  "using the list stored in the note")
        if deck and deck.get("commanders"):
            mainboard = sorted(deck["mainboard"], key=lambda c: c[1].lower())
            decklist = [(1, n) for n in deck["commanders"]] + mainboard
            primary = deck["commanders"][0]
            pins = deck.get("pins")
        else:
            decklist = _note_decklist(text)
            if not decklist:
                print(f"[{did}] {deck_name_of(note)}: no stored list to fall back on — skipped")
                continue
            primary = decklist[0][1]  # listing puts the commander first
            pins = _note_pins(text)

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

        # Deck List block — replace with the (possibly updated) list, keeping
        # any pins/foil/category decorations the import carried
        listing = render_deck_listing(decklist, pins)
        text = re.sub(r"(## 📜 Deck List\s*```\n).*?(```)",
                      lambda m: f"{m.group(1)}{listing}\n{m.group(2)}",
                      text, count=1, flags=re.S)

        # Card gallery — fresh images only, no price lookups; pinned cards
        # show the printing the import named
        imgs = fetch_card_images([n for _, n in decklist], pins)
        cells = [(imgs.get(name.lower()), f"{name}{f' ×{qty}' if qty > 1 else ''}")
                 for qty, name in decklist]
        gallery = render_card_gallery(cells)
        if "### 🖼️ Card Gallery" in text:
            text = re.sub(r"### 🖼️ Card Gallery\n.*?(?=\n## 📜 Deck List)",
                          lambda _: gallery, text, count=1, flags=re.S)
        else:
            text = text.replace("## 📜 Deck List", f"{gallery}\n\n## 📜 Deck List", 1)

        # --reimport never rebuilds from the template, so refresh the Contents
        # table here too — it's idempotent, and a spliced note can gain or lose
        # its 🖼️ gallery/section shape.
        note.write_text(insert_toc(text), encoding="utf-8")
        changed = (f" — list changed, run --recheck {did} to refresh prices"
                   if list_changed else "")
        print(f"[{did}] {deck_name_of(note)}: list + art refreshed "
              f"({len(decklist)} cards){changed}")

    # A changed deck list can change a note's card count, so keep the master
    # index in step — every other write path already rebuilds it.
    update_deck_index(out_dir)


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
        help="full re-import from each deck's original source — list, prices, "
             "galleries and buy lists (art reused unless the commander "
             "changed). Every note, or one by ID (see --list). Falls back to "
             "re-pricing a note's stored list if its source is unreachable")
    parser.add_argument(
        "--reimport", nargs="?", const="__all__", default=None, metavar="ID",
        help="refresh deck lists and card art from source WITHOUT new prices; "
             "all notes, or one by ID (see --list)")
    parser.add_argument(
        "--list", action="store_true", dest="list_ids",
        help="list every deck's id and name, then exit")
    parser.add_argument(
        "--delete", nargs="?", const="__ask__", default=None, metavar="ID",
        help="delete a deck and everything it owns (note, art, archived .txt, "
             "brief, price history). No ID: list decks and ask which. Always "
             "reindexes afterwards so ids stay gap-free")
    parser.add_argument(
        "--reindex", action="store_true",
        help="renumber every deck note to a gap-free, unique 1..N deck-id "
             "sequence (fixing missing/duplicate ids), remap price history and "
             "rebuild the index; runs automatically after --delete")
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="skip the confirmation prompt on --delete")
    parser.add_argument(
        "--collection-value", action="store_true", dest="collection_value",
        help="price your _Collection.md (basics excluded) and write a "
             "💰 Collection Value section into it")
    parser.add_argument(
        "--set", nargs="?", const="__all__", default=None, metavar="WHAT",
        dest="set_codes",
        help="set-collection checklist: one tickable line per card, grouped by "
             "rarity. No argument refreshes every checklist you already have. "
             "Otherwise a checklist's name, a preset (ff), or Scryfall set "
             "codes (fin,fic) to build a new one. Refreshing re-prices "
             "everything and keeps your ticks")
    parser.add_argument(
        "--set-label", metavar="NAME", dest="set_label",
        help="friendly name for the --set note (default: the set codes)")
    parser.add_argument(
        "--set-reset", action="store_true", dest="set_reset",
        help="with --set: discard existing ticks and rederive them from the "
             "collection file (use after correcting how ownership is matched)")
    parser.add_argument(
        "--collection", metavar="FILE", dest="collection",
        help="create the collection file from a card-list export (a plain "
             "alphabetical 'N Card Name' list). Refuses to overwrite a "
             "collection that already has cards unless --force is given")
    parser.add_argument(
        "--merge-collection", metavar="FILE", dest="merge_collection",
        help="diff a full owned-cards export against _Collection.md and "
             "append what's missing (append-only; removals only reported)")
    parser.add_argument(
        "--brief", nargs="?", const="__all__", default=None, metavar="ID",
        help="write a compact analysis brief per deck (all, or one by ID) "
             "into the vault's _analysis-briefs/ — input for /analyse-deck")
    parser.add_argument(
        "--colorize", action="store_true",
        help="one-shot backfill: append each deck's commander colour identity "
             "(⚪🔵⚫🔴🟢 circles) to its note filename, deck-name and title. "
             "New imports get the suffix automatically; this stamps the decks "
             "imported before the feature existed, rewrites wiki-links to the "
             "renamed notes and rebuilds the index")
    parser.add_argument(
        "--index", action="store_true",
        help="regenerate the _Decks.md master index from the notes' current "
             "frontmatter (no network; also runs after every import/recheck)")
    parser.add_argument(
        "--refresh-db", action="store_true", dest="refresh_db",
        help="rebuild the local Scryfall reference DB even if it is still "
             "fresh (it otherwise refreshes itself once a day)")
    parser.add_argument(
        "--no-bulk", action="store_true", dest="no_bulk",
        help="skip the reference DB and look printings up through the API "
             "instead — slower and rate-limited, but no ~80 MB download")
    return parser


def _parse_id(val: str) -> int:
    try:
        return int(val)
    except ValueError:
        if "://" in val or val.lower().endswith(".txt"):
            sys.exit(f"That option takes a deck id, not a source.\n"
                     f"To (re-)import that deck from its source run:\n"
                     f"  python mtg_deck_importer.py --force \"{val}\"\n"
                     "or find its id with --list and pass the number instead.")
        sys.exit(f"Deck id must be a number, got {val!r} — run --list to see ids.")


def main() -> None:
    # The console output (and --help) is emoji-heavy; force stdout to UTF-8 so
    # a legacy Windows code page (cp1252) degrades to '?' instead of crashing a
    # print mid-run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_arg_parser()
    args = parser.parse_args()

    global _bulk_enabled, _bulk_force
    _bulk_enabled = not args.no_bulk
    _bulk_force = args.refresh_db
    if args.refresh_db and not any(
            [args.source, args.recheck, args.reimport, args.set_codes,
             args.collection, args.merge_collection, args.collection_value]):
        scryfall_db()  # --refresh-db on its own: just rebuild and stop
        return

    if args.list_ids:
        list_decks(resolve_out_dir())
        return
    if args.delete is not None:
        if args.source:
            parser.error("--delete takes an optional deck id, not a URL/file.")
        out_dir = resolve_out_dir()
        target = (choose_deck_to_delete(out_dir) if args.delete == "__ask__"
                  else _parse_id(args.delete))
        if target is not None and delete_deck(out_dir, target, args.yes):
            reindex(out_dir)
        return
    if args.reindex:
        if args.source:
            parser.error("--reindex takes no other arguments.")
        reindex(resolve_out_dir())
        return
    if args.colorize:
        if args.source:
            parser.error("--colorize takes no other arguments.")
        colorize_decks(resolve_out_dir())
        return
    if args.index:
        if args.source:
            parser.error("--index takes no other arguments.")
        out_dir = resolve_out_dir()
        update_deck_index(out_dir)
        print(f"Updated:   {DECKS_INDEX} in {out_dir}")
        return
    if args.collection_value:
        if args.source:
            parser.error("--collection-value takes no other arguments.")
        collection_value(resolve_out_dir())
        return
    if args.set_codes is not None:
        if args.source:
            parser.error("--set takes a checklist name, preset or set codes, "
                         "not a deck URL/file.")
        out_dir = resolve_out_dir()
        if args.set_codes == "__all__":
            notes = collection_notes(out_dir)
            if not notes:
                sys.exit("No set checklists yet. Create one with a preset or set "
                         "codes, e.g.:\n"
                         "  python mtg_deck_importer.py --set ff\n"
                         "  python mtg_deck_importer.py --set fin,fic "
                         "--set-label \"Final Fantasy\"")
            for note in notes:
                codes = _note_set_codes(note)
                if not codes:
                    print(f"Skipped:   {note.name} has no set-codes: line")
                    continue
                set_collection(out_dir, codes, args.set_label or _note_label(note),
                               reset=args.set_reset)
            return
        codes, label = resolve_set_target(out_dir, args.set_codes)
        if not codes:
            parser.error("--set needs a checklist name, a preset, or set codes.")
        set_collection(out_dir, codes, args.set_label or label,
                       reset=args.set_reset)
        return
    if args.collection:
        if args.source:
            parser.error("--collection takes the list file as its own "
                         "argument — no deck URL/file alongside it.")
        create_collection(resolve_out_dir(), args.collection, force=args.force)
        return
    if args.merge_collection:
        if args.source:
            parser.error("--merge-collection takes the list file as its own "
                         "argument — no deck URL/file alongside it.")
        merge_collection(resolve_out_dir(), args.merge_collection)
        return
    if args.brief is not None:
        if args.source:
            parser.error("--brief takes an optional deck id, not a URL/file.")
        out_dir = resolve_out_dir()
        ids = deck_id_map(out_dir)
        targets = ids if args.brief == "__all__" else \
            {_parse_id(args.brief): ids.get(_parse_id(args.brief))}
        for did, note in sorted(targets.items()):
            if note is None:
                sys.exit(f"No deck with id {did}. Run --list to see the ids.")
            write_brief(out_dir, did, note)
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
