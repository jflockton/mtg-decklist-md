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

Fetches the deck list (Moxfield via a headed browser because of Cloudflare;
EDHREC via plain HTTP), downloads the commander's artwork from Scryfall, and
writes a markdown deck note into the Obsidian vault folder set by the
VAULT_OUTPUT_DIR environment variable (usually via a .env file next to this
script — see .env.example).

Output note:  YYYY-MM-DD_MTG_<Commander Name>.md
Output image: Attachments/YYYY-MM-DD_MTG_<Commander Name>.jpg
"""

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


def card_aliases(name: str) -> set[str]:
    """All names a card can appear under: its canonical name plus Universes
    Beyond flavor names (e.g. the Marvel precons print Spark Double as
    "Loki's Double") — deck sources and collection lists may use either.
    """
    aliases = {name.lower()}
    r = requests.get(SCRYFALL_NAMED, params={"exact": name},
                     headers=HTTP_HEADERS, timeout=30)
    time.sleep(0.1)
    if r.status_code != 200:
        return aliases
    canonical = r.json()["name"]
    aliases.add(canonical.lower())
    s = requests.get(SCRYFALL_SEARCH,
                     params={"q": f'!"{canonical}"', "unique": "prints"},
                     headers=HTTP_HEADERS, timeout=30)
    time.sleep(0.1)
    if s.status_code == 200:
        for c in s.json().get("data", []):
            if c.get("flavor_name"):
                aliases.add(c["flavor_name"].lower())
    return aliases


def buy_report(decklist: list[tuple[int, str]], owned: dict[str, int],
               prices: dict[str, dict]) -> dict:
    """Which deck cards are missing from the collection, and what the gap
    costs (per-source totals use the already-fetched deck prices).
    """
    missing = []
    totals = {"eur": 0.0, "usd": 0.0}
    owned_unique = owned_copies = total_copies = 0
    for qty, name in decklist:
        total_copies += qty
        have = owned.get(name.lower(), 0)
        if have < qty:
            # Not obviously owned — check flavor-name aliases before giving up
            have = sum(owned.get(a, 0) for a in card_aliases(name))
        have = min(have, qty)
        owned_copies += have
        if have >= qty:
            owned_unique += 1
            continue
        need = qty - have
        p = prices.get(name.lower()) or {}
        totals["eur"] += (p.get("eur") or 0) * need
        totals["usd"] += (p.get("usd") or 0) * need
        missing.append((need, name, p.get("eur"), p.get("usd")))
    missing.sort(key=lambda c: c[2] or 0, reverse=True)
    return {"missing": missing, "totals": totals,
            "owned_unique": owned_unique, "owned_copies": owned_copies,
            "unique": len(decklist), "total_copies": total_copies}


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
    }


FETCHERS = [
    (re.compile(r"moxfield\.com/decks/"), fetch_moxfield),
    (re.compile(r"edhrec\.com/deckpreview"), fetch_edhrec),
]


def fetch_deck(url: str) -> dict:
    for pattern, fetcher in FETCHERS:
        if pattern.search(url):
            return fetcher(url)
    if url.lower().endswith(".txt") and Path(url).is_file():
        return fetch_textfile(url)
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
    r = requests.get(SCRYFALL_NAMED, params={"exact": commander},
                     headers=HTTP_HEADERS, timeout=30)
    r.raise_for_status()
    card = r.json()
    # Double-faced cards keep image_uris per face
    image_uris = card.get("image_uris") or card["card_faces"][0]["image_uris"]
    time.sleep(0.1)  # Scryfall asks for ~10 requests/sec max
    img = requests.get(image_uris["normal"], headers=HTTP_HEADERS, timeout=30)
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
        r = requests.post(
            SCRYFALL_COLLECTION,
            json={"identifiers": [{"name": n} for n in chunk]},
            headers=HTTP_HEADERS, timeout=30,
        )
        r.raise_for_status()
        for card in r.json().get("data", []):
            p = card.get("prices", {})
            entry = {
                "eur": float(p["eur"]) if p.get("eur") else None,
                "usd": float(p["usd"]) if p.get("usd") else None,
                "tix": float(p["tix"]) if p.get("tix") else None,
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
            prices[name.lower()] = cheap
        time.sleep(0.1)
    return prices


def cheapest_paper_printing(name: str) -> dict | None:
    r = requests.get(
        SCRYFALL_SEARCH,
        params={"q": f'!"{name}" game:paper', "unique": "prints"},
        headers=HTTP_HEADERS, timeout=30,
    )
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
    totals = {"eur": 0.0, "usd": 0.0, "tix": 0.0}
    coverage = {"eur": 0, "usd": 0, "tix": 0}
    unpriced = []
    priced_cards = []
    all_cards = []
    for qty, name in decklist:
        p = prices.get(name.lower())
        if not p or (p["eur"] is None and p["usd"] is None):
            unpriced.append(name)
            all_cards.append((qty, name, None, None))
            continue
        for src in totals:
            if p.get(src) is not None:
                totals[src] += p[src] * qty
                coverage[src] += 1
        priced_cards.append((name, p["eur"], p["usd"]))
        all_cards.append((qty, name, p["eur"], p["usd"]))
    top = sorted(priced_cards, key=lambda c: c[1] or 0, reverse=True)[:10]
    all_cards.sort(key=lambda c: c[2] or 0, reverse=True)
    return {"totals": totals, "coverage": coverage, "unique": len(decklist),
            "top": top, "all": all_cards, "unpriced": unpriced,
            "rates": fx_rates()}


def build_note(deck: dict, decklist: list[tuple[int, str]],
               image_url: str, deck_url: str, report: dict,
               buy: dict | None, collection_name: str | None) -> str:
    today = date.today().isoformat()
    commander_line = ", ".join(deck["commanders"])
    listing = "\n".join(f"{qty} {name}" for qty, name in decklist)
    totals = report["totals"]
    coverage = report["coverage"]
    unique = report["unique"]

    def money(eur, usd):
        e = f"€{eur:,.2f}" if eur is not None else "—"
        u = f"${usd:,.2f}" if usd is not None else "—"
        return e, u

    rates = report["rates"]

    def gbp_cell(amount):
        return f"£{amount:,.2f}" if amount is not None else "—"

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
        f"| {label} | {native} | {gbp_cell(gbp)} | {cov}/{unique} |"
        for label, native, gbp, cov in source_rows
    )
    fm_prices = {"eur": totals["eur"]}
    if rates:
        fm_prices["gbp"] = totals["eur"] * rates["eur_gbp"]
    fm_prices["usd"] = totals["usd"]
    fm_prices["tix"] = totals["tix"]
    price_frontmatter = "\n".join(f"price-{k}: {v:.2f}" for k, v in fm_prices.items())

    def card_gbp(eur, usd):
        # Prefer the Cardmarket EUR price; fall back to USD if only that exists
        if not rates:
            return None
        if eur is not None:
            return eur * rates["eur_gbp"]
        if usd is not None:
            return usd * rates["usd_gbp"]
        return None

    top_rows = "\n".join(
        f"| {name} | {money(eur, usd)[0]} | {money(eur, usd)[1]} | {gbp_cell(card_gbp(eur, usd))} |"
        for name, eur, usd in report["top"]
    )
    all_rows = "\n".join(
        f"| {name}{f' ×{qty}' if qty > 1 else ''} | {money(eur, usd)[0]} | {money(eur, usd)[1]} | {gbp_cell(card_gbp(eur, usd))} |"
        for qty, name, eur, usd in report["all"]
    )
    unpriced_note = (
        f"\n> ⚠️ No price found for {len(report['unpriced'])} card(s): "
        + ", ".join(report["unpriced"]) if report["unpriced"] else ""
    )

    buy_section = ""
    if buy is not None:
        summary = (f"Compared against `{collection_name}` — you own "
                   f"**{buy['owned_unique']}/{buy['unique']}** cards "
                   f"({buy['owned_copies']}/{buy['total_copies']} copies).")
        if buy["missing"]:
            buy_gbp = gbp_cell(buy["totals"]["eur"] * rates["eur_gbp"] if rates else None)
            buy_rows = "\n".join(
                f"| {name} | {need} | {money(eur, usd)[0]} | {money(eur, usd)[1]} | {gbp_cell(card_gbp(eur, usd))} |"
                for need, name, eur, usd in buy["missing"]
            )
            buy_section = f"""## 🛒 Cards to Buy

{summary}
Completing the deck ≈ **€{buy["totals"]["eur"]:,.2f} · ${buy["totals"]["usd"]:,.2f} · {buy_gbp}** (prices per copy below).

| Card | Need | EUR | USD | ≈ GBP |
|------|-----:|----:|----:|------:|
{buy_rows}

"""
        else:
            buy_section = f"""## 🛒 Cards to Buy

{summary}
🎉 **You own every card in this deck — nothing to buy!**

"""
        price_frontmatter += f"\nowned: {buy['owned_unique']}/{buy['unique']}"
        price_frontmatter += f"\nbuy-eur: {buy['totals']['eur']:.2f}"
        if rates:
            price_frontmatter += f"\nbuy-gbp: {buy['totals']['eur'] * rates['eur_gbp']:.2f}"

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

| Source | Value | ≈ GBP | Cards priced |
|--------|------:|------:|-------------:|
{value_rows}

*💰 Standard (non-foil) cards, Scryfall daily snapshot ({today}). ≈ GBP is rough — ECB reference rates via frankfurter.dev; 1 tix ≈ $1.*

![{commander_line}|290]({image_url})

## 🧠 First Impressions

-

## 💪 Strengths

-

## ⚠️ Weaknesses

-

## 🔄 Cards to Consider Swapping

-

## 📝 Play Notes

-

{buy_section}## 🏆 Priciest Cards

| Card | EUR | USD | ≈ GBP |
|------|----:|----:|------:|
{top_rows}

### 💵 All Card Prices

Per-card prices (×N marks multiples — basics etc.; the price shown is per copy).

| Card | EUR | USD | ≈ GBP |
|------|----:|----:|------:|
{all_rows}
{unpriced_note}
## 📜 Deck List

```
{listing}
```
"""


def main() -> None:
    argv = sys.argv[1:]
    force = "--force" in argv
    own = "--own" in argv
    argv = [a for a in argv if a not in ("--force", "--own")]
    if len(argv) != 1:
        sys.exit(__doc__)
    deck_url = argv[0]
    out_dir = output_dir()
    if not out_dir.is_dir():
        sys.exit(f"Vault output folder does not exist: {out_dir}")

    deck = fetch_deck(deck_url)
    if not deck["commanders"]:
        sys.exit("No commander found on this deck — is it a Commander deck?")
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

    note_path.write_text(
        build_note(deck, decklist, image_url, deck_url, report, buy, collection_name),
        encoding="utf-8",
    )

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
