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


def build_note(deck: dict, decklist: list[tuple[int, str]],
               image_filename: str, deck_url: str) -> str:
    today = date.today().isoformat()
    commander_line = ", ".join(deck["commanders"])
    listing = "\n".join(f"{qty} {name}" for qty, name in decklist)
    return f"""---
tags: [mtg, deck, commander]
created: {today}
commander: {commander_line}
deck-url: {deck_url}
---

# 🃏 {deck["name"]}

**Commander:** {commander_line}
**Format:** {deck["format"]}
**Source:** [{deck["source"]}]({deck_url})

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

    note_path.write_text(
        build_note(deck, decklist, image_path.name, deck_url),
        encoding="utf-8",
    )

    total = sum(qty for qty, _ in decklist)
    print(f"Deck:      {deck['name']}")
    print(f"Commander: {', '.join(deck['commanders'])}")
    print(f"Cards:     {total} ({len(decklist)} unique)")
    print(f"Note:      {note_path}")
    print(f"Artwork:   {image_path}")


if __name__ == "__main__":
    main()
