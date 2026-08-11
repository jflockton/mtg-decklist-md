---
name: analyse-deck
description: Write the 🎮 Play Pattern / 🏆 Win Conditions / ⚠️ Interactions & Warnings sections of a deck note from its --brief file. Use when asked to "analyse deck N" (or several). Token-lean by design — the brief is the only input needed.
---

# Analyse a deck (token-lean workflow)

The Python importer owns all facts (prices, ownership, 📊 Deck Shape). Your
only job is judgement: the three prose sections. Everything you need is in
the brief — spend tokens on thinking, not on reading.

## Steps

1. Find the brief: `<vault>/_analysis-briefs/<NN> - <deck name>.md`
   (vault = VAULT_OUTPUT_DIR from `.env`). If missing or the user says the
   list changed, generate it first:
   `python mtg_deck_importer.py --brief <id>`
2. Read ONLY the brief. Do NOT read the full deck note, price tables, or the
   web. The brief contains the deck shape, role groups, full list, and oracle
   text for any card new enough that you might not know it (🆕 section) —
   trust it over memory for those cards.
3. Write the three sections and insert each with a single Edit into the deck
   note (path is named in the brief). Each empty section looks like:

   `## 🎮 Play Pattern\n\n-`

   Replace the `-` placeholder with your prose. Never touch anything outside
   the three headings; never run --recheck as part of this.
4. Tick each section's row in the note's `## 🗂️ Contents` table: hand-written
   sections carry a `— ✍️ *empty*` marker, so drop that marker from the three
   rows you filled (leave the rest of each cell as-is). If the note has no
   Contents table yet, leave it — the next `--recheck` builds one.

## What to write

- **🎮 Play Pattern** — turns 1–3, mid-game, and how the deck closes;
  sequencing tips specific to THIS list (what to play first, what to hold).
  No generic Commander advice.
- **🏆 Win Conditions** — numbered, primary first, naming exact cards; call
  out infinite combos explicitly with all pieces listed.
- **⚠️ Interactions & Warnings** — only rules-TRUE, list-specific gotchas
  (the "never blink what Agent of Treachery stole" class). If unsure a
  ruling is correct, leave it out — a wrong warning is worse than none.

## Style

- UK English, concise: ≤ ~200 words per section. The reader is the pilot.
- Bold card names at first mention in a section. Emoji sparingly, headers
  never (the `##` headings already exist).
- If the deck is a budget variant, mention what the swaps changed about how
  it plays (the brief's deck name and shape usually make this obvious).

## After all requested decks

Reply with one line per deck (id, deck, the primary win condition you
identified) — not the full prose, the user reads that in Obsidian.

## Vault linking convention

Any **new** markdown file this skill (or any MTG Deck Importer tooling) writes into
the vault MUST carry the project link, or it floats as an island in the Obsidian graph:

- frontmatter: `project: "[[02 - Projects/MTG Deck Importer/_Current State|MTG Deck Importer]]"`
- a visible `**Project:** [[02 - Projects/MTG Deck Importer/_Current State|MTG Deck Importer]]` line near the top
- a file that serves one deck/collection also wiki-links that note (e.g. a `**For:**` line)

Editing an existing note: leave its links alone.
