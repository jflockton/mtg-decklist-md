# 🃏 mtg-decklist-md

**A Commander deck library that lives in your Obsidian vault — and tells you what to buy.** ✨

Point it at a deck (Moxfield, EDHREC, or a plain `.txt` list) and it writes a rich markdown
note you own outright: commander art, the full list, a browsable card gallery, and prices
from four markets converted to £.

Then it does the part the deck sites don't. It reads **your** collection, works out exactly
which cards you're still missing, re-prices each one at its **cheapest printing**, and hands
you a copy-paste shopping list. Run it again next month and it re-prices the lot, charts the
trend, and flags when a card you need has been reprinted and crashed in price.

### What it actually does for you

- 🗂️ **Manages a whole fleet, not one deck.** Every deck gets a stable id and a row in an
  auto-generated index with its value, how much of it you own, and what finishing it costs —
  so you can see all your decks and the total damage at a glance.
- 🛒 **Answers "what do I still need?"** Quantity-aware against your collection (10 Mountains
  towards a 23-Mountain deck is 13 short, not "missing"), and it understands Universes Beyond
  flavour names, so *Loki's Double* counts as the *Spark Double* you own.
- 💸 **Answers "what's the cheapest way to get it?"** A full **Cheapest Build** of every deck
  at each card's cheapest functionally-identical printing, with `(SET) 123` pins that store
  decklist finders actually parse.
- 📈 **Watches prices over time.** Every refresh snapshots deck value and cost-to-finish, and
  the console shouts when a card drops hard — the reprint radar for a deck you're saving for.
- 📊 **Tells you about the deck itself.** Locally computed type counts, mana curve, role
  buckets and a Commander **bracket checklist** (Game Changers, extra turns, mass land denial)
  — no AI, no API key, just counting.
- 🔒 **Leaves you owning everything.** Plain markdown and hosted image links in your own vault.
  No account, no lock-in; it reads and writes files and stops there.

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
[Collecting a set](#-collecting-a-set) ·
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
python mtg_deck_importer.py --set <codes> [--set-label N] # set-collection checklist
python mtg_deck_importer.py --collection <file>          # create the collection file
python mtg_deck_importer.py --merge-collection <file>    # merge into an existing one
python mtg_deck_importer.py --collection-value           # price your collection
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
| `--list` | Print every deck's id and name, then exit — the quickest way to find the id another command wants. |
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
| `--collection <file>` | **Create** the collection file from a card-list export — see [Getting your cards in](#getting-your-cards-in). Merges duplicate rows, strips set codes and foil markers, writes a plain alphabetical list. Refuses to overwrite a collection that already has cards unless you add `--force`. |
| `--merge-collection <file>` | **Add to** an existing collection: diff an export against it and append what's missing under a dated heading. **Append-only** — nothing is ever deleted; cards in your collection but absent from the export are only *reported* for you to prune by hand. |
| `--collection-value` | Price everything in your collection (basic and snow-covered lands excluded) and write a **💰 Collection Value** section into it: totals per market plus a top-20 table, replaced in place on re-runs. |

### 🎯 Collecting a whole set

| Command | What it does |
|---------|--------------|
| `--set <codes>` | Build or refresh a **set-collection checklist** — one tickable line per card across one or more Scryfall set codes (`--set fin,fic`), grouped by rarity with tokens, art series and masterpieces in their own blocks. See [Collecting a set](#-collecting-a-set). |
| `--set-label <name>` | Friendly title for that note (default: the set codes). |

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

# Collection: create it, then keep it topped up
python mtg_deck_importer.py --collection "moxfield-export.txt"
python mtg_deck_importer.py --merge-collection "latest-order.txt"
python mtg_deck_importer.py --collection-value
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

The collection file is what turns a deck note into a **shopping decision** — it's how the
app knows which cards you already own. It lives at `_Collection.md` in your output folder
(or wherever `COLLECTION_FILE` points).

**The format is one card per line, `N Card Name` — the same as a deck list.** That's the
whole spec. Only lines starting with a digit are read, so you can add headings, notes and
tables anywhere around the list and they're ignored:

```markdown
# 🗃️ My Card Collection

Anything that isn't a card line is ignored, so notes like this are fine.

1 Sol Ring
3 Lightning Bolt
23 Mountain
```

### Getting your cards in

Point `--collection` at any card-list export and it writes the file for you:

```bash
python mtg_deck_importer.py --collection "moxfield-export.txt"
```

It **merges duplicate rows** (exports split one card across printings — those are all still
copies you own), strips set codes and `*F*` foil markers, sorts alphabetically, and writes a
plain list with a two-line header. Nothing else — it's yours to annotate afterwards.

Already have a collection file? `--collection` **won't overwrite it** (it's hand-curated and
not reproducible from an export) — it tells you to use `--merge-collection` instead, which
appends only the genuinely new cards under a dated heading. Use `--collection --force` only
if you really do want to start over.

| You want to… | Command |
|--------------|---------|
| Create the file from an export | `--collection <file>` |
| Add a new order/export to it | `--merge-collection <file>` |
| Add a precon you just bought | `--own --force <that deck's source>` |
| Know what it's all worth | `--collection-value` |

### What happens without one

The app always tells you which of these three states it's in, and what that costs you:

| State | What you'll see | What still works |
|-------|-----------------|------------------|
| ✅ **Ready** | `Collection: _Collection.md — 312 unique cards (415 copies)` | Everything. |
| ⚠️ **Empty** — file exists but has no card lines | A warning naming the file, plus the fix | Imports and refreshes run **without** the 🛒 Cards to Complete sections. `--collection-value` stops. |
| ⚠️ **Missing** — no file at all | A warning naming the expected path, plus the fix | Same as Empty. |

Nothing fails silently: if the ownership comparison is skipped you get a `⚠️` line saying so
and the exact command to fix it. Prices, galleries, deck lists and the Cheapest Build never
depend on the collection.

### What you get when it's there

Every deck note gains a **cost-to-finish** line and two **🛒 Cards to Complete** sections —
one at the deck's own card versions, one at the cheapest versions, each with a copy-paste buy
list. The owned count and buy totals also land in the frontmatter (`owned`, `buy-eur`,
`buy-gbp`, `buy-mp`, `buy-cheapest-eur`, `buy-cheapest-gbp`) for Dataview.

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

## 🎯 Collecting a set

Decks aren't the only thing worth tracking. `--set` builds a **long-term collection
checklist** for one or more sets — the "own every card in this set eventually" project:

```bash
python mtg_deck_importer.py --set fin,fic,fca,pfin,afin,afic,tfin,tfic --set-label "Final Fantasy"
```

```
Set:       Final Fantasy — 811 distinct cards
Progress:  0/811 ticked (0%) — £1,242.40 of £1,242.40 still to buy
```

You get one note with a progress bar, a per-section summary, and a tickable line per card
grouped by **Mythic · Rare · Uncommon · Common**, with **Through the Ages · Art Series ·
Tokens** in their own blocks:

```markdown
`████░░░░░░░░░░░░░░░░` **175/811** (22%) · £1,144.78 still to buy of £1,242.40

### Rare — 78/320 · £352.11 to go

- [x] ⭐ Aerith, Last Ancient — £0.29
- [ ] ⭐ Cloud, Midgar Mercenary — £3.02
```

**A ticked box means you have it**, and it comes from either of two places: your
collection file already listing the card, or a tick you made by hand. So you don't start
from zero — the run above began at 175/811 purely from `_Collection.md`.

Tick a box as each new card arrives. **Re-running `--set` preserves every tick** while
re-pricing the list, so it survives years of use — the whole point. A tick is never
removed by a refresh, so a card you own but haven't added to your collection file stays
ticked. ⭐ marks legendaries.

It's one line per **card**, at its cheapest printing across all the sets you listed — not
one per printing. Chasing every borderless/showcase/foil variant of a premium set runs into
five figures; this keeps it a collection rather than a mortgage. Digital-only cards (Arena's
rebalanced `A-` versions) are excluded since they can't be owned in paper.

Set notes are **not** decks: they carry no `deck-id`, so they never appear in `--list` or the
`_Decks.md` index and can't be hit by `--delete` or `--reindex`.

## 📦 What lands in your vault

| Path | What it is |
|------|------------|
| `YYYY-MM-DD_MTG_<Commander>.md` | The deck note — one per deck |
| `Attachments/YYYY-MM-DD_MTG_<Commander>.jpg` | Commander art (offline backup) |
| `_Decks.md` | Auto-generated master index of every deck — never edit by hand |
| `imports/` | Archived `.txt` decklists, so any machine can re-import |
| `_analysis-briefs/` | `--brief` output |
| `YYYY-MM-DD_MTG-Collection_<name>.md` | `--set` collection checklists |
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

The Python script owns all the *facts* (prices, ownership, deck shape). The judgement calls —
strategy, sequencing, what to buy — are handled by [Claude Code](https://claude.com/claude-code)
**skills** that live in this repo under `.claude/skills/<name>/SKILL.md`.

A skill is just a markdown prompt. Claude Code loads it **only when you invoke it** by name in
a session opened on this repo, so it costs nothing until used, needs no API key, and runs on
your existing Claude plan. Each one reads the notes the importer wrote and writes prose back
into a specific preserved heading — never touching the generated data around it.

| Skill | Writes into | What it does |
|-------|-------------|--------------|
| `/analyse-deck <id>` | 🎮 Play Pattern · 🏆 Win Conditions · ⚠️ Interactions & Warnings | Deliberately token-lean: its only input is the deck's `--brief` file, never the full note. Run `--brief <id>` first. |
| `/deck-guide <id>` | 🧭 Deck Guide | A full strategy guide — the 100 by role, play pattern, non-obvious warnings, bracket justification, budget notes with cheaper alternatives, and an upgrade path. |

Both are coupled to this project on purpose: `analyse-deck` consumes the `--brief` format, and
`deck-guide` writes into headings the importer preserves. A skill that only needs the *notes*
(a shopping workflow, say) doesn't belong here — put it in `~/.claude/skills/` instead, where
it's available in every session rather than only when this repo is open.

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
