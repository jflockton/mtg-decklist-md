"""MTG Deck Importer — Moxfield deck URL -> Obsidian vault deck note.

Usage:
    python mtg_deck_importer.py https://moxfield.com/decks/<public_id>

Fetches the deck list from Moxfield's API (via a headless browser, because
Moxfield sits behind Cloudflare and rejects plain HTTP clients), downloads the
commander's artwork from Scryfall, and writes a markdown deck note into the
Obsidian vault folder configured in config.json.

Output note:  YYYY-MM-DD_MTG_<Commander Name>.md
Output image: YYYY-MM-DD_MTG_<Commander Name>.jpg
"""

import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"

SCRYFALL_NAMED = "https://api.scryfall.com/cards/named"
MOXFIELD_API = "https://api2.moxfield.com/v3/decks/all/{deck_id}"

# Windows-illegal filename characters (commas are fine and kept)
ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def deck_id_from_url(url: str) -> str:
    m = re.search(r"moxfield\.com/decks/([A-Za-z0-9_-]+)", url)
    if not m:
        sys.exit(f"Could not find a deck id in URL: {url}")
    return m.group(1)


def fetch_deck(deck_id: str) -> dict:
    """Fetch deck JSON from Moxfield's API from inside a real browser context.

    Cloudflare fingerprints the client, so requests/curl get 403 and headless
    browsers get the "Attention Required!" block page. A headed system Chrome
    passes normally, so a visible window appears for a few seconds per run.
    """
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
    return json.loads(result["body"])


def board_cards(deck: dict, board: str) -> list[tuple[int, str]]:
    cards = deck.get("boards", {}).get(board, {}).get("cards", {})
    return [(c["quantity"], c["card"]["name"]) for c in cards.values()]


def fetch_commander_art(commander: str, dest: Path) -> None:
    # Scryfall rejects requests without proper User-Agent/Accept headers
    headers = {
        "User-Agent": "mtg-deck-importer/1.0 (personal Obsidian tool)",
        "Accept": "*/*",
    }
    r = requests.get(SCRYFALL_NAMED, params={"exact": commander}, headers=headers, timeout=30)
    r.raise_for_status()
    card = r.json()
    # Double-faced cards keep image_uris per face
    image_uris = card.get("image_uris") or card["card_faces"][0]["image_uris"]
    time.sleep(0.1)  # Scryfall asks for ~10 requests/sec max
    img = requests.get(image_uris["normal"], headers=headers, timeout=30)
    img.raise_for_status()
    dest.write_bytes(img.content)


def build_note(deck: dict, commanders: list[str], decklist: list[tuple[int, str]],
               image_filename: str, deck_url: str) -> str:
    today = date.today().isoformat()
    commander_line = ", ".join(commanders)
    listing = "\n".join(f"{qty} {name}" for qty, name in decklist)
    return f"""---
tags: [mtg, deck, commander]
created: {today}
commander: {commander_line}
deck-url: {deck_url}
---

# 🃏 {deck["name"]}

**Commander:** {commander_line}
**Format:** {deck.get("format", "commander").title()}
**Source:** [Moxfield]({deck_url})

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
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    deck_url = sys.argv[1]
    config = load_config()
    out_dir = Path(config["vault_output_dir"])
    if not out_dir.is_dir():
        sys.exit(f"Vault output folder does not exist: {out_dir}")

    deck = fetch_deck(deck_id_from_url(deck_url))
    commanders = sorted(name for _, name in board_cards(deck, "commanders"))
    if not commanders:
        sys.exit("No commander found on this deck — is it a Commander deck?")
    mainboard = sorted(board_cards(deck, "mainboard"), key=lambda c: c[1].lower())
    decklist = [(1, name) for name in commanders] + mainboard

    primary = commanders[0]
    safe_name = ILLEGAL_FILENAME_CHARS.sub("", primary)
    stem = f"{date.today().isoformat()}_MTG_{safe_name}"

    attachments_dir = out_dir / "Attachments"
    attachments_dir.mkdir(exist_ok=True)
    image_path = attachments_dir / f"{stem}.jpg"
    fetch_commander_art(primary, image_path)

    note_path = out_dir / f"{stem}.md"
    note_path.write_text(
        build_note(deck, commanders, decklist, image_path.name, deck_url),
        encoding="utf-8",
    )

    total = sum(qty for qty, _ in decklist)
    print(f"Deck:      {deck['name']}")
    print(f"Commander: {', '.join(commanders)}")
    print(f"Cards:     {total} ({len(decklist)} unique)")
    print(f"Note:      {note_path}")
    print(f"Artwork:   {image_path}")


if __name__ == "__main__":
    main()
