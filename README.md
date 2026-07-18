# 🃏 mtg-decklist-md

**Moxfield deck URL in → Obsidian deck note out.** ✨

Give it a Moxfield deck link and it drops a ready-to-review markdown note into
your Obsidian vault — commander artwork, review sections for your thoughts,
and the full deck list in clean copy-paste format.

```
python mtg_deck_importer.py https://moxfield.com/decks/<public_id>
```

## 📦 What you get

Two files in your configured vault folder:

- 📝 `YYYY-MM-DD_MTG_<Commander Name>.md` — the deck note:
  - 🖼️ commander card image (embedded, offline-safe)
  - 🧠 **First Impressions** · 💪 **Strengths** · ⚠️ **Weaknesses** ·
    🔄 **Cards to Consider Swapping** · 📝 **Play Notes** — empty headings
    ready for your review
  - 📜 **Deck List** — commander first, then mainboard alphabetically, one
    `1 Card Name` per line (pastes straight back into Moxfield / Arena)
- 🎨 `Attachments/YYYY-MM-DD_MTG_<Commander Name>.jpg` — the commander card
  from Scryfall (the `Attachments` subfolder is created if missing)

## 🤔 Why does a Chrome window pop up?

Moxfield sits behind Cloudflare bot protection: 🚫 plain `requests`/`curl`
get HTTP 403 no matter the headers, and 🚫 headless browsers get the
"Attention Required!" block page. ✅ A normal headed Chrome passes, so the
app opens a real Chrome window for a few seconds per run and fetches the deck
JSON from inside that page. No fingerprint spoofing or stealth tricks — if
Moxfield ever blocks this too, the fallback is their built-in Export button
(same text format).

Commander artwork comes from 🔓 [Scryfall's open API](https://scryfall.com/docs/api),
which needs no browser (just polite `User-Agent`/`Accept` headers).

## 🚀 Setup

Needs 🐍 Python 3.10+ and 🌐 Google Chrome installed (the app drives the
system Chrome).

```
pip install -r requirements.txt
```

Point `config.json` at your vault's output folder:

```json
{
  "vault_output_dir": "C:/path/to/vault/03 - Personal/2026-07-18_MTG"
}
```

## 🎮 Usage

```
python mtg_deck_importer.py https://moxfield.com/decks/Na_36cWsnEOhEGT_o27XgQ
```

```
Deck:      sisterhood of the traveling pants (auraboros)
Commander: Cass, Hand of Vengeance
Cards:     100 (74 unique)
Note:      ...\2026-07-18_MTG_Cass, Hand of Vengeance.md
Artwork:   ...\Attachments\2026-07-18_MTG_Cass, Hand of Vengeance.jpg
```

## 🙏 Credits

- 🃏 Deck data: [Moxfield](https://moxfield.com)
- 🎨 Card data & images: [Scryfall](https://scryfall.com)
- Card images © Wizards of the Coast — personal use only
