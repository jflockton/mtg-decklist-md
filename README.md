# 🃏 mtg-decklist-md

**Deck URL in → Obsidian deck note out.** ✨

Give it a deck link and it drops a ready-to-review markdown note into your
Obsidian vault — commander artwork, review sections for your thoughts, and the
full deck list in clean copy-paste format.

```
python mtg_deck_importer.py https://moxfield.com/decks/<public_id>
python mtg_deck_importer.py https://edhrec.com/deckpreview/<hash>
python mtg_deck_importer.py "path/to/My Deck Name.txt"
```

## 🌐 Supported sources

| Source | How it's fetched |
|--------|------------------|
| 🟣 [Moxfield](https://moxfield.com) — `…/decks/<id>` | Headed Chrome (Cloudflare — see below) |
| 🟠 [EDHREC](https://edhrec.com) — `…/deckpreview/<hash>` | Plain HTTP — no browser window 🎉 |
| 📄 Local `.txt` decklist | Read directly — no network for the list itself |

For a `.txt` file: one `1 Card Name` per line (the usual export format),
**first card is the commander**, and the file name becomes the deck name —
`Krenko Goblin Swarm.txt` → deck "Krenko Goblin Swarm". Artwork and prices
still come from Scryfall as normal.

🛡️ An existing deck note is **never overwritten** (your review notes live in
it) — pass `--force` to regenerate one anyway.

## 📦 What you get

Two files in your configured vault folder:

- 📝 `YYYY-MM-DD_MTG_<Commander Name>.md` — the deck note:
  - 🖼️ commander card image — embedded as a standard markdown link to
    Scryfall's hosted image, so it renders in **any** markdown viewer (not
    just Obsidian); a local copy is still saved to `Attachments/` as an
    offline backup
  - 🧠 **First Impressions** · 💪 **Strengths** · ⚠️ **Weaknesses** ·
    🔄 **Cards to Consider Swapping** · 📝 **Play Notes** — empty headings
    ready for your review
  - 💰 **Deck Value + card prices** — deck totals per source with a ≈ GBP
    column (also in the frontmatter for Dataview), a top-10 Priciest Cards
    table, and a full per-card price table sorted dearest-first — every
    price table shows native EUR / USD plus ≈ GBP
  - 📜 **Deck List** — commander first, then mainboard alphabetically, one
    `1 Card Name` per line (pastes straight back into Moxfield / Arena)
- 🎨 `Attachments/YYYY-MM-DD_MTG_<Commander Name>.jpg` — the commander card
  from Scryfall (the `Attachments` subfolder is created if missing)

## 🤔 Why does a Chrome window pop up (Moxfield only)?

Moxfield sits behind Cloudflare bot protection: 🚫 plain `requests`/`curl`
get HTTP 403 no matter the headers, and 🚫 headless browsers get the
"Attention Required!" block page. ✅ A normal headed Chrome passes, so the
app opens a real Chrome window for a few seconds per run and fetches the deck
JSON from inside that page. No fingerprint spoofing or stealth tricks — if
Moxfield ever blocks this too, the fallback is their built-in Export button
(same text format).

Commander artwork comes from 🔓 [Scryfall's open API](https://scryfall.com/docs/api),
which needs no browser (just polite `User-Agent`/`Accept` headers).

## 💰 Where do the prices come from?

Scryfall's daily price snapshot, always for the **standard (non-foil) card**,
totalled into four estimates shown right under the note's Source line:

| Source | Currency | Market |
|--------|----------|--------|
| 🇪🇺 [Cardmarket](https://www.cardmarket.com/en/Magic) | EUR | European paper singles |
| 🇺🇸 [TCGPlayer](https://www.tcgplayer.com) | USD | US paper singles |
| 🖥️ [Cardhoarder](https://www.cardhoarder.com) | tix | Magic Online (1 tix ≈ $1) |

Every row — and every card in the Priciest Cards / All Card Prices tables —
also gets a **≈ GBP column**: EUR/USD converted at the ECB reference rates
([frankfurter.dev](https://frankfurter.dev)), tix via the ~$1 convention. (Scryfall itself isn't a marketplace — it's the aggregator
all these numbers come from. If the exchange-rate API is unreachable, the
GBP cells show a dash.)

There's no reliable GBP source (and no, not eBay — auction listings aren't a
price index 😄). Most cards are priced in one bulk lookup; when Scryfall's
default printing is an online-only set with no paper price (it happens — e.g.
Tempest Remastered Mox Diamond), the app falls back to the **cheapest paper
printing**. Each row shows how many of the deck's cards that source actually
priced. Treat the totals as fair estimates, not valuations.

## 🚀 Setup

Needs 🐍 Python 3.10+ and 🌐 Google Chrome installed (the app drives the
system Chrome for Moxfield decks; EDHREC needs no browser).

```
pip install -r requirements.txt
```

### ⚙️ Configuration

Copy `.env.example` to `.env` and point it at the folder in **your** vault
where deck notes should land:

```
VAULT_OUTPUT_DIR=C:/path/to/your/vault/MTG
```

That's the only setting. 📌 A real `VAULT_OUTPUT_DIR` environment variable
takes priority over the `.env` file if you'd rather set it system-wide, and
the `.env` file itself is gitignored — your vault path stays off GitHub.

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

- 🃏 Deck data: [Moxfield](https://moxfield.com) & [EDHREC](https://edhrec.com)
- 🎨 Card data & images: [Scryfall](https://scryfall.com)
- Card images © Wizards of the Coast — personal use only
