# 🃏 mtg-decklist-md

**A Commander deck library that lives in your Obsidian vault — and tells you what to buy.**

Point it at a deck (Moxfield, EDHREC, or a plain `.txt` list) and it writes a markdown note
you own outright: commander art, the full list, a card gallery, and Cardmarket prices in £.
Then it does the part the deck sites don't — it reads **your** collection, works out exactly
what you're still missing, re-prices each card at its cheapest printing, and hands you a
copy-paste shopping list.

```bash
python mtg_deck_importer.py https://moxfield.com/decks/<public_id>
```

```
Deck:      Doom Prevails (id 4)
Commander: Doctor Doom, King of Latveria
Colours:   🔵⚫🔴
Cards:     100 (88 unique)
Value:     ~EUR 141.02 / GBP 120.66 / USD 150.11 / TIX 62.30
Owned:     88/88 cards — to buy: 0 (~EUR 0.00 / ~EUR 0.00 at cheapest versions)
Note:      ...\Doctor Doom, King of Latveria 🔵⚫🔴.md
Artwork:   ...\Attachments\Doctor Doom, King of Latveria 🔵⚫🔴.jpg
```

Run it again next month and it re-prices the lot, charts the trend, and shouts when a card
you need has been reprinted and crashed in price. Everything is plain markdown and hosted
image links in your own vault — no account, no lock-in.

## 📑 Index

| Section | Description |
|---|---|
| [🚀 Quick start](#-quick-start) | Requirements, install, `.env` settings, and the first run |
| [🔁 The loop](#-the-loop) | How you actually live with it month to month |
| [🌐 Deck sources](#-deck-sources) | Moxfield, EDHREC, Archidekt and `.txt` — and how chosen printings are honoured |
| [🗃️ Your collection](#️-your-collection) | The one-card-per-line format, `--collection` vs `--merge-collection` |
| [🎯 Collecting a whole set](#-collecting-a-whole-set) | `--set` checklists, tick preservation, and cheapest-printing pricing |
| [💰 Prices](#-prices) | Cardmarket as house currency, ≈ GBP conversion, and the Cheapest Build |
| [📦 What lands in your vault](#-what-lands-in-your-vault) | Every file the importer writes, and what's inside a deck note |
| [The reference DB](#the-reference-db) | The local Scryfall SQLite cache and its refresh flags |
| [🧾 Command reference](#-command-reference) | Every flag, grouped by import, refresh, manage, collection, sets and analysis |
| [🤖 Claude Code skills](#-claude-code-skills) | `/analyse-deck` and `/deck-guide`, and what each writes into |
| [❓ FAQ](#-faq) | Why Chrome opens, and what stays off GitHub |
| [🙏 Credits](#-credits) | Data sources and image rights |

## 🚀 Quick start

Needs **Python 3.10+** and **Google Chrome** (only for Moxfield — EDHREC and `.txt` files
need no browser).

```bash
pip install -r requirements.txt
cp .env.example .env        # then edit VAULT_OUTPUT_DIR
python mtg_deck_importer.py "My Krenko Deck.txt"
```

The first run that prices anything downloads Scryfall's daily card export (~80 MB, about ten
seconds) into a local database — see [the reference DB](#the-reference-db). Everything after
that is offline card lookups.

| Setting | Required | What it does |
|---------|:--------:|--------------|
| `VAULT_OUTPUT_DIR` | ✅ | Folder in your Obsidian vault where notes, `Attachments/` and the index are written. |
| `COLLECTION_FILE` | — | Path to your owned-cards list. Defaults to `_Collection.md` inside `VAULT_OUTPUT_DIR`. |
| `COLLECTION_DB` | — | Path to a [CardVault](https://github.com/jflockton/mtg-cardvault) scanner `inventory.db`. When set, that DB is the **source of truth** for what you own — see CardVault mode below. |
| `MANAPOOL_CONDITION` | — | Minimum card condition for ManaPool prices — affects `--collection-value` only. `any`, `lp` (default) or `nm`. |

A real environment variable overrides `.env`, and `.env` is gitignored — your vault path
stays off GitHub.

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

## 🔁 The loop

How you actually live with it, month to month:

1. **Import decks as you find them.** `python mtg_deck_importer.py <url-or-txt>` — each gets
   a note and a stable id.
2. **Tell it what you own.** `--collection <export>` once, then `--merge-collection <export>`
   whenever you buy something — or set `COLLECTION_DB` and just keep scanning cards into
   CardVault. This is what turns a deck note into a shopping decision.
3. **Refresh when you're thinking about money.** `--recheck` re-prices every deck, redoes
   every buy list, and flags cards that have crashed since last time.
4. **Buy from the list.** Each note ends with a copy-paste 📋 Buy List, and a cheaper one
   under 💸 Cheapest Build that pins the exact printings.
5. **Edited a deck on the site?** `--force <its url>` pulls the changes in, keeping
   everything you've written in the note.

`--recheck` with no id is the slow one: each Moxfield deck opens a Chrome window and Scryfall
rate-limits bursts, so a large library takes a while. Scoping it to `--recheck <id>` is the
quick version.

## 🌐 Deck sources

| Source | How it's fetched |
|--------|------------------|
| 🟣 [Moxfield](https://moxfield.com) — `…/decks/<id>` | Headed Chrome ([why?](#-faq)) |
| 🟠 [EDHREC](https://edhrec.com) — `…/deckpreview/<hash>` | Plain HTTP — no browser |
| 🟢 [Archidekt](https://archidekt.com) — `…/decks/<id>` | Plain HTTP — no browser |
| 📄 Local `.txt` decklist | Read directly — no network for the list itself |

A `.txt` file is one `1 Card Name` per line (the usual export format), **first card is the
commander**, and the filename becomes the deck name. After a successful import it's moved
into `imports/` inside your vault, so any machine with the vault can re-import it.

**Chosen printings are honoured.** A line may pin its exact version, Archidekt-style —
`1x Blade of Selves (c15) 51 *F* [Tokens]` — and the note then prices, pictures and
buy-lists *that* printing (at its foil price when marked `*F*`) instead of Scryfall's
default, which is just the newest reprint. The `[Category]` tag is your own role note:
it's kept in the 📜 Deck List for reference, and never leaks into the copy-paste buy
lists. The 💸 Cheapest Build ignores all of it by design — it still hunts every printing,
so the savings show what your chosen (possibly foil) version really costs over the
cheapest one. An Archidekt **URL** import gets all of this from the site automatically,
including which card is the commander; bare `1 Card Name` lines behave exactly as before.
Archidekt's **Tokens & Extras** come along too, as a 🎟️ section of the note — the physical
token cards the deck creates, pinned to the chosen printings, kept off every deck total.

<details>
<summary>Re-imports, and several decks sharing one commander</summary>

An existing note is **never overwritten without `--force`**, and even then everything you've
written in the review sections is preserved — the rebuild refreshes the data *around* your
notes. A note counts as the *same deck* when its `deck-url` or `deck-name` matches, whatever
the import date — matched by frontmatter, not filename — and `--force` updates it in place keeping its existing filename.

**Multiple builds per commander** are fine. The first is named
`<Commander> <colours>.md`; another build of the same commander gets a
`" - <deck name>"` suffix before the colours instead of colliding — so name your `.txt`
files meaningfully (`Cloud Limit Break Precon.txt` becomes that deck's name).
</details>

## 🗃️ Your collection

The collection file is how the app knows which cards you already own. It lives at
`_Collection.md` in your output folder (or wherever `COLLECTION_FILE` points).

**CardVault mode.** If you scan your cards with the CardVault app, set `COLLECTION_DB` to its
`inventory.db` and stop maintaining the file by hand: ownership, `--collection-value` and the
`--set` checklists all read straight from the DB (every entry pinned to its exact printing,
foils included), and `_Collection.md` becomes a generated mirror of it — rewritten
automatically whenever ownership is read, or on demand with `--sync-collection`, so the
Obsidian note and its wiki-links stay alive. The 💰 value block and anything you write outside
the mirrored section survive the rewrite. In this mode `--collection` and
`--merge-collection` are disabled (scan cards into the app instead), and `--own` writes the
deck **into the DB**: inventory quantities at the deck's pinned printings, plus a deck entry
in CardVault itself. The first sync backs your old file up to `imports/` and flags any card
it listed that the DB doesn't, so nothing silently stops counting as owned. A missing DB
reads as *no collection* — it never falls back silently to a stale file.

**The format is one card per line, `N Card Name` — the same as a deck list.** That's the
whole spec. Only lines starting with a digit are read, so headings, notes and tables can sit
anywhere around the list and are ignored:

```markdown
# 🗃️ My Card Collection

Anything that isn't a card line is ignored, so notes like this are fine.

1 Sol Ring (LTC) 284
3 Lightning Bolt (2X2) 117
1 Lightning Bolt (2X2) 117 ✨
23 Mountain
```

A bare `N Card Name` is always enough. Where a line goes further — `(SET) number` pinning
the printing, `✨` (or `*F*`) marking it foil — the extra detail is kept and used:
`--collection-value` prices that exact version, and a foil is often several times its
non-foil twin. Ownership itself is always judged by name, so a pin never stops a card
counting towards a deck.

Point `--collection` at any card-list export and it writes the file for you, **carrying the
export's printing detail through**: one line per distinct printing (a card you own in three
sets gets three lines, foils separated from non-foils), identical rows merged, sorted by
name then printing. A scanner export whose ids come off the physical cards therefore
rebuilds a fully-priceable collection. Already have one? `--collection` **won't overwrite
it** — it's hand-curated and not reproducible from an export, so use `--merge-collection`,
which appends only genuinely new cards under a dated heading and never deletes anything.
Merged lines keep their printings too; ownership is compared by name, and only the shortfall
is appended.

With a collection in place every deck note gains a cost-to-finish line and two 🛒 **Cards to
Complete** sections — one at the deck's own printings, one at the cheapest ones — each with a
copy-paste buy list. The owned count and buy totals also land in the frontmatter (`owned`,
`buy-eur`, `buy-gbp`, `buy-cheapest-eur`, `buy-cheapest-gbp`) for Dataview.

Without one, nothing fails silently: you get a ⚠️ line naming the file it expected and the
exact command to fix it, and the note is written without the 🛒 sections. Prices, galleries,
deck lists and the Cheapest Build never depend on the collection.

<details>
<summary>Quantity awareness, flavour names, and wishlist precons</summary>

Missing cards are **quantity-aware**: owning 10 Mountains against a 23-Mountain deck shows
`🛒 13 (have 10)`. Cards you can pull from your collection appear as ✅ rows, kept off the
totals.

**Flavour names are understood.** Universes Beyond precons print some cards under skinned
names (the Marvel decks call Spark Double "Loki's Double") while deck sites report the
canonical name. Before declaring a card missing, every alias is checked via Scryfall — so
your collection can use whichever name is on the physical card.

**Wishlist precons**: import one you're *considering* — its cost-to-finish is the deck's value
in singles, which tells you whether the sealed product or the singles are the better deal.
When you buy it, re-run with `--own` to mark the whole list owned in one flag rather than
copying ~100 card names.

The alias lookup already enumerates every printing of a card, so the cheapest-version
comparisons cost no extra API calls.
</details>

## 🎯 Collecting a whole set

Decks aren't the only thing worth tracking. `--set` builds a long-term collection checklist
for one or more sets — the "own every card in this set eventually" project. Keep several on
the go; a bare `--set` refreshes them all.

```bash
python mtg_deck_importer.py --set ff          # or: marvel, spiderman, or raw codes: fin,fic
```

```markdown
`████░░░░░░░░░░░░░░░░` **175/811** (22%) · £1,144.78 still to buy of £1,242.40

### Rare — 78/320 · £352.11 to go

- [x] ⭐ Aerith, Last Ancient — £0.29
- [ ] ⭐ Cloud, Midgar Mercenary — £3.02
```

One tickable line per card, grouped by **Mythic · Rare · Uncommon · Common**, with **Through
the Ages · Art Series · Tokens** in their own blocks. ⭐ marks legendaries.

**A ticked box means you have it**, from either of two places: your collection file already
listing the card, or a tick you made by hand. So you don't start from zero — the run above
began at 175/811 purely from `_Collection.md`. **Re-running preserves every tick** while
re-pricing the list, so it survives years of use. A tick is never removed by a refresh, so a
card you own but haven't added to your collection file stays ticked.

Every run ends with an **Added** line — how many boxes this refresh ticked that weren't ticked
before, so after a `--merge-collection` you can see exactly what the new cards bought you:

```text
Added:     +12 since the last refresh (175 → 187 of 811)
```

A bare `--set` totals it across every checklist, and the count also lands in each note as
`printings-added:` in the frontmatter and an **Added:** line in the body.

It's one line per **card**, at its cheapest printing across the sets you listed — not one per
printing. Chasing every borderless/showcase/foil variant of a premium set runs into five
figures; this keeps it a collection rather than a mortgage. Digital-only cards (Arena's
rebalanced `A-` versions) are excluded since they can't be owned in paper.

Set notes carry no `deck-id`, so they never appear in `--list` or `_Decks.md` and can't be hit
by `--delete` or `--reindex`.

## 💰 Prices

Deck notes are priced against **Cardmarket**, from Scryfall's daily snapshot, always for the
standard non-foil card:

| Source | Currency | Role in a deck note |
|--------|----------|---------------------|
| 🇪🇺 [Cardmarket](https://www.cardmarket.com/en/Magic) | EUR | **The price that matters** — every ≈ GBP figure and the whole Cheapest Build |
| 🇺🇸 [TCGPlayer](https://www.tcgplayer.com) | USD | Reference — US market comparison |
| 🖥️ [Cardhoarder](https://www.cardhoarder.com) | tix | Reference — Magic Online (1 tix ≈ $1) |

**≈ GBP** is Cardmarket EUR converted at ECB reference rates via
[frankfurter.dev](https://frankfurter.dev). Treat the totals as fair estimates, not
valuations. If the rate API is unreachable the last good rate is reused, and failing that the
GBP cells show a dash.

The 💸 **Cheapest Build** picks each card's cheapest Cardmarket printing — which is also the
one it pins with `(SET) 123` — so the version you're told to buy and the price you're quoted
always describe the same card. **Every printing of every card is considered**, however cheap
the card, and any card Cardmarket has no price for is reported as a count rather than quietly
left out of the total.

**Every line of both buy lists carries its `(SET) 123` id** — including cards whose own
printing was already the cheapest, since those need it most. Without an id you're told
"Smoke, €3.05" and then handed a search listing Alpha at €499 and Beta at €90. The id is the
only thing that gets you to the card being quoted.

Cheapest means cheapest, collector printings included. If an Intl. Collectors' Edition *Smoke*
is the cheapest way to own a Smoke, that's what you're pointed at — whether a given printing
suits your playgroup is your call, not the script's.

The one thing skipped is a **price with nothing behind it**: a EUR price and no USD price at
all is one lonely European listing with no second market to corroborate it. That's how a €3.00
*Summer Magic* Birds of Paradise turns up, for a 1994 test print worth thousands. Requiring
both markets is blunt, but it's the difference between a bargain and a card nobody is selling.

<details>
<summary>Where ManaPool fits, and why there's no GBP source</summary>

[ManaPool](https://manapool.com)'s live cheapest US listings are used by
`--collection-value` only, where the point is valuing the exact printings you own against a
real marketplace. Deck notes deliberately don't use it: its prices are per *card name* across
all printings, so it can't describe the specific printing a Cheapest Build pins, and mixing
the two markets made £ totals that didn't match the rows they were summing. Their catalog
(~50 MB) is downloaded once and re-used from `.cache/` for 24 hours — and a deck run never
touches it.

There's no reliable GBP price index (and no, not eBay — auction listings aren't an index 😄),
hence the conversion. Most cards are priced in one bulk lookup; when Scryfall's default
printing is an online-only set with no paper price (e.g. Tempest Remastered Mox Diamond), the
app falls back to the cheapest paper printing. Each row of the value table shows how many of
the deck's cards that source actually priced.
</details>

## 📦 What lands in your vault

| Path | What it is |
|------|------------|
| `<Commander> <colours>.md` | The deck note — one per deck |
| `Attachments/<same stem>.jpg` | Commander art (offline backup) |
| `_Decks.md` | Auto-generated master index of every deck — never edit by hand |
| `imports/` | Archived `.txt` decklists, so any machine can re-import |
| `_analysis-briefs/` | `--brief` output |
| `_Collection - <name>.md` | `--set` collection checklists (sorted to the top) |
| `.price-history.json` | Dated price snapshots behind the 📉 Price History tables |

`<colours>` is the commander's **colour identity as coloured circles** — ⚪ white, 🔵 blue,
⚫ black, 🔴 red, 🟢 green (◇ for a colourless commander), always in WUBRG order. The same
suffix ends the `deck-name:` frontmatter and the note's title, so a deck's colours are
visible at a glance in the file list, the graph and `_Decks.md` alike. New imports get it
automatically; `--colorize` stamps it onto decks imported before the feature existed.

Nothing here is the card database — that lives in the repo's `.cache/`, not your vault, so it
never syncs to Dropbox.

### The reference DB

Card lookups don't go to the network. The repo's `.cache/` holds a SQLite copy of **every
paper printing** (~107,000 of them, ~60 MB), rebuilt from
[Scryfall's daily bulk export](https://scryfall.com/docs/api/bulk-data) whenever it's over a
day old — an ~80 MB download that takes about ten seconds.

That's what makes finding a card's cheapest printing an indexed query instead of a
rate-limited search per card. Before it, a full `--recheck` spent minutes being throttled and
gave up after Scryfall's first page of 175 printings; basics have nearly 800.

| Command | What it does |
|---------|--------------|
| *(nothing)* | The DB refreshes itself once a day, on any command that prices something |
| `--refresh-db` | Rebuild now, even if it's fresh. On its own, just rebuilds and exits |
| `--no-bulk` | Skip the DB and use the API — slower and rate-limited, but no download |

`--reimport`, `--list`, `--index`, `--delete`, `--reindex` and `--brief` never trigger a
refresh, since none of them price anything.

<details>
<summary>Everything inside a deck note, in the order it appears</summary>

0. 🔗 **Header block** — commander, format, source, and a **Project** wiki-link back to the
   vault's MTG Deck Importer dashboard (also in the frontmatter as `project:`), so generated
   notes never float unlinked in the Obsidian graph. `_Decks.md` and the collection notes
   carry the same link.
1. 💰 **Deck Value** — totals per market with a ≈ GBP column (also in the frontmatter for
   Dataview) and the 🛒 cost-to-finish line.
2. 📉 **Price History** — a collapsed table of the last 8 price checks (deck value, cost to
   finish, cheapest finish) with the overall trend in the title. The console also flags
   notable drops and per-card price crashes — usually a reprint, and the "time to buy" signal
   for a wishlist deck.
3. 🖼️ **Commander image** — a link to Scryfall's hosted image, so it survives being read
   outside Obsidian; a local copy goes to `Attachments/` as an offline backup.
4. 🗂️ **Contents** — a table of every section in *this* note, each row a `[[#heading]]` jump
   link with a one-line description. Built by reading the finished note's own `##` headings,
   so it can only ever list sections the note actually has (no 🎟️ Tokens row on a deck with
   no tokens, no 🛒 rows when there's no collection to compare against). Hand-written sections
   still holding their `-` placeholder are marked **✍️ *empty***, so the table doubles as a
   checklist of what you've yet to write. Rebuilt on every write — import, `--force`,
   `--recheck` **and** `--reimport` (the one path that splices rather than regenerating).
5. ✍️ **Your review sections** — 🧠 First Impressions · 🧭 Deck Guide. Empty headings,
   **preserved through every rebuild**. The old 💪 Strengths · ⚠️ Weaknesses ·
   🔄 Cards to Consider Swapping · 📝 Play Notes stubs are no longer created — the
   🧭 Deck Guide covers that ground — but an older note that has prose under any of
   them keeps it, in place, through every rebuild.
6. 📊 **Deck Shape** — locally computed, no AI: type counts, mana curve, keyword role
   buckets, and a bracket checklist (Game Changers, extra turns, mass land denial). Followed
   by three more preserved headings — 🎮 Play Pattern · 🏆 Win Conditions ·
   ⚠️ Interactions & Warnings — for you or `/analyse-deck` to fill. **A written
   🧭 Deck Guide takes ownership of those three subjects** (it covers them in its own
   `###` blocks), so `/deck-guide` deletes the empty stubs and a rebuild stops emitting
   them — no duplicated prose, and no headings left inviting you to write it twice. Any
   of the three that already holds prose is kept for good, guide or no guide.
7. 💰 **Card Prices** and 🖼️ **Card Gallery** — every card dearest-first (EUR / USD / ≈ GBP),
   and a 4-column grid of hosted card images. Both in collapsible callouts.
8. 📜 **Deck List** — commander first, then mainboard alphabetically; pastes straight back
   into Moxfield or Arena.
9. 🛒 **Cards to Complete the Deck** — what you're missing, plus a copy-paste 📋 Buy List.
10. 💸 **Cheapest Build** — the whole deck at each card's cheapest Cardmarket printing, with a
   decklist carrying `(SET) 123` pins for the exact printings.
11. 🛒 **Cards to Complete — Cheapest Build** — the same missing cards at those cheapest
    printings, with per-card savings and a 📋 Budget Buy List.
</details>

## 🧾 Command reference

`<source>` is a Moxfield URL, an EDHREC deckpreview URL, or a path to a `.txt` decklist.
Commands taking `[id]` act on every deck when the id is omitted. `--help` documents every
flag too.

| Command | What it does |
|---------|--------------|
| **Import** | |
| `<source>` | Import into a **new** deck note. Refuses to clobber an existing note for the same deck. |
| `--force <source>` | Regenerate an **existing** note in place, keeping everything you've written in it. |
| `--own <source>` | Append the whole deck to your collection as owned, *then* compare. Keeps the deck's `(SET) number` pins, so the precon lands in the collection at the printings it ships. In CardVault mode the cards go into the inventory DB, plus a deck entry in the app. For when you actually buy a wishlist precon. Usually paired with `--force`. |
| **Refresh** | |
| `--recheck [id]` | Full re-import from the original source: deck edits, fresh prices, gallery, and both Cards-to-Complete sections. Art is reused unless the commander changed. Unreachable source → falls back to re-pricing the list stored in the note, so a run never stops. |
| `--reimport [id]` | Deck list and art only, **no new prices**. Leaves all price, buy and Cheapest Build sections untouched. Flags any deck whose list changed so you know to `--recheck` it. |
| **Manage** | |
| `--list` | Print every deck's id and name, then exit — the quickest way to find the id another command wants. |
| `--delete [id]` | Delete a deck and **everything it owns** — note, commander art, archived `.txt`, brief, price-history entry. Shows the exact file list and confirms first; `-y` skips the prompt. Reindexes afterwards. |
| `--reindex` | Renumber every note to a gap-free, unique `1..N` sequence, fixing ids gone missing or duplicated; remaps history and briefs. Runs automatically after `--delete`. |
| `--index` | Rebuild `_Decks.md` from the notes' current frontmatter. No network. (Also runs after every import and recheck.) |
| `--colorize` | One-shot backfill: append each deck's commander colour identity (⚪🔵⚫🔴🟢) to its note filename, `deck-name` and title, rewriting wiki-links to the renamed notes. New imports don't need it — they get the suffix at creation. Idempotent. |
| **Collection** | |
| `--collection <file>` | **Create** the collection file from an export, one line per printing with `(SET) number` and ✨ carried through. Refuses to overwrite a populated collection unless you add `--force`. Disabled in CardVault mode. |
| `--merge-collection <file>` | **Add to** an existing collection — append-only, printings kept. Cards missing from the export are only *reported*, never deleted. Disabled in CardVault mode. |
| `--sync-collection` | CardVault mode only: rewrite `_Collection.md`'s card listing from the inventory DB now. Also happens automatically whenever ownership is read. |
| `--collection-value` | Price your collection and write a 💰 Collection Value block at the top of the file. Uses the exact printing and the foil price wherever a line records them. Basics excluded. Runs automatically after the two above. |
| **Sets** | |
| `--set` | *(no argument)* Refresh **every** checklist you have — re-price, tick anything new, keep your ticks. The everyday command. |
| `--set <what>` | A checklist's name (`--set "Final Fantasy"`), a preset (`ff`, `marvel`, `spiderman`), or raw Scryfall set codes (`fin,fic`). |
| `--set-label <name>` | Friendly title for the note (default: the preset's name, or the set codes). |
| `--set-reset` | Discard existing ticks and rederive them from the collection file. |
| **Analysis** | |
| `--brief [id]` | Write a compact analysis brief into `_analysis-briefs/`: deck shape, role groups, the full list, and oracle text for cards recent enough that a model may not know them. Input for `/analyse-deck`. |
| **Reference DB** | |
| `--refresh-db` | Rebuild the local Scryfall card DB now rather than waiting for its daily refresh. On its own, rebuilds and exits. |
| `--no-bulk` | Skip the DB for this run and look printings up through the API instead — slower and rate-limited, but avoids the ~80 MB download. |

> ⚠️ `--delete` **permanently removes files** — there's no recycle bin. If your vault is in
> Dropbox/iCloud/git, they're recoverable from its version history. `--reindex` **changes
> deck ids**, so re-run `--list` afterwards.

A few combinations worth knowing:

```bash
# You bought that wishlist precon — mark it owned and refresh
python mtg_deck_importer.py --own --force "Cloud Limit Break Precon.txt"

# Collection changed — reprice every deck and redo the buy lists
python mtg_deck_importer.py --recheck

# Start the collection file over from a fresh export
python mtg_deck_importer.py --collection "moxfield-export.txt" --force
```

## 🤖 Claude Code skills

The Python script owns all the *facts* (prices, ownership, deck shape). The judgement calls —
strategy, sequencing, what to buy — are handled by [Claude Code](https://claude.com/claude-code)
**skills** in this repo under `.claude/skills/<name>/SKILL.md`.

A skill is just a markdown prompt, loaded **only when you invoke it** by name in a session
opened on this repo — so it costs nothing until used, needs no API key, and runs on your
existing Claude plan. Each reads the notes the importer wrote and writes prose back into a
specific preserved heading, never touching the generated data around it.

| Skill | Writes into | What it does |
|-------|-------------|--------------|
| `/analyse-deck <id>` | 🎮 Play Pattern · 🏆 Win Conditions · ⚠️ Interactions & Warnings | Deliberately token-lean: its only input is the deck's `--brief` file, never the full note. Run `--brief <id>` first. Stops if the deck already has a 🧭 Deck Guide, which owns those three subjects. |
| `/deck-guide <id>` | 🧭 Deck Guide | A full strategy guide — the 100 by role, play pattern, win conditions, non-obvious warnings, bracket justification, budget notes with cheaper alternatives, and an upgrade path. Deletes the three empty analysis stubs it supersedes (never one with prose in it) and fixes up the 🗂️ Contents table. |

Both are coupled to this project on purpose: `analyse-deck` consumes the `--brief` format, and
`deck-guide` writes into headings the importer preserves. A skill that only needs the *notes*
(a shopping workflow, say) doesn't belong here — put it in `~/.claude/skills/` instead, where
it's available in every session rather than only when this repo is open.

## ❓ FAQ

**Why does a Chrome window pop up?** Only for Moxfield, which sits behind Cloudflare: plain
`requests` gets HTTP 403 whatever the headers, and headless browsers get the block page. A
normal headed Chrome passes, so the app opens a real window for a few seconds and reads the
deck JSON from inside that page. No fingerprint spoofing or stealth tricks — if Moxfield ever
blocks this too, the fallback is their Export button (same text format). Scryfall's
[open API](https://scryfall.com/docs/api) needs no browser.

**Is my card data private?** Your vault path lives in `.env`, and `.env`, `*.txt`, `export.md`
and `_Collection.md` are all gitignored — decklists and collection data stay off GitHub.

## 🙏 Credits

- Deck data: [Moxfield](https://moxfield.com) & [EDHREC](https://edhrec.com)
- Card data & images: [Scryfall](https://scryfall.com)
- Collection valuation: [ManaPool](https://manapool.com)
- Exchange rates: [frankfurter.dev](https://frankfurter.dev)
- Card images © Wizards of the Coast — personal use only
