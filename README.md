# 🃏 mtg-decklist-md

**Deck link in → Obsidian deck note out.** ✨

Give it a Moxfield/EDHREC URL or a `.txt` decklist and it writes a ready-to-review
markdown note into your vault: commander art, the full deck list, per-card prices in
€/$/£, a card gallery, and — if you keep a collection file — exactly which cards you're
missing and what they'd cost.

```bash
python mtg_deck_importer.py https://moxfield.com/decks/<public_id>
```

```
Deck:      Doom Prevails (id 4)
Commander: Doctor Doom, King of Latveria
Cards:     100 (88 unique)
Value:     ~EUR 141.02 / GBP 120.66 / USD 150.11 / TIX 62.30
Owned:     88/88 cards — to buy: 0 (~EUR 0.00 / ~EUR 0.00 at cheapest versions)
Note:      ...\2026-07-19_MTG_Doctor Doom, King of Latveria.md
Artwork:   ...\Attachments\2026-07-19_MTG_Doctor Doom, King of Latveria.jpg
```

**Contents** — [Setup](#-setup) · [Commands](#-commands) · [Examples](#-examples) ·
[Deck sources](#-deck-sources) · [Your collection](#-your-collection) ·
[What lands in your vault](#-what-lands-in-your-vault) · [Prices](#-prices) ·
[Claude Code skills](#-claude-code-skills) · [FAQ](#-faq)

## 🚀 Setup

Needs 🐍 **Python 3.10+** and 🌐 **Google Chrome** (only for Moxfield — EDHREC and
`.txt` files need no browser).

```bash
pip install -r requirements.txt
```

<details>
<summary>Optional: use a virtual environment</summary>

```bash
python -m venv .venv

.venv\Scripts\Activate.ps1     # Windows PowerShell
.venv\Scripts\activate.bat     # Windows cmd
source .venv/bin/activate      # macOS / Linux
```

You're in when the prompt shows `(.venv)`; `deactivate` drops you back out. Then run the
`pip install` above.
</details>

### ⚙️ Configuration

Copy `.env.example` to `.env` and point it at the folder in **your** vault where deck
notes should land. Only the first setting is required:

| Setting | Required | What it does |
|---------|:--------:|--------------|
| `VAULT_OUTPUT_DIR` | ✅ | Folder in your Obsidian vault where notes, `Attachments/` and the index are written. |
| `COLLECTION_FILE` | — | Path to your owned-cards list. Defaults to `_Collection.md` inside `VAULT_OUTPUT_DIR`. |
| `MANAPOOL_CONDITION` | — | Minimum card condition for ManaPool prices: `any`, `lp` (default), or `nm`. |

📌 A real environment variable overrides the `.env` file, and `.env` is gitignored — your
vault path stays off GitHub.

## 🧾 Commands

```bash
python mtg_deck_importer.py [--force] [--own] <source>   # import
python mtg_deck_importer.py --recheck [id]               # refresh everything
python mtg_deck_importer.py --reimport [id]              # refresh lists + art only
python mtg_deck_importer.py --list                       # show deck ids
python mtg_deck_importer.py --delete [id] [-y]           # remove a deck
python mtg_deck_importer.py --reindex                    # renumber deck ids
python mtg_deck_importer.py --index                      # rebuild _Decks.md
python mtg_deck_importer.py --collection-value           # price your collection
python mtg_deck_importer.py --merge-collection <file>    # merge an owned-cards export
python mtg_deck_importer.py --brief [id]                 # write analysis briefs
python mtg_deck_importer.py --help
```

`<source>` is a Moxfield URL, an EDHREC deckpreview URL, or a path to a `.txt` decklist.
Every note carries a stable **deck id** in its frontmatter (`deck-id:`) so a single deck can
be targeted by number — run `--list` to see them. Any note still missing an id gets one
assigned the next time a command reads the notes.

### 📥 Importing

| Command | What it does |
|---------|--------------|
| `<source>` | Import into a **new** deck note — prices, buy list, gallery, art. Refuses to clobber an existing note for the same deck. |
| `--force <source>` | Regenerate an **existing** note in place: fresh list, prices, gallery and art, **keeping everything you've written** in the review sections. |
| `--own <source>` | Append the whole deck to your collection file as owned (skipped if already listed), *then* compare. For when you actually buy a wishlist precon — one flag instead of copying ~100 card names. Usually paired with `--force`. |

### 🔄 Refreshing existing notes

| Command | What it does |
|---------|--------------|
| `--recheck` | Refresh **every** note by re-importing from its original source: deck edits, fresh prices, gallery, Cheapest Build and both Cards-to-Complete sections. Commander art is reused unless the commander changed. If a source is unreachable, that deck falls back to re-pricing the list stored in its note, so the run never stops. |
| `--recheck <id>` | The same full re-import, scoped to one deck. |
| `--reimport` | Refresh every note's **deck list and art only — no new prices**. Rebuilds the gallery and commander art; leaves all price, buy and Cheapest Build sections untouched. Falls back to the note's stored list if a fetch fails, so it works offline. Flags any deck whose list changed so you know to `--recheck` it. |
| `--reimport <id>` | The same, for one deck. |

> ⏱️ `--recheck` with no id is the slow one: each Moxfield deck opens a Chrome window, and
> Scryfall rate-limits bursts, so a large library takes a while.

### 🗂️ Managing decks

| Command | What it does |
|---------|--------------|
| `--list` | Print every deck's id and name, then exit. |
| `--delete` | List the decks, ask which id to remove, then delete that note **and everything it owns** — commander art, the archived `.txt` it was imported from, its analysis brief and its price-history entry. Reindexes afterwards. |
| `--delete <id>` | Same, targeting a known deck directly. Still asks for confirmation and shows the exact file list first. |
| `-y`, `--yes` | Skip the `--delete` confirmation prompt. |
| `--reindex` | Renumber every note to a gap-free, unique `1..N` `deck-id` sequence, fixing ids that have gone **missing or duplicated**; remaps price history and briefs to the new numbers and rebuilds the index. Runs automatically after `--delete` — call it by hand if you delete a note yourself. |
| `--index` | Rebuild the `_Decks.md` master index from the notes' current frontmatter. No network. (Also runs automatically after every import and recheck.) |

> ⚠️ `--delete` **permanently removes files** — there's no recycle bin. If your vault is in
> Dropbox/iCloud/git, they're recoverable from its version history.
> Note that `--reindex` **changes deck ids**, so re-run `--list` afterwards.

### 🗃️ Collection

| Command | What it does |
|---------|--------------|
| `--collection-value` | Price everything in your collection file (basic lands excluded) and write a **💰 Collection Value** section into it: totals per market plus a top-20 table, replaced in place on re-runs. |
| `--merge-collection <file>` | Diff a full owned-cards export against your collection file and append what's missing, under a dated heading. **Append-only** — nothing is ever deleted; cards in your collection but absent from the export are only *reported* for you to prune by hand. |

### 🧠 Analysis

| Command | What it does |
|---------|--------------|
| `--brief` | Write a compact **analysis brief** for every deck into the vault's `_analysis-briefs/`: deck shape, role groups, the full list, and oracle text for cards recent enough that a model may not know them. Input for the `/analyse-deck` skill. |
| `--brief <id>` | The same, for one deck. |
| `--help` | Show the built-in help and exit. |

## 📋 Examples

```bash
# Import from each supported source
python mtg_deck_importer.py https://moxfield.com/decks/Na_36cWsnEOhEGT_o27XgQ
python mtg_deck_importer.py https://edhrec.com/deckpreview/abc123
python mtg_deck_importer.py "My Krenko Deck.txt"

# You edited the deck on the site — pull the changes in
python mtg_deck_importer.py --force https://moxfield.com/decks/Na_36cWsnEOhEGT_o27XgQ

# You bought that wishlist precon — mark it owned and refresh
python mtg_deck_importer.py --own --force "Cloud Limit Break Precon.txt"

# Collection changed — reprice every deck and redo the buy lists
python mtg_deck_importer.py --recheck

# Just deck 7: full re-import / art-only refresh
python mtg_deck_importer.py --recheck 7
python mtg_deck_importer.py --reimport 7

# Housekeeping
python mtg_deck_importer.py --list
python mtg_deck_importer.py --delete 7 -y
python mtg_deck_importer.py --reindex

# Collection jobs
python mtg_deck_importer.py --collection-value
python mtg_deck_importer.py --merge-collection "moxfield-export.txt"
```

## 🌐 Deck sources

| Source | How it's fetched |
|--------|------------------|
| 🟣 [Moxfield](https://moxfield.com) — `…/decks/<id>` | Headed Chrome ([why?](#-faq)) |
| 🟠 [EDHREC](https://edhrec.com) — `…/deckpreview/<hash>` | Plain HTTP — no browser 🎉 |
| 📄 Local `.txt` decklist | Read directly — no network for the list itself |

A `.txt` file is one `1 Card Name` per line (the usual export format), **first card is the
commander**, and the filename becomes the deck name. After a successful import it's
**moved into `imports/` inside your vault**, so any machine with the vault can re-import it.

<details>
<summary>Re-imports, and several decks sharing one commander</summary>

🛡️ An existing note is **never overwritten without `--force`**, and even then everything
you've written in the review sections is preserved — the rebuild refreshes the data
*around* your notes. A note counts as the *same deck* when its `deck-url` or `deck-name`
matches, whatever the import date, and `--force` updates it in place keeping the original
dated filename. So the update loop is: change the deck, re-run with `--force`, done.

👥 **Multiple builds per commander** are fine. The first keeps the plain
`YYYY-MM-DD_MTG_<Commander>.md` name; another build of the same commander gets a
`" - <deck name>"` suffix instead of colliding — so name your `.txt` files meaningfully
(`Cloud Limit Break Precon.txt` becomes that deck's name).
</details>

## 🗃️ Your collection

Keep a `_Collection.md` in the output folder (or point `COLLECTION_FILE` elsewhere) listing
what you own — one `N Card Name` per line, same format as a deck list. Headings and prose
are ignored, so it can be a perfectly normal Obsidian note.

When it exists, every deck note gains a **cost-to-finish** line and two **🛒 Cards to
Complete** sections: one at the deck's own card versions, one at the cheapest versions,
each with a copy-paste buy list. The owned count and buy totals also land in the
frontmatter (`owned`, `buy-eur`, `buy-gbp`, `buy-mp`, `buy-cheapest-eur`,
`buy-cheapest-gbp`) for Dataview.

> No collection file? Imports still work — the Cards to Complete sections are simply
> skipped. `--recheck` and `--collection-value`, though, **stop with an error**, since
> comparing against your collection is the whole point of them.

<details>
<summary>Quantity awareness, flavour names, and wishlist precons</summary>

Missing cards are **quantity-aware**: owning 10 Mountains against a 23-Mountain deck shows
`🛒 13 (have 10)`. Cards you can pull from your collection appear as ✅ rows, kept off the
totals.

🎭 **Flavour names are understood.** Universes Beyond precons print some cards under
skinned names (the Marvel decks call Spark Double "Loki's Double") while deck sites report
the canonical name. Before declaring a card missing, every alias is checked via Scryfall —
so your collection can use whichever name is on the physical card. Moxfield export
decorations (`*F*` foil markers, duplicate rows per printing) are handled too.

🏷️ **Wishlist precons**: import one you're *considering* — its cost-to-finish is the deck's
value in singles, which tells you whether the sealed product or the singles are the better
deal. When you buy it, re-run with `--own`.

💡 The alias lookup already enumerates every printing of a card, so the cheapest-version
comparisons cost no extra API calls.
</details>

## 📦 What lands in your vault

| Path | What it is |
|------|------------|
| `YYYY-MM-DD_MTG_<Commander>.md` | The deck note — one per deck |
| `Attachments/YYYY-MM-DD_MTG_<Commander>.jpg` | Commander art (offline backup) |
| `_Decks.md` | Auto-generated master index of every deck — never edit by hand |
| `imports/` | Archived `.txt` decklists, so any machine can re-import |
| `_analysis-briefs/` | `--brief` output |
| `.price-history.json` | Dated price snapshots behind the 📉 Price History tables |

Lookups are cached in the repo's `.cache/` — Scryfall printings for 3 days, set codes for 7,
ManaPool's catalog for 24 hours — so only the first run after a quiet spell is slow.

<details>
<summary>Everything inside a deck note, in the order it appears</summary>

1. 💰 **Deck Value** — totals per market with a ≈ GBP column (also in the frontmatter for
   Dataview) and the 🛒 cost-to-finish line.
2. 📉 **Price History** — a collapsed table of the last 8 price checks (deck value, cost to
   finish, cheapest finish) with the overall trend in the title. The console also flags
   notable drops in a deck's finish cost and per-card price crashes — usually a reprint,
   and the "time to buy" signal for a wishlist deck.
3. 🖼️ **Commander image** — a link to Scryfall's hosted image, so it survives being read
   outside Obsidian; a local copy goes to `Attachments/` as an offline backup.
4. ✍️ **Your review sections** — 🧠 First Impressions · 💪 Strengths · ⚠️ Weaknesses ·
   🔄 Cards to Consider Swapping · 📝 Play Notes · 🧭 Deck Guide. Empty headings,
   **preserved through every rebuild**.
5. 📊 **Deck Shape** — locally computed, no AI: type counts, mana curve, keyword role
   buckets, and a bracket checklist (Game Changers, extra turns, mass land denial).
   Followed by three more preserved headings — 🎮 Play Pattern · 🏆 Win Conditions ·
   ⚠️ Interactions & Warnings — for you or the `/analyse-deck` skill to fill.
6. 💰 **Card Prices** and 🖼️ **Card Gallery** — every card dearest-first (EUR / USD /
   ManaPool / ≈ GBP), and a 4-column grid of hosted card images. Both in collapsible
   callouts.
7. 📜 **Deck List** — commander first, then mainboard alphabetically; pastes straight back
   into Moxfield or Arena.
8. 🛒 **Cards to Complete the Deck** — what you're missing, plus a copy-paste 📋 Buy List.
9. 💸 **Cheapest Build** — the whole deck at each card's cheapest functionally-identical
   version (other printings, UB/plain-name swaps, ManaPool's cheapest listing) with a
   best-mix total and a decklist carrying `(SET) 123` pins for the exact printings. Cards
   at or below ~€0.50 keep the deck's own version — there's nothing to save on pennies.
10. 🛒 **Cards to Complete — Cheapest Build** — the same missing cards at those cheapest
    versions, with per-card savings and a 📋 Budget Buy List.
</details>

## 💰 Prices

Scryfall's daily snapshot, always for the **standard non-foil** card:

| Source | Currency | Market |
|--------|----------|--------|
| 🇪🇺 [Cardmarket](https://www.cardmarket.com/en/Magic) | EUR | European singles (market average) |
| 🇺🇸 [TCGPlayer](https://www.tcgplayer.com) | USD | US singles (market average) |
| 🛍️ [ManaPool](https://manapool.com) | USD | US marketplace — **live cheapest listings** |
| 🖥️ [Cardhoarder](https://www.cardhoarder.com) | tix | Magic Online (1 tix ≈ $1) |

Every row also gets a **≈ GBP** column, converted at ECB reference rates via
[frankfurter.dev](https://frankfurter.dev). Treat the totals as fair estimates, not
valuations.

<details>
<summary>Why ManaPool is different, and why there's no GBP source</summary>

ManaPool is what you could *actually buy each card for right now* — the cheapest listing
across printings at your minimum condition (`MANAPOOL_CONDITION`), comparable to their cart
optimizer's lowest-price subtotal. Shipping isn't included and it's a US marketplace. Their
full catalog (~50 MB) is downloaded once and re-used from `.cache/` until it's over 24 hours
old.

There's no reliable GBP price index (and no, not eBay — auction listings aren't an index 😄),
hence the conversion. If the rate API is unreachable, the GBP cells show a dash. Most cards
are priced in one bulk lookup; when Scryfall's default printing is an online-only set with
no paper price (e.g. Tempest Remastered Mox Diamond), the app falls back to the cheapest
paper printing. Each row shows how many of the deck's cards that source actually priced.
</details>

## 🤖 Claude Code skills

The repo ships three optional [Claude Code](https://claude.com/claude-code) skills that read
these notes. They need no API key — they run on your existing Claude plan.

| Skill | What it does |
|-------|--------------|
| `/analyse-deck <id>` | Fills 🎮 Play Pattern · 🏆 Win Conditions · ⚠️ Interactions & Warnings from the deck's `--brief` file. Token-lean: the brief is its only input. |
| `/deck-guide <id>` | Writes a full strategy guide into the note's 🧭 Deck Guide section — the 100 by role, play pattern, warnings, bracket justification, budget notes and an upgrade path. |
| `/buy-deck <id>` | Finds the genuinely cheapest way to buy a deck's missing cards across UK shops, solving for **total cost including postage** rather than per-card price. |

## ❓ FAQ

**Why does a Chrome window pop up?** Only for Moxfield, which sits behind Cloudflare: plain
`requests` gets HTTP 403 whatever the headers, and headless browsers get the block page. A
normal headed Chrome passes, so the app opens a real window for a few seconds and reads the
deck JSON from inside that page. No fingerprint spoofing or stealth tricks — if Moxfield
ever blocks this too, the fallback is their Export button (same text format). Scryfall's
[open API](https://scryfall.com/docs/api) needs no browser.

**Is my card data private?** Your vault path lives in `.env`, and `.env`, `*.txt`,
`export.md` and `_Collection.md` are all gitignored — decklists and collection data stay
off GitHub.

## 🙏 Credits

- 🃏 Deck data: [Moxfield](https://moxfield.com) & [EDHREC](https://edhrec.com)
- 🎨 Card data & images: [Scryfall](https://scryfall.com)
- 💱 Exchange rates: [frankfurter.dev](https://frankfurter.dev)
- Card images © Wizards of the Coast — personal use only
