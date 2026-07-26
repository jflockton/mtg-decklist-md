---
name: deck-guide
description: Analyse one of James's MTG deck notes and write a tailored strategy guide into its 🧭 Deck Guide section. Invoke with a deck id (see --list) or a commander/deck name, e.g. /deck-guide 3 or /deck-guide krenko.
---

# Deck Guide writer

You are writing a strategy guide for one Commander deck, directly into its
Obsidian note. The guide is hand-crafted analysis — the one thing the importer
script cannot generate.

## Finding the note

1. Resolve the vault folder from `VAULT_OUTPUT_DIR` in `.env` (next to
   `mtg_deck_importer.py`).
2. The argument is either a deck id (run
   `python mtg_deck_importer.py --list` to resolve it) or a fragment of the
   commander/deck name — match it case-insensitively against the
   `????-??-??_MTG_*.md` filenames and `deck-name:` frontmatter.
3. If it matches nothing or more than one note, list the candidates and stop.

## What to read before writing

- The note itself: full deck list, Cards to Complete / buy data, prices,
  what James has already written in the review sections (never contradict or
  overwrite his own notes — reference them if relevant).
- `_Collection.md` context is already reflected in the note's owned counts.
- Use your Magic knowledge for card roles and interactions. If unsure about a
  specific card's exact text, check Scryfall
  (`https://api.scryfall.com/cards/named?exact=<name>`, User-Agent header
  required) rather than guessing — wrong rules text in a guide is worse than
  no guide.

## Where the guide goes

Replace the body of the `## 🧭 Deck Guide` section (it is `-` when empty).
Everything else in the note is off-limits. The importer preserves this
section across --force/--recheck, same as the review sections.

**Use `###` or smaller headings inside the guide — never `##`** (a `##` would
break the section-preservation regex).

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
blue, ⚫ = black, 🔴 = red, 🟢 = green; generic cost is a plain number or X,
**followed by a comma** when coloured pips come after it (so "2,⚫" is
unambiguously 2 generic + one black, never two black). Pips repeat, one dot
per pip — never a count after the comma. So Doom is 1,🔵⚫🔴; mayhem
{3}{U}{R} is "mayhem 3,🔵🔴"; {X}{R}{R} is X,🔴🔴; {B}{B}{B} is ⚫⚫⚫ (no
generic part, no comma). Write {T} as "Tap:" and true colourless {C} as ◇.

## Structure (adapt, don't pad — skip a block if the deck doesn't need it)

1. **The 100, by role** — group every card into functional categories with
   emoji headings (### 👑 Commander, ### 🌱 Ramp, ### 🎯 Removal, ### 🏆
   Threats & wincons, ### 🏞️ Lands …). Counts per category. One-line notes
   only where a card is non-obvious; plain lists where it isn't. The
   categories should teach how the deck is shaped.
2. **🎮 Play pattern** — turns 1–2 / 3–4 / 5+ and how the deck closes out;
   name the actual cards in each phase.
3. **⚠️ Warnings & non-obvious interactions** — numbered; rules traps,
   anti-synergies, table-politics warnings (mark salt with ⚠️).
4. **✅ Bracket justification** — Game Changer count, mass land denial,
   extra turns, two-card infinites, tutor count; state the bracket it fits.
5. **💰 Budget notes** — use the note's own price data; GBP-first (James is
   UK — recommend Cardmarket/Magic Madhouse, warn about US shipping); name
   the expensive cards and what to cut to hit a lower budget.
6. **📈 Upgrade path** — ordered next-buys toward the bracket above.

## Voice

Match James's vault style: direct, technical, emoji on headings and key
points, no filler. Tables for enumerable facts, prose for reasoning. British
English. It should read like a knowledgeable mate's crib sheet, not a
magazine article.

## Afterwards

Tell James the guide is in, with a 2–3 sentence summary of the deck's plan
and the one warning he most needs to know. Do not run the importer.
