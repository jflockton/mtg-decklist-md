"""MTG Deck Importer — deck URL -> Obsidian vault deck note.

Usage:
    python mtg_deck_importer.py [--force] https://moxfield.com/decks/<public_id>
    python mtg_deck_importer.py [--force] https://edhrec.com/deckpreview/<hash>

An existing deck note is never overwritten (your review notes live in it) —
pass --force to regenerate it anyway.

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
        "source": "Moxfield",
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
        "source": "EDHREC",
        "commanders": commanders,
        "mainboard": mainboard,
    }


FETCHERS = [
    (re.compile(r"moxfield\.com/decks/"), fetch_moxfield),
    (re.compile(r"edhrec\.com/deckpreview"), fetch_edhrec),
]


def fetch_deck(url: str) -> dict:
    for pattern, fetcher in FETCHERS:
        if pattern.search(url):
            return fetcher(url)
    supported = ", ".join(p.pattern.replace("\\", "") for p, _ in FETCHERS)
    sys.exit(f"Unsupported deck URL: {url}\nSupported sites: {supported}")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def fetch_commander_art(commander: str, dest: Path) -> None:
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


def eur_to_gbp_rate() -> float | None:
    """Current ECB reference rate via frankfurter.dev; None if unreachable."""
    try:
        r = requests.get(ECB_RATES, params={"base": "EUR", "symbols": "GBP"},
                         headers=HTTP_HEADERS, timeout=15)
        r.raise_for_status()
        return float(r.json()["rates"]["GBP"])
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
    rate = eur_to_gbp_rate()
    if rate is not None:
        totals["gbp"] = totals["eur"] * rate
        coverage["gbp"] = coverage["eur"]
    return {"totals": totals, "coverage": coverage, "unique": len(decklist),
            "top": top, "all": all_cards, "unpriced": unpriced}


def build_note(deck: dict, decklist: list[tuple[int, str]],
               image_filename: str, deck_url: str, report: dict) -> str:
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

    value_rows = "\n".join(
        f"| {label} | {sym}{totals[src]:,.2f} | {coverage[src]}/{unique} |"
        for src, label, sym in [
            ("eur", "🇪🇺 Cardmarket (EUR)", "€"),
            ("gbp", "💷 GBP estimate (Cardmarket EUR → £, ECB rate)", "£"),
            ("usd", "🇺🇸 TCGPlayer (USD)", "$"),
            ("tix", "🖥️ Cardhoarder (MTGO tix)", ""),
        ] if src in totals
    )
    price_frontmatter = "\n".join(
        f"price-{src}: {totals[src]:.2f}"
        for src in ("eur", "gbp", "usd", "tix") if src in totals
    )
    top_rows = "\n".join(
        f"| {name} | {money(eur, usd)[0]} | {money(eur, usd)[1]} |"
        for name, eur, usd in report["top"]
    )
    all_rows = "\n".join(
        f"| {name}{f' ×{qty}' if qty > 1 else ''} | {money(eur, usd)[0]} | {money(eur, usd)[1]} |"
        for qty, name, eur, usd in report["all"]
    )
    unpriced_note = (
        f"\n> ⚠️ No price found for {len(report['unpriced'])} card(s): "
        + ", ".join(report["unpriced"]) if report["unpriced"] else ""
    )
    return f"""---
tags: [mtg, deck, commander]
created: {today}
commander: {commander_line}
deck-url: {deck_url}
{price_frontmatter}
price-date: {today}
---

# 🃏 {deck["name"]}

**Commander:** {commander_line}
**Format:** {deck["format"]}
**Source:** [{deck["source"]}]({deck_url})

| Source | Value | Cards priced |
|--------|------:|-------------:|
{value_rows}

*💰 Standard (non-foil) cards, Scryfall daily snapshot ({today}).*

![[{image_filename}]]

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

## 🏆 Priciest Cards

| Card | EUR | USD |
|------|----:|----:|
{top_rows}

### 💵 All Card Prices

Per-card prices (×N marks multiples — basics etc.; the price shown is per copy).

| Card | EUR | USD |
|------|----:|----:|
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
    argv = [a for a in argv if a != "--force"]
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
    stem = f"{date.today().isoformat()}_MTG_{safe_name}"

    note_path = out_dir / f"{stem}.md"
    if note_path.exists() and not force:
        sys.exit(f"Note already exists (use --force to overwrite): {note_path}")

    attachments_dir = out_dir / "Attachments"
    attachments_dir.mkdir(exist_ok=True)
    image_path = attachments_dir / f"{stem}.jpg"
    fetch_commander_art(primary, image_path)

    report = price_report(decklist, fetch_prices([name for _, name in decklist]))

    note_path.write_text(
        build_note(deck, decklist, image_path.name, deck_url, report),
        encoding="utf-8",
    )

    total = sum(qty for qty, _ in decklist)
    totals = report["totals"]
    print(f"Deck:      {deck['name']}")
    print(f"Commander: {', '.join(deck['commanders'])}")
    print(f"Cards:     {total} ({len(decklist)} unique)")
    gbp = f" / GBP {totals['gbp']:,.2f}" if "gbp" in totals else ""
    print(f"Value:     ~EUR {totals['eur']:,.2f}{gbp} / USD {totals['usd']:,.2f}"
          f" / TIX {totals['tix']:,.2f}"
          + (f"  ({len(report['unpriced'])} unpriced)" if report["unpriced"] else ""))
    print(f"Note:      {note_path}")
    print(f"Artwork:   {image_path}")


if __name__ == "__main__":
    main()
