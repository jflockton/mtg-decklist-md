# MTG Deck Importer

Single-script Python tool (`mtg_deck_importer.py`) that turns Moxfield / EDHREC /
Archidekt / `.txt` decklists into priced deck notes in James's Obsidian vault.
Vault paths come from `.env` (`VAULT_OUTPUT_DIR`). Never touch the real vault in
tests — the `VAULT_OUTPUT_DIR` environment variable overrides `.env`, so point it
at a scratch directory.

## Vault linking convention — applies to ALL new files, including new .py logic

Every new markdown file created in the vault by this repo or its skills MUST link
back to the project, or it floats as an island in the Obsidian graph:

- frontmatter: `project: "[[02 - Projects/MTG Deck Importer/_Current State|MTG Deck Importer]]"`
- a visible `**Project:** [[02 - Projects/MTG Deck Importer/_Current State|MTG Deck Importer]]`
  line near the top of the body
- a file that serves one deck/collection also wiki-links that note (analysis
  briefs do this via their `Note file:` line; `_buying-test` notes via a
  `**For:**` line)

In Python, use the `PROJECT_LINK` constant in `mtg_deck_importer.py` rather than
hard-coding the path again. The full path is required — other vault projects also
have a `_Current State.md`, so a bare `[[_Current State]]` is ambiguous.

Gotcha: `--reimport` splices sections into an existing note in place and does NOT
apply note-template changes; only a fresh import or `--recheck` rebuilds a note
from the template.

## Obsidian sync (part of "done" for significant changes)

1. Update the vault dashboard `02 - Projects/MTG Deck Importer/_Current State.md`
   (overwrite-style: bump `updated:`, new "Done recently" block demoting the old
   one, keep watch-outs current).
2. Mirror the repo README to the same vault folder, prose paragraphs unwrapped to
   single lines (vault convention: never hard-wrap; list/table/heading/fence
   lines stay as-is).
3. Commit and push.
