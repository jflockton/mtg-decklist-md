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
Cards:     100 (88 unique)
Value:     ~EUR 141.02 / GBP 120.66 / USD 150.11 / TIX 62.30
Owned:     88/88 cards — to buy: 0 (~EUR 0.00 / ~EUR 0.00 at cheapest versions)
Note:      ...\2026-07-19_MTG_Doctor Doom, King of Latveria.md
Artwork:   ...\Attachments\2026-07-19_MTG_Doctor Doom, King of Latveria.jpg
```

Run it again next month and it re-prices the lot, charts the trend, and shouts when a card
you need has been reprinted and crashed in price. Everything is plain markdown and hosted
image links in your own vault — no account, no lock-in.

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
   whenever you buy something. This is what turns a deck note into a shopping decision.
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
| 📄 Local `.txt` decklist | Read directly — no network for the list itself |

A `.txt` file is one `1 Card Name` per line (the usual export format), **first card is the
commander**, and the filename becomes the deck name. After a successful import it's moved
into `imports/` inside your vault, so any machine with the vault can re-import it.

<details>
<summary>Re-imports, and several decks sharing one commander</summary>

An existing note is **never overwritten without `--force`**, and even then everything you've
written in the review sections is preserved — the rebuild refreshes the data *around* your
notes. A note counts as the *same deck* when its `deck-url` or `deck-name` matches, whatever
the import date, and `--force` updates it in place keeping the original dated filename.

**Multiple builds per commander** are fine. The first keeps the plain
`YYYY-MM-DD_MTG_<Commander>.md` name; another build of the same commander gets a
`" - <deck name>"` suffix instead of colliding — so name your `.txt` files meaningfully
(`Cloud Limit Break Precon.txt` becomes that deck's name).
</details>

## 🗃️ Your collection

The collection file is how the app knows which cards you already own. It lives at
`_Collection.md` in your output folder (or wherever `COLLECTION_FILE` points).

**The format is one card per line, `N Card Name` — the same as a deck list.** That's the
whole spec. Only lines starting with a digit are read, so headings, notes and tables can sit
anywhere around the list and are ignored:

```markdown
# 🗃️ My Card Collection

Anything that isn't a card line is ignored, so notes like this are fine.

1 Sol Ring
3 Lightning Bolt
23 Mountain
```

Point `--collection` at any card-list export and it writes the file for you: duplicate rows
merged (exports split one card across printings — those are all still copies you own), set
codes and `*F*` foil markers stripped, sorted alphabetically. Already have one?
`--collection` **won't overwrite it** — it's hand-curated and not reproducible from an
export, so use `--merge-collection`, which appends only genuinely new cards under a dated
heading and never deletes anything.

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

Two kinds of printing are skipped, because "cheapest" has to mean *cheapest you could
actually buy and play*:

- **Gold- and silver-bordered cards** — World Championship decks, Pro Tour Collector Sets and
  Collectors' Edition. They're replicas, illegal in every format, and routinely among a
  card's cheapest listings. Five of Birds of Paradise's six cheapest rows are championship
  cards.
- **Printings priced in only one market** — a EUR price with no USD price at all means one
  lonely European listing and nothing to corroborate it. That's how a €3.00 *Summer Magic*
  Birds of Paradise appears, for a 1994 test print worth thousands.

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
| `YYYY-MM-DD_MTG_<Commander>.md` | The deck note — one per deck |
| `Attachments/YYYY-MM-DD_MTG_<Commander>.jpg` | Commander art (offline backup) |
| `_Decks.md` | Auto-generated master index of every deck — never edit by hand |
| `imports/` | Archived `.txt` decklists, so any machine can re-import |
| `_analysis-briefs/` | `--brief` output |
| `YYYY-MM-DD_MTG-Collection_<name>.md` | `--set` collection checklists |
| `.price-history.json` | Dated price snapshots behind the 📉 Price History tables |

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

1. 💰 **Deck Value** — totals per market with a ≈ GBP column (also in the frontmatter for
   Dataview) and the 🛒 cost-to-finish line.
2. 📉 **Price History** — a collapsed table of the last 8 price checks (deck value, cost to
   finish, cheapest finish) with the overall trend in the title. The console also flags
   notable drops and per-card price crashes — usually a reprint, and the "time to buy" signal
   for a wishlist deck.
3. 🖼️ **Commander image** — a link to Scryfall's hosted image, so it survives being read
   outside Obsidian; a local copy goes to `Attachments/` as an offline backup.
4. ✍️ **Your review sections** — 🧠 First Impressions · 💪 Strengths · ⚠️ Weaknesses ·
   🔄 Cards to Consider Swapping · 📝 Play Notes · 🧭 Deck Guide. Empty headings,
   **preserved through every rebuild**.
5. 📊 **Deck Shape** — locally computed, no AI: type counts, mana curve, keyword role
   buckets, and a bracket checklist (Game Changers, extra turns, mass land denial). Followed
   by three more preserved headings — 🎮 Play Pattern · 🏆 Win Conditions ·
   ⚠️ Interactions & Warnings — for you or `/analyse-deck` to fill.
6. 💰 **Card Prices** and 🖼️ **Card Gallery** — every card dearest-first (EUR / USD / ≈ GBP),
   and a 4-column grid of hosted card images. Both in collapsible callouts.
7. 📜 **Deck List** — commander first, then mainboard alphabetically; pastes straight back
   into Moxfield or Arena.
8. 🛒 **Cards to Complete the Deck** — what you're missing, plus a copy-paste 📋 Buy List.
9. 💸 **Cheapest Build** — the whole deck at each card's cheapest Cardmarket printing, with a
   decklist carrying `(SET) 123` pins for the exact printings.
10. 🛒 **Cards to Complete — Cheapest Build** — the same missing cards at those cheapest
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
| `--own <source>` | Append the whole deck to your collection as owned, *then* compare. For when you actually buy a wishlist precon. Usually paired with `--force`. |
| **Refresh** | |
| `--recheck [id]` | Full re-import from the original source: deck edits, fresh prices, gallery, and both Cards-to-Complete sections. Art is reused unless the commander changed. Unreachable source → falls back to re-pricing the list stored in the note, so a run never stops. |
| `--reimport [id]` | Deck list and art only, **no new prices**. Leaves all price, buy and Cheapest Build sections untouched. Flags any deck whose list changed so you know to `--recheck` it. |
| **Manage** | |
| `--list` | Print every deck's id and name, then exit — the quickest way to find the id another command wants. |
| `--delete [id]` | Delete a deck and **everything it owns** — note, commander art, archived `.txt`, brief, price-history entry. Shows the exact file list and confirms first; `-y` skips the prompt. Reindexes afterwards. |
| `--reindex` | Renumber every note to a gap-free, unique `1..N` sequence, fixing ids gone missing or duplicated; remaps history and briefs. Runs automatically after `--delete`. |
| `--index` | Rebuild `_Decks.md` from the notes' current frontmatter. No network. (Also runs after every import and recheck.) |
| **Collection** | |
| `--collection <file>` | **Create** the collection file from an export. Refuses to overwrite a populated collection unless you add `--force`. |
| `--merge-collection <file>` | **Add to** an existing collection — append-only. Cards missing from the export are only *reported*, never deleted. |
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
