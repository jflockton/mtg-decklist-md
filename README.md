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

Flags: `--force` regenerates an existing note; `--own` adds the whole deck
to your collection (see below). Run `--help` for the full list.

Every note carries a stable **deck id** in its frontmatter (`deck-id:`), so a
single deck can be targeted by number. `python mtg_deck_importer.py --list`
prints the id ↔ deck name for every note. Ids are assigned on the fly and
never renumber an existing deck.

**Updated your collection, or edited some decks?**
`python mtg_deck_importer.py --recheck` (no id) refreshes *every* deck note by
**re-importing it from its original source** — the Moxfield/EDHREC URL, or the
archived `.txt` in `./imports` — so deck edits, fresh prices, and fresh art
(commander image + both 🖼️ galleries) all land, along with the 💸 Cheapest
Build and the 🛒 Cards to Buy comparison against the current `_Collection.md`.
If a deck's source can't be reached (dead link, file gone, offline), that deck
falls back to a price/buy refresh from the list stored in the note, so the run
never stops and gone-source decks still update. Review sections are always
preserved. (Moxfield decks each open a Chrome window, and Scryfall rate-limits
heavy bursts — so a big library can take a while.)

**Just one deck?** `--recheck <id>` (e.g. `--recheck 7`) does the same **full
re-import** for a single deck: re-fetches its list from source, pulls fresh
prices, and refreshes the art — scoped to one note (review sections preserved).
Run `--list` to find the id.

**Fresh art without re-pricing?** `python mtg_deck_importer.py --reimport`
re-fetches every deck's list from its source (or one deck with
`--reimport <id>`), re-downloads the commander art, and rebuilds the 🖼️ card
gallery from fresh Scryfall images — but **skips the market-price refresh
entirely**. The price, buy, and 💸 Cheapest Build sections are left exactly as
they are (run `--recheck` to update those). Each list is re-fetched from its
source (Moxfield/EDHREC URL, or the archived `.txt`); if that fetch fails it
falls back to the deck list already stored in the note, so it works offline
too. Handy for backfilling galleries into older notes cheaply — if a live
re-fetch changes a deck's list, it flags the deck so you know to `--recheck`
it for matching prices.

## 🧾 Command reference

```
python mtg_deck_importer.py [--force] [--own] <source>
python mtg_deck_importer.py --recheck [id]
python mtg_deck_importer.py --reimport [id]
python mtg_deck_importer.py --list
python mtg_deck_importer.py --help
```

`<source>` is a Moxfield URL, an EDHREC deckpreview URL, or a path to a `.txt`
decklist.

| Option | What it does |
|--------|--------------|
| *(none)* | Import `<source>` into a new deck note (prices, buy list, galleries, art). Errors if a note for that deck already exists. |
| `--force` | Regenerate an **existing** note for `<source>` in place — refreshes the deck list, prices, galleries and art while **keeping your review sections**. |
| `--own` | Before comparing, append the whole deck to `_Collection.md` as owned (skipped if already listed). Use when you actually buy a wishlist precon. Combine with `<source>` (usually with `--force`). |
| `--recheck` | *(no id)* Refresh **every** note by re-importing from its original source (Moxfield/EDHREC URL, or the `.txt` in `./imports`): deck edits, fresh prices, fresh art (commander + galleries), 💸 Cheapest Build and 🛒 Cards to Buy. Falls back to the note's stored list if a source can't be reached. Moxfield decks each open a browser. |
| `--recheck <id>` | Same **full re-import**, scoped to one deck by id. |
| `--reimport` | *(no id)* Refresh **every** note's deck list and card art from source **without** re-pricing. Rebuilds the 🖼️ card gallery and commander art; leaves price / buy / Cheapest Build sections untouched. Falls back to the note's stored list if a source fetch fails. |
| `--reimport <id>` | Same as above, for a single deck by id. |
| `--list` | Print every deck's id and name, then exit. Use it to find the id for `--recheck <id>` / `--reimport <id>`. |
| `--help` | Show the built-in help and exit. |

Deck ids come from the `deck-id:` field in each note's frontmatter — run
`--list` to see them.

### 📋 Examples

```
# Import a deck (browser opens briefly for Moxfield)
python mtg_deck_importer.py https://moxfield.com/decks/Na_36cWsnEOhEGT_o27XgQ

# Import an EDHREC deck preview (no browser)
python mtg_deck_importer.py https://edhrec.com/deckpreview/abc123

# Import a local decklist (first line is the commander)
python mtg_deck_importer.py "My Krenko Deck.txt"

# You edited the deck on the site — pull the changes into its note
python mtg_deck_importer.py --force https://moxfield.com/decks/Na_36cWsnEOhEGT_o27XgQ

# You bought a wishlist precon — mark it owned and refresh the note
python mtg_deck_importer.py --own --force "Cloud Limit Break Precon.txt"

# See every deck's id and name
python mtg_deck_importer.py --list

# Re-import every deck from source — deck edits, fresh prices, fresh art
python mtg_deck_importer.py --recheck

# Fully re-import just deck 7 (fresh list + prices + art)
python mtg_deck_importer.py --recheck 7

# Backfill galleries / refresh art everywhere, no re-pricing
python mtg_deck_importer.py --reimport

# Refresh art for just deck 7, no re-pricing
python mtg_deck_importer.py --reimport 7
```

## 🌐 Supported sources

| Source | How it's fetched |
|--------|------------------|
| 🟣 [Moxfield](https://moxfield.com) — `…/decks/<id>` | Headed Chrome (Cloudflare — see below) |
| 🟠 [EDHREC](https://edhrec.com) — `…/deckpreview/<hash>` | Plain HTTP — no browser window 🎉 |
| 📄 Local `.txt` decklist | Read directly — no network for the list itself |

For a `.txt` file: one `1 Card Name` per line (the usual export format),
**first card is the commander**, and the file name becomes the deck name —
`Krenko Goblin Swarm.txt` → deck "Krenko Goblin Swarm". Call the file
whatever you like; the name only sets the note's title and identifies the
deck on re-import. Artwork and prices still come from Scryfall as normal.
After a successful import the file is **moved to `./imports/`** (gitignored)
as your record of what was imported; the note's `deck-url` points there.

🛡️ An existing deck note is **never overwritten without `--force`** — and
even then, **anything you've written in the review sections is preserved**:
the regeneration refreshes the deck list, prices, and shopping list around
your notes. A note counts as the *same deck* when its `deck-url` or
`deck-name` matches, whatever the import date, and `--force` updates it in
place (keeping its original dated filename). So the update-a-deck workflow
is simply: change the deck (on the site, or edit the txt in `imports/`),
re-run with `--force`, done.

👥 **Multiple builds per commander** are fine: the first deck keeps the plain
`YYYY-MM-DD_MTG_<Commander>.md` name, and a different build of the same
commander (say, the precon plus your enhanced version) gets a
`" - <deck name>"` suffix instead of colliding — so name your `.txt` files
meaningfully (`Cloud Limit Break Precon.txt` → that becomes the deck name).

## 🗃️ Your collection & the shopping list

Keep a `_Collection.md` in the output folder (or point `COLLECTION_FILE` in
`.env` somewhere else) listing the cards you own — one `N Card Name` per
line, same format as a deck list; headings and prose are ignored so it can be
a normal Obsidian note. When it exists, every imported deck note gains a
**🛒 Cards to Buy** section: how many of the deck's cards you already own,
the cards you're missing (quantity-aware — owning 10 Mountains against a
23-Mountain deck means you need 13 more), per-card prices, and the total
cost to complete the deck in EUR / USD / ≈ GBP. The owned count and buy
totals also land in the frontmatter (`owned`, `buy-eur`, `buy-gbp`) for
Dataview. No collection file? The section is simply skipped.

🏷️ **Wishlist precons**: import a precon you're *thinking* of buying as
normal — its Cards to Buy total is the deck's value in singles, which tells
you whether the sealed product or the singles are the better deal. When you
do buy it, re-run with **`--own`**: the whole deck list is appended to
`_Collection.md` under the deck's name (skipped if already there), and the
note refreshes to fully owned. One flag instead of hand-copying ~100 cards.

🎭 **Flavor names are understood**: Universes Beyond precons print some cards
under skinned names (the Marvel decks call Spark Double "Loki's Double") while
deck sites report the canonical Magic name. Before declaring a card missing,
the app checks all its aliases via Scryfall — so your collection can use
whichever name is on the physical card. Moxfield export decorations (`*F*`
foil markers, duplicate rows for different printings) are also handled.

💡 **Cheaper Printings**: under Cards to Buy, a second table lists any missing
card with a cheaper *functionally identical* version — another printing, or
the plain-MTG/UB-skinned counterpart in either direction — with the set, the
price gap, and a "buy the cheapest versions" total. Comes free with the alias
lookup: no extra API calls.

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
  - 🖼️ **Card Gallery** — a 4-column grid of every card's image (Scryfall
    "small" art) right under the price table, so you can eyeball the whole
    deck at a glance instead of scrolling a wall of names. Images are hosted
    links, so they render in any markdown viewer and cost nothing to store
  - 📜 **Deck List** — commander first, then mainboard alphabetically, one
    `1 Card Name` per line (pastes straight back into Moxfield / Arena)
  - 💸 **Cheapest Build** — the whole deck again with every card at its
    cheapest functionally-identical version (other printings, UB/plain-name
    swaps, ManaPool's cheapest listing) with a best-mix total, a
    **copy-paste deck list** of those cheapest versions with `(SET)` codes
    pinning the exact printings (Moxfield's import format), and its own
    🖼️ gallery showing each chosen cheapest version. Cards under €0.50 keep
    the deck's own version. Printing lookups are cached for 3 days in
    `.cache/`, so only the first run after a quiet spell is slow.
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
| 🇪🇺 [Cardmarket](https://www.cardmarket.com/en/Magic) | EUR | European paper singles (market average) |
| 🇺🇸 [TCGPlayer](https://www.tcgplayer.com) | USD | US paper singles (market average) |
| 🛍️ [ManaPool](https://manapool.com) | USD | US marketplace — **live cheapest listings** via their [open API](https://manapool.com/api/docs/v1) |
| 🖥️ [Cardhoarder](https://www.cardhoarder.com) | tix | Magic Online (1 tix ≈ $1) |

ManaPool differs from the others: it's what you could *actually buy each card
for right now* (cheapest listing across printings at your minimum condition —
`MANAPOOL_CONDITION` in `.env`: `any`/`lp` (default)/`nm`), comparable to
their cart optimizer's lowest-price subtotal. Shipping/packages aren't
included, and it's a US marketplace. Their full price catalog (~50 MB) is
downloaded once and cached next to the script; runs re-use the local file and
only re-download when it's over 24 hours old. Every price table gets an
`MP $` column and the Deck Value table a ManaPool row.

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

Optional but tidy — create and activate a venv first (create once, activate
each new terminal session):

```
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Windows (cmd)
.venv\Scripts\activate.bat

# macOS / Linux
source .venv/bin/activate
```

You're in when the prompt shows `(.venv)` — `deactivate` drops you back out.
Then install the dependencies (with or without a venv):

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
