# MTG Deck Importer

Give it a Moxfield deck URL, get an Obsidian deck note with the full card list
and the commander's artwork, dropped straight into the vault.

## Why Playwright (and why a browser window pops up)?

Moxfield sits behind Cloudflare bot protection — plain `requests`/`curl` get
HTTP 403 no matter the headers, and headless browsers get the "Attention
Required!" block page. A normal headed Chrome passes, so the app opens a real
Chrome window for a few seconds per run and fetches the deck JSON from inside
that page. No fingerprint spoofing or stealth tricks — if Moxfield ever blocks
this too, the fallback is their built-in Export button (same text format).
Commander artwork comes from Scryfall's open API, which needs no browser.

## Setup

Needs Google Chrome installed (the app drives the system Chrome).

```
pip install -r requirements.txt
```

Set the vault output folder in `config.json` (default:
`03 - Personal/2026-07-18_MTG` inside the Obsidian vault).

## Usage

```
python mtg_deck_importer.py https://moxfield.com/decks/<public_id>
```

Output (into the configured vault folder):

- `YYYY-MM-DD_MTG_<Commander Name>.md` — deck note: commander image, full deck
  list (commander first, then mainboard alphabetically), and empty review
  sections (first impressions, strengths, weaknesses, swaps, play notes).
- `Attachments/YYYY-MM-DD_MTG_<Commander Name>.jpg` — commander card image
  from Scryfall (the `Attachments` subfolder is created if missing).
