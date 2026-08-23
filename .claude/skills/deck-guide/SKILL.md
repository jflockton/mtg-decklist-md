---
name: deck-guide
description: Analyse one of James's MTG deck notes and write a tailored strategy guide into its 🧭 Deck Guide section — card roles, a deck-specific mulligan/opening-hand guide (what to keep, what to ship), play pattern, win conditions, rules traps, bracket, budget swaps and upgrades. Invoke with a deck id (see the _Decks.md index) or a commander/deck name, e.g. /deck-guide 3 or /deck-guide krenko.
---

# Deck Guide writer

You are writing a strategy guide for one Commander deck, directly into its
Obsidian note. The guide is hand-crafted analysis — the one thing the importer
script cannot generate.

## Finding the note

Deck notes live in the vault at `03 - Personal/MTG/` —
`C:\Users\James\Dropbox\obsidianVault\` on Windows, `~/Dropbox/obsidianVault/`
on the Mac. Read the vault directly; do **not** go hunting for a repo, a `.env`
or the importer script — this skill works from any session.

1. `03 - Personal/MTG/_Decks.md` is the deck index. Its `| # |` column is the
   deck id and each row wiki-links the note, so it resolves an id argument
   without running anything.
2. The argument is either a deck id (look it up in that table) or a fragment of
   the commander/deck name — match it case-insensitively against the deck-note
   filenames (the bare deck name, e.g. `Brago, King Eternal.md`, with a
   ` - <deck name>` suffix on alternate builds) and the `deck-name:` frontmatter.
3. If it matches nothing or more than one note, list the candidates and stop.
4. `_`-prefixed notes are not decks — `_Decks.md`, `_Collection*.md`,
   `_To-Buy.md` are index and collection notes, never targets.

## What to read before writing

- The note itself: full deck list, Cards to Complete / buy data, prices,
  what James has already written in the review sections (never contradict or
  overwrite his own notes — reference them if relevant).
- ⚠️ Deck notes run 800+ lines and a single Read will truncate. The price
  data you want for the budget block — 💸 Cheapest Build and 🛒 Cards to
  Complete — Cheapest Build — are the **last two sections**. Page to the end
  or jump straight to them; don't build budget figures from the first buy
  table you happen to hit on the way down.
- `## 📊 Deck Shape` — computed type counts and the mana-curve table. This is
  the factual basis for the mulligan guide's keep rule; don't eyeball the curve
  when the note has already counted it.
- `_Collection.md` context is already reflected in the note's owned counts.
- Use your Magic knowledge for card roles and interactions. If unsure about a
  specific card's exact text, check Scryfall
  (`https://api.scryfall.com/cards/named?exact=<name>`, User-Agent header
  required) rather than guessing — wrong rules text in a guide is worse than
  no guide.

## Where the guide goes

Replace the body of the `## 🧭 Deck Guide` section (it is `-` when empty). The
importer preserves this section across --force/--recheck, same as the review
sections. Apart from the two edits below, everything else in the note is
off-limits.

**Delete the three empty analysis stubs.** The guide covers play pattern, win
conditions and rules traps in its own `###` blocks, so the standalone versions
would only duplicate it. Remove the heading *and* its `-` body for each of:

```
## 🎮 Play Pattern

-

## 🏆 Win Conditions

-

## ⚠️ Interactions & Warnings

-
```

**Only delete a stub that is still `-`.** If a section already has prose in it
(from `/analyse-deck`, or written by hand), leave that section completely
alone — do not delete it, do not rewrite it, and reference it from the guide
rather than contradicting it. Mention in your sign-off which ones you left.

The importer follows the same rule: once 🧭 Deck Guide has content, a rebuild
stops emitting empty stubs for those three, so they won't come back on the next
`--recheck` — but any that still hold prose are kept forever.

**Use `###` or smaller headings inside the guide — never `##`** (a `##` would
break the section-preservation regex, and `##` headings are what the note's
🗂️ Contents table lists).

**Then fix up the 🗂️ Contents table.** Notes open with a `## 🗂️ Contents` table
listing every section, one row each. Two edits, both required:

- Drop the `— ✍️ *empty*` marker from the 🧭 Deck Guide row (leave the rest of
  the cell as-is) so the table doesn't call your new guide empty.
- **Delete the rows for the stubs you removed.** A row whose section no longer
  exists is a dead link. Only delete rows for sections you actually deleted.

If the note has no Contents table yet, leave it alone — the next `--recheck`
builds one from the note's real headings.

**Never hard-wrap prose.** Obsidian renders every single newline as a real
line break, so 80-column wrapping shows as ragged lines mid-sentence. Write
each paragraph, bullet and numbered item as ONE long line and let Obsidian
soft-wrap. Newlines only between blocks (headings, table rows, list items,
blank lines).

**Define shorthand on first use.** James is still learning MTG jargon — the
first time the guide uses ETB ("enters the battlefield"), the bin
(graveyard), or a keyword mechanic (connive, mayhem, miracle…), gloss it in
brackets or a one-liner. After that, use the shorthand freely.

**Mana costs as emoji dots, never `{U}`-style symbols.** ⚪ = white, 🔵 =
blue, ⚫ = black, 🔴 = red, 🟢 = green; generic cost is a plain number or X.
**Comma-separate every element** so a number can never be misread as
multiplying a dot: Doom is 1,🔵,⚫,🔴; mayhem {3}{U}{R} is "mayhem 3,🔵,🔴";
{X}{R}{R} is X,🔴,🔴; {B}{B}{B} is ⚫,⚫,⚫; a lone {2}{B} is 2,⚫. One dot
per pip — never a count in front of a dot. Write {T} as "Tap:" and true
colourless {C} as ◇.

## Structure (adapt, don't pad — skip a block if the deck doesn't need it)

1. **The 100, by role** — group every card into functional categories with
   emoji headings (### 👑 Commander, ### 🌱 Ramp, ### 🎯 Removal, ### 🏆
   Threats & wincons, ### 🏞️ Lands …). Counts per category. One-line notes
   only where a card is non-obvious; plain lists where it isn't. The
   categories should teach how the deck is shaped.
2. **✋ Mulligan guide** — what a keepable opening hand looks like for THIS deck. **Derive it from the list, never generalise:** the note's `## 📊 Deck Shape` section already gives you the type counts and the `| Mana value |` curve table — use them, plus the land and ramp counts from your own §1 role breakdown; read the curve to see the turn the deck wants to be doing something; note which colour pips the early plays demand; and identify the two or three cards the deck genuinely needs to function versus the ones that are luxuries. Cover, as `####` sub-blocks or a tight list:
   - **The keep rule** — the land range to keep on seven, and how it tightens on six, stated as real numbers derived from this deck's land count and curve, with the reasoning in one line (a 38-land deck with a 2-drop commander keeps differently from a 32-land deck with rocks).
   - **✅ Snap-keeps** — two or three example hands built from cards actually in this list, each with a one-line "why this is fine".
   - **❌ Auto-mulligans** — the specific shapes that lose *with this deck*: name the trap (all lands and no early play, colour-screwed off a heavy pip, engine pieces with no mana to cast them, a hand that does nothing before turn 5).
   - **🎯 What you're digging for** — the named cards worth keeping a marginal hand to cast, and the ones that look exciting but are win-more (not worth keeping a bad hand for).
   - **👑 Commander dependence** — whether the deck can function without casting the commander on curve. This changes what a keep is: a deck that needs its commander keeps hands that cast it, a deck that merely likes it can keep on generic value.
   Gloss the **London mulligan** on first use — draw a fresh seven each time, then put one card on the bottom per mulligan taken, so a "mulligan to five" is seven cards with two bottomed. Be concrete throughout: name cards, give numbers. No "keep a balanced hand" filler.
3. **🎮 Play pattern** — turns 1–2 / 3–4 / 5+ and how the deck closes out;
   name the actual cards in each phase. Sequencing specific to THIS list (what
   to play first, what to hold), not generic Commander advice.
4. **🏆 Win conditions** — numbered, primary first, naming exact cards. Say
   plainly if the deck has no fast kill and wins on attrition. List every
   piece of any infinite combo explicitly. This block replaces the standalone
   🏆 Win Conditions section you deleted, so it has to stand on its own.
5. **⚠️ Warnings & non-obvious interactions** — numbered; rules traps,
   anti-synergies, table-politics warnings (mark salt with ⚠️). Rules-TRUE
   only: if you are unsure a ruling is right, check Scryfall or leave it out.
6. **✅ Bracket justification** — Game Changer count, mass land denial,
   extra turns, two-card infinites, tutor count; state the bracket it fits.
7. **💰 Budget notes** — use the note's own price data; GBP-first (James is
   UK — recommend Cardmarket/Magic Madhouse, warn about US shipping); name
   the expensive cards and what to cut to hit a lower budget.

   ⚠️ **Price everything off `## 🛒 Cards to Complete — Cheapest Build`, the
   note's last section.** The importer already does this work: that table is
   dearest-first, one ≈ GBP figure per card, cheapest-printing basis, missing
   cards only — so it's the bill he'll actually pay and an owned card can't
   leak in as a phantom saving. Its total is `buy-cheapest-gbp`. Don't build
   budget figures from the earlier 🛒 Cards to Complete (deck's own printings,
   15–20% dearer) — quote that one only if you explicitly label it.

   **Always include a cheaper-alternatives table** for the deck's priciest
   cards (roughly anything over ~£4, and certainly the top handful that
   dominate the bill): `| Cut | ≈ saved | Replace with | ≈ cost |`, where
   each replacement is a *functional* substitute — same job, honest note on
   what's lost — not just any cheap card. Verify replacement prices against
   Scryfall (`prices.eur` on the cheapest non-digital printing, converted at
   the note's own EUR→GBP rate) and flag when a swap also changes the
   bracket (e.g. cutting a Game Changer).

   **Do the sum before you state a total.** `≈ saved` is that table's price
   for the cut card minus the replacement's; the column must add up to the
   drop you claim, i.e. `buy-cheapest-gbp − Σsaved = the "down to ≈ £X"
   figure`. Quote the total saving as well as the end price so the arithmetic
   is checkable at a glance.

   For a deck James already fully owns, frame it as "which cards could move
   to another deck and what would slot in here" rather than a buy decision.
8. **📈 Upgrade path** — ordered next-buys toward the bracket above.

## Voice

Match James's vault style: direct, technical, emoji on headings and key
points, no filler. Tables for enumerable facts, prose for reasoning. British
English. It should read like a knowledgeable mate's crib sheet, not a
magazine article.

## Afterwards

Tell James the guide is in, with a 2–3 sentence summary of the deck's plan
and the one warning he most needs to know, plus **the keep rule in one line** ("keep 3–5 lands with a turn-2 play") so he has the mulligan heuristic without opening the note. Say which of the three analysis
stubs you removed, and name any you left in place because they already had
prose. Do not run the importer.

## Vault linking convention

Any **new** markdown file this skill (or any MTG Deck Importer tooling) writes into
the vault MUST carry the project link, or it floats as an island in the Obsidian graph:

- frontmatter: `project: "[[02 - Projects/MTG Deck Importer/_Current State|MTG Deck Importer]]"`
- a visible `**Project:** [[02 - Projects/MTG Deck Importer/_Current State|MTG Deck Importer]]` line near the top
- a file that serves one deck/collection also wiki-links that note (e.g. a `**For:**` line)

Editing an existing note: leave its links alone.
