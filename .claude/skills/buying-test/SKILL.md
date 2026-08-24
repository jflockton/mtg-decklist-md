---
name: buying-test
description: Price a set of MTG cards — a deck's buy list, a wish list from the MTG CardVault app, OR a section of one of James's _Collection notes — across four UK sources (Cardmarket, magecards.co.uk, magicmadhouse.co.uk, trolltradercards.com), English listings only, and solve for the CHEAPEST CROSS-SITE SPLIT on landed cost including postage — splitting the order across sites is encouraged when it saves money — while also reporting the fewest-sellers plan as the price of convenience. Exploits Magic Madhouse's free UK delivery over £40 by proposing top-up cards from _To-Buy.md. Writes the plans into the vault's _buying-test folder and leaves the recommended plan sitting in the baskets, ready to pay. Answers "what's the cheapest way to actually buy this lot, and who has it?" Invoke as /buying-test <deck name or text file>, /buying-test wishlist [name] to price a CardVault wish list straight out of its database, or /buying-test collection [name] [section] — name the collection to skip the prompt, e.g. /buying-test collection spiderman. Add a value filter — --under <£> / --over <£> — to scope to a price band (e.g. /buying-test collection spiderman --under 1, or --under 0.10 for the pennies).
---

# Fewest-seller coverage finder (decks & collections)

Takes a set of cards — a **deck buy list** or a **section of a `_Collection` note** —
prices it across **four UK sources** (**cardmarket.com**, **magecards.co.uk**,
**magicmadhouse.co.uk** and **trolltradercards.com**), works out both the **smallest
set of sellers that stocks it** and the **cheapest cross-site split**, and writes the
plans to the vault. All four run on the same list so the totals are directly comparable.

The four sources are **not the same kind of thing** and must not be treated alike:
- **Cardmarket** — a marketplace of thousands of sellers. Set-cover applies; its
  own Shopping Wizard does the solving. Postage per seller.
- **Mage Cards** — a smaller UK marketplace. Set-cover in principle, but usually
  too thin for it. Flat £1.11 postage per seller.
- **Magic Madhouse** — a **single shop**, not a marketplace (see §2c). Set-cover is
  meaningless (it's one parcel or nothing); the questions are *what does it stock*
  and *does the order clear the £40 free-delivery threshold*.
- **Troll Trader Cards** — also a **single shop** (see §2d), on CrystalCommerce.
  Flat **£2.00 tracked UK postage**, no free-delivery threshold. Its big advantage
  over MMH is that **every search row names the set**, so exact-printing matching is
  actually possible; its disadvantage is retail pricing that rarely beats a
  marketplace.

**Three input modes** (see §1):
- **Deck mode** — a deck name or a text file; printing-agnostic (any printing counts).
- **Collection mode** — `/buying-test collection`; pins the **exact** printing and
  prices only the **unowned** cards in a chosen section.
- **Wish list mode** — `/buying-test wishlist`; reads a named wish list straight
  out of the **MTG CardVault** app's database. Printing-specific like collection
  mode, and skips anything already in stock.

**Never check out. Never log in for James.** Build the plans, price them, write
the notes, **fill the basket(s) for the recommended plan** (§5), then stop at the
checkout page.

**Two standing requirements on every listing picked, both marketplaces, both
modes:**
- 🇬🇧 **English only.** Every card must be an **EN** listing. Cardmarket happily
  returns DE/FR/IT/ES copies and they are usually the cheapest ones on the page,
  so an unfiltered run quietly prices a foreign-language deck. Set the filter
  where one exists, then **verify the language on every row you report**.
- 🛒 **The winner's basket gets filled.** The point of a priced plan is that James
  can go and buy it. A run that ends with an empty basket makes him redo the
  whole shop by hand.

> 🔌 **Both marketplaces need a login, and James is usually signed in on his own
> Chrome, not the isolated in-app Browser pane.** If the in-app pane shows a login
> screen while he says he's logged in, switch to driving his real Chrome:
> `list_connected_browsers` → `select_browser` → work in a fresh tab there. The
> two browsers have separate cookie jars.

## What this skill optimises — and how it differs from `buy-deck`

Every run produces **three plans**, and the third is usually the one he buys:

1. **Fewest sellers** — the minimum set of sellers covering the list, per site.
   The original lens: minimum set-cover / min-parcel.
2. **Cheapest per site** — the same list solved for money within one site.
3. 🏆 **Cheapest cross-site split** — the **globally cheapest plan**, free to take
   each card from whichever of the three sites is cheapest, postage included. This
   is the **headline answer**.

> 📌 **James's stated preference: "I don't care about ordering from several places
> if it saves money."** So the cross-site split leads the report and gets the
> basket. Fewest-sellers stays in as the comparison that tells him what
> convenience would cost — not as the recommendation.

Splitting across sites only pays when the card saving beats the extra postage, so
the split **must be solved on landed cost** (cards + every parcel's postage), never
on per-card price alone. A £2 saving that adds a £4.25 parcel is a £2.25 loss. See
§3 for the solve and the £40 Magic Madhouse threshold that often decides it.

This is still not the same problem `buy-deck` solves — `buy-deck` optimises one
site's basket; this skill compares three sites and reports the trade-offs.

For the deep marketplace-driving mechanics (Cloudflare, login state, cart APIs,
listing-row parsing, the regex-escaping trap, batch limits), the
[`buy-deck`](../buy-deck/SKILL.md) skill is the reference — this skill reuses all
of it and only restates what's specific to the coverage objective.

## 1. Get the card list

Decide the mode from the argument, then build the list.

### Deck mode — a text file or a deck name (printing-agnostic)

- **File path**: read it. Accept plain `Card Name` lines and buy-list lines like
  `1 Angelic Destiny (SOC) 134`. Drop a leading quantity, **strip `(SET) 123` /
  `[...]` pins** — printing is irrelevant, match on **name only** (normalise:
  lowercase, `'`→`'`, collapse whitespace, MDFCs split on `//` take the first
  face). Blank lines and `#`/`//` comments are ignored.
- **Deck name/id**: if the argument isn't a readable file and isn't `collection`,
  treat it as a deck. Vault decks live at `03 - Personal/MTG/`. Match filenames
  and `deck-id:` frontmatter (`_Decks.md` indexes them). Take the fenced block
  under `### 📋 Buy List (copy-paste)`. Ambiguous → list candidates and stop.

### Collection mode — `/buying-test collection [name] [section]` (printing-SPECIFIC)

Triggered when the first argument is `collection` (optionally followed by a
collection name and section), or when the argument names/points at a
`_Collection - *` note. **This mode inverts the deck rule: the exact printing
matters**, because the sections *are* printings.

1. **Which collection — name it to skip the question.** If the invocation named
   one, use it: `collection spiderman`, `collection ff`, `collection marvel`, or a
   `_Collection` note name. The `_Collection - *.md` notes live in
   `03 - Personal/MTG/` — currently **Final Fantasy** (`ff`), **Marvel Super
   Heroes** (`marvel`), **Marvel's Spider-Man** (`spiderman`), plus the master
   `_Collection.md`. Only if none was named, list them and let James pick. An
   ambiguous name → list candidates and stop; **never guess the collection.**
2. **Which section — or the whole collection.** If a section is named
   (`collection spiderman regular`, or a set code like `SPM`), use it. If **no
   section is given but a value filter is** (e.g. `--under 1`), span the **whole
   collection** — "every sub-£1 unowned card in Spider-Man" is a valid, common
   query. Otherwise parse the note's headings — top level `## SET — x/y · £z`
   (`FIN`, `FIC`, `PFIN`) and sub-level `### Treatment — x/y · £z` (Regular,
   Showcase, Full-art, …) — show the **unowned count** (`y − x`) next to each, and
   ask.
3. **Extract the section's UNOWNED cards.** Card lines look like:
   `` - [ ] `FIN 1` Summon: Bahamut — £19.31 ``. Take only **`- [ ]`** lines
   (skip `- [x]`, already owned). For each, capture **set code + collector
   number** (`` `FIN 1` ``), the **name**, and the note's **£ trend** figure.
   MDFCs appear as `Front // Summon: Back` — keep the front face for searching.
4. ⚠️ **Price the EXACT printing per card — never the name-based wizard/MageFinder.**
   `FIN 1` = Regular; `FIN 356` = the *Showcase* of the same name at a totally
   different price. Both optimisers are name-keyed and grab the **cheapest
   printing**, which is wrong for a collection — proven on a real run: the wizard
   priced Showcase *Summon: Bahamut* (trend ~£40) as the **£18.99 regular**, and
   quietly gave regular copies for foil-only wants. So in collection mode:
   - **Cardmarket**: land on the exact printing via the **`SET number` search
     code**. Type the code into the **live search box** (e.g. `fin 356`) and click
     the autocomplete suggestion — it resolves to the one printing, even when the
     treatment lives in a separate **Extras** expansion (FF Showcase/borderless =
     `FINAL FANTASY: Extras` / **XFIN**, not base FIN). ⚠️ The URL search
     (`Products/Search?searchString=`) does **not** do this — it must be the
     autocomplete box. **Verify the landed product's name + number match** the
     want (the search maps Scryfall numbering → Cardmarket product but isn't
     guaranteed 1:1). Then filter the product page to **UK** sellers and read the
     cheapest listing. Scryfall by `set/number` gives the canonical printing and a
     Cardmarket trend as a benchmark.
   - **Mage Cards**: hit the exact printing page directly —
     `/cards/<set-slug>/<number>/<card-slug>` (e.g.
     `/cards/final-fantasy/356/summon-bahamut`) — and read its seller rows. Do
     **not** use MageFinder here.
   - Then run **our own fewest-sellers greedy** over the per-printing listings
     (pick the seller covering the most still-uncovered cards; tie-break cheapest).
   - Flag any card where only the wrong treatment / a foil / a non-EN copy is
     stocked, and anything unobtainable at the wanted printing.

### Wish list mode — `/buying-test wishlist [name]` (printing-SPECIFIC)

Triggered by `wishlist` / `wish` as the first argument. A **wish list** is a named
list of cards to buy kept in the **MTG CardVault** app (`C:\projects\mtg-cardvault`)
— built by clicking **☆ Add to wish list** on cards in its inventory browser. This
mode prices one straight from the app's database, so nothing has to be exported by
hand.

**Like collection mode, the exact printing matters.** A wish list holds *printings*
— James picked that art — so price `SLD 2652` as that Secret Lair, not the cheapest
*Rogue's Passage* on the market. Use the collection-mode per-card machinery in §1
(Cardmarket autocomplete `SET number` search code, Mage Cards direct printing URL),
**not** the name-keyed Shopping Wizard / MageFinder.
- `--any-printing` relaxes this to deck-mode behaviour (name-only, cheapest
  printing wins, both optimisers back in play). Offer it when a list is mostly
  ordinary cards where the art plainly does not matter — it is far faster and
  usually cheaper — but never assume it.

**1. Read the lists.** The inventory DB is the app's *precious* file and may sit in
Dropbox, so resolve its location the way the app does — `location.json` in the
local data dir points at it, falling back to that same dir. `reference.db` (set
codes, collector numbers, Cardmarket trend) always stays local. **Read-only,
always** — `mode=ro`; never write to the shop's inventory:

```python
import os, sqlite3, json
local = os.path.join(os.environ['APPDATA'], 'mtg-cardvault', 'data')
inv_dir = local
try:
    inv_dir = json.load(open(os.path.join(local, 'location.json'), encoding='utf8'))['inventoryDir']
except Exception:
    pass
con = sqlite3.connect(f"file:{os.path.join(inv_dir, 'inventory.db')}?mode=ro", uri=True)
con.execute('ATTACH DATABASE ? AS ref', (f"file:{os.path.join(local, 'reference.db')}?mode=ro",))
# the lists, newest first
print(con.execute("""SELECT w.id, w.name,
       (SELECT COUNT(*) FROM wishlist_cards c WHERE c.wishlist_id = w.id) AS cards
     FROM wishlists w ORDER BY w.updated_at DESC""").fetchall())
# one list's cards: name, printing, Cardmarket trend (EUR), and two stock counts —
# this exact printing (what the app's badge counts) and any printing of the name
print(con.execute("""SELECT wc.name, r.set_code, r.collector_number, r.prices_eur,
       COALESCE((SELECT SUM(i.quantity) FROM inventory i
                 WHERE i.scryfall_id = wc.scryfall_id), 0) AS owned_exact,
       COALESCE((SELECT SUM(i.quantity) FROM inventory i
                 WHERE i.name = wc.name COLLATE NOCASE), 0) AS owned_any
     FROM wishlist_cards wc
     LEFT JOIN ref.scryfall_cards r ON r.scryfall_id = wc.scryfall_id
     WHERE wc.wishlist_id = ? ORDER BY wc.id DESC""", (LIST_ID,)).fetchall())
```

- **Which list — name it to skip the question.** `wishlist limit break` matches on
  a case-insensitive substring of the name. No name → print the lists with their
  card counts and ask. Ambiguous → list the candidates and stop; **never guess.**
- **No `wishlists` table / no lists yet** → say so plainly: the app creates the
  tables on first launch, and cards go on a list via **☆ Add to wish list** in
  Show Inventory. Don't fall back to another mode uninvited.
- **A missing `reference.db`** (or a card it doesn't know) leaves set/number/price
  null. Price those by name, and **flag them** — an unpinned card is exactly the
  case where the wrong printing gets bought.

**2. Drop what's already in stock — on the *exact* printing.** The app's
*in stock ×N* badge counts only the printing on the list (that is what
`owned_exact` is), and this mode follows it: a card whose pinned printing is
already in the shop is dropped, and **report what you skipped** ("3 of 12 already
in stock, not priced"). A wish list outlives the buying of its cards, so this is
the difference between a buy list and a re-buy.
- ⚠️ **Owning a *different* printing is a flag, not a skip** (`owned_any` >
  `owned_exact`). He pinned this art deliberately, so keep the card in the plan
  and note "you already own another printing of this" — cheap to say, and it
  catches the case where the pin was incidental rather than wanted.
- `--include-owned` prices the lot anyway (a playset, a second copy for another
  deck).

**3. Value filter.** `--under` / `--over` work as everywhere else, but the figure
here is **`prices_eur`** — Scryfall's Cardmarket trend in **euros**, not the £ the
`_Collection` notes carry. Convert before comparing, at the ECB daily rate the app
itself uses (`https://api.frankfurter.dev/v1/latest?base=EUR&symbols=GBP`); if that
call fails, filter in € and **say the threshold was read as euros**.

**Base list name** for the output files: `Wishlist <list name>` (e.g. `Wishlist
Limit break upgrade`), plus any filter suffix.

**Size check.** Same rule as collection mode — exact-printing pricing is one page
load per card per marketplace, so over ~40 cards, tell James the size and confirm
before starting the grind.

### All three modes

Keep a **base list name** for the output filenames — deck: the deck/file stem;
wish list: `Wishlist <list name>`;
collection: `<collection short> <SET> <Section>` (e.g. `Final Fantasy FIN
Regular`), or `<collection short> <filter>` for a whole-collection / value run
(e.g. `Spider-Man under-1`). De-duplicate; keep the requested count `N`.

**Value filter — `--under <£>` / `--over <£>` (every mode).** Scope the list by the
**trend** figure *before* touching any marketplace — the filter is free, because the
deck buy list and every `_Collection` line already carry a `≈ £` / `— £` figure, and
a wish list carries Scryfall's Cardmarket trend (in **€** — convert first, see wish
list mode).
- `--under 1` keeps only cards whose trend is below £1; `--under 0.10` keeps just
  the 2–9p bulk. `--over 5` keeps the chase cards; combine for a band
  (`--over 1 --under 5`). Thresholds are plain pounds — decimals fine (`0.10`).
- **Report what you excluded** — count and total value above/below the line — so a
  filtered run never reads as "the whole section" (no silent caps).
- ⚠️ **The £ is trend, a benchmark, not the live UK price** — a card at £0.98 trend
  can land just over £1 live. Filter on trend, then price for real, and flag any
  that cross the line once priced.
- 💡 **Why it earns its keep:** at low thresholds the cards are worth less than the
  postage to ship them, so a `--under 0.10` run is only sane if the fewest-seller
  solve lands them in **one or two parcels** — say so in the summary ("£1.90 of
  cards, don't split it across three envelopes"). It's also what makes a huge
  section tractable: `--under 1` can turn a 273-card section into ~40.

**Collection mode is per-card and Cardmarket rate-limits — pace it, and warn on
big sections.** Exact-printing pricing means **one page load per card per
marketplace**, and ⚠️ **Cardmarket throttles after ~5 rapid loads** (every
navigation then becomes the Cloudflare challenge and silently returns zero
results). So:
- Space Cardmarket product-page loads out; **never loop a big section blind**.
- ⚠️ **Cardmarket also logs James out mid-session** (a password field appears in
  the header; your username / LOG OUT vanish). A long paced grind *will* likely hit
  this — **checkpoint priced cards to a scratch file as you go**, and when it
  happens **stop and ask him to re-auth**, never log in. Resume from the
  checkpoint.
- A section like `FIN Regular` (≈273 cards) is **~550 paced loads** — genuinely
  slow. **Before starting a section over ~40 cards, tell James the size and
  confirm** he wants the full grind (offer to run it in the background / in
  batches, or to scope down to a treatment or price band).
- Mage Cards has no such limiter — its half can run straight through.
Report totals as the sum over all cards; list anything dropped or unobtainable.

## 2. Price all four sources on the same list

Price **Cardmarket**, **Mage Cards**, **Magic Madhouse** and **Troll Trader** independently
first — each site solved on its own terms (the two marketplaces for fewest sellers *and*
for cheapest; the two single shops for coverage and basket total). ⚠️ **Keep EVERY English offer you see — seller,
condition, quantity and price — not just the cheapest one.** A cheapest-per-card
grid cannot be consolidated, because the moment you discard the other offers you
have thrown away the only evidence that one seller stocks three of the cards. §3
and §3a both need the full roster; capturing only the winner per card is the single
commonest way this skill produces a postage-heavy plan. Do not skip a site because another one looked cheap: the
split can only find a saving across sources you actually priced.

> §2a/§2b below (Shopping Wizard, MageFinder) are the **deck-mode** engines —
> they're name-based / printing-agnostic and are the right tool when any printing
> counts. **Collection mode does not use them** (they mis-price treatments); it
> uses the exact-printing method in §1.4 and our own greedy. The login, browser,
> rate-limit and greedy notes below still apply. §2c (Magic Madhouse) is per-card
> searching in both modes, so it works the same either way.

### 2a. Cardmarket — use its native coverage tools

**Requires James logged in** (his username shows top-right) — check `document.title` for
`Login | Cardmarket`; if logged out, **stop and ask**, never log in. Cloudflare
"Just a moment…" clears in ~5 s and **rate-limits** aggressive browsing — pace
yourself, sample, never loop a whole deck. Decline non-essential cookies.

1. **Build a UK Wants list.** `/en/Magic/Wants` → **New List** → **ADD LIST** →
   lands on `/en/Magic/Wants/<id>`. ⚠️ **The name must be letters, spaces and the
   digits 1–9 only, ≤ 30 chars** — the field's own hint reads "A-Z; 1-9" and it
   means it: **`0` is rejected as well as punctuation**, silently, with no list
   created and no error shown. A date is therefore unusable — `To Buy general 2026
   08 18` failed on the zeros; `To Buy general list` worked. Use e.g.
   `Sythis coverage`. **Always confirm the list exists before moving on** — read
   back the new `/Wants/<id>` and its title, because a rejected name leaves you
   looking at some *other* list.
2. **ADD DECK LIST** → paste one `1 Card Name` per line → **ADD TO <LISTNAME>**.
   ⚠️ **Use real keystrokes here** (click the textarea, `type` the block): the
   native-setter + `input` event trick sets `.value` but the form submits *empty*,
   and you get a silent no-op. ⚠️ **`type` can also drop characters** at full
   speed, and a mangled name is silently discarded rather than flagged — so
   **reload and confirm "N Wants - N Cards" matches your N exactly**, and if it's
   short, diff the list against your input to find the mangled rows.
3. ⚠️ **Raise the condition floor AND pin the language** via **Bulk modification**
   — select all wants (header checkbox), then set both fields and **MODIFY
   SELECTED**:
   - **Min. Condition** (`minCondition`: `2`=NM, `3`=EX, `4`=GD …) — wants default
     to ≥ Poor and a Poor total isn't comparable. EX is a sensible floor.
   - 🇬🇧 **Languages → English** (`Languages` multi-select) — this is the one place
     the language constraint can be set once for the whole list, and it then flows
     into both the coverage panel and the Shopping Wizard. **Do not skip it.**
   Verify afterwards: the wants table should show your condition code and `EN` on
   all N rows.
4. **SELLERS WITH THE MOST CARDS** — this panel *is* the coverage answer. It
   ranks sellers by how many of your wants they stock. Filter to **United
   Kingdom**. Record the top sellers and their coverage counts.
5. **SHOPPING WIZARD** (`/en/Magic/Wants/ShoppingWizard?idWantsList=<id>`) —
   **run it twice** and report both, because the fewest-seller premium can be
   huge and James needs to see it:
   - **Drive it with real UI clicks**, not JS on the accordion — setting the
     hidden step controls by script and clicking Run just resets to step 1.
     Step 1 pick the list → **Next**; step 2 set filters → **Next**; step 3 pick
     strategy → **Run Wizard**.
   - Step 2 ⚠️ **Seller Location → United Kingdom** (`sellerCountry[]`, a
     multi-select) — avoids import VAT/handling.
   - Run **"Reduce Shipments"** (the fewest-sellers objective) **and** **"Reduce
     Price"** (the cheapest-per-card counterfactual). ⚠️ Reduce Shipments shows
     **0 % for ~60–70 s before completing** — that is *not* a stall, wait it out
     (title flips to "Results Summary"). Only fall back if it's still 0 % after
     ~2–3 min.
   - Each result page gives **Articles Value / Shipping Cost / Shipments / Total**
     and a **per-seller breakdown with per-card prices**. 🇬🇧 **Check the language
     on every row** — if a non-EN copy slipped through, the wants list wasn't
     pinned to English at step 3; fix it and re-run rather than reporting it.
   - ⚠️ **Keep the results URL** (`/Wants/ShoppingWizard/Results/<id>`) for each
     run. You need it again in §4 to go back and fill the basket, and it's the
     only way to return to a plan without re-running the wizard.
   - In the note, lead with the **Reduce Shipments** plan (fewest sellers) but
     always show the Reduce Price total beside it — on a real Sythis run the
     fewest-seller plan was **6 sellers / £247.57** vs **9 sellers / £112.15**
     cheapest: **£135 more to save 3 parcels.** That comparison is the point.
6. Cards the wizard couldn't place = **unavailable**; list them with the cheapest
   "From" price seen if any.

### 2b. magecards.co.uk — MageFinder, then a coverage read

**Requires login too** (James signs in on his own Chrome — see the browser note
up top). Use **MageFinder** (`/mage-finder`) — it takes the whole decklist at
once and is *far* better than scraping ~70 card pages. £1.11 postage per seller.

1. ⚠️ **The MageFinder buylist is saved to his account and is ADDITIVE.** It may
   already hold another deck's list; "Add cards to buylist" **appends**, so you
   get a polluted 100-card mix. **Clear it first:** **Remove all** → confirm **Go
   ahead**. This deletes whatever was saved there, so **ask James before clearing**
   — surface what's in it if it looks like a real in-progress shop.
2. **Add your decklist** → paste one `1 Card Name` per line → **Add cards to
   buylist**. Confirm the count reads your N (minus any unmatched). → **Find
   sellers** (needs login; bounces to `/login` if not).
3. Read the results page:
   - Header: **"We found S sellers that match C cards"**, an **Add all to cart
     (£X)** total, and **"N items were not found"**.
   - Each seller block: **"Matches k of C cards"**, a cart subtotal, and a table
     of `Card · Set · Condition · Lang · Qty · Price`.
   - 🇬🇧 **MageFinder has no language filter — the `Lang.` column is your only
     check.** Read it on every row. Anything that isn't `EN` must be flagged in the
     note and **excluded from the totals**, with the card treated as unavailable
     here rather than quietly counted as a hit.
4. ⚠️ **MageFinder optimises cheapest-per-card and its £X total EXCLUDES
   shipping.** It is *not* a fewest-seller plan — it spreads a deck over dozens of
   sellers (a real Sythis run: **68 cards over 49 sellers**, £120 cards-only,
   **~£174 once £1.11×49 postage is added**). Report the realistic total as
   `cards £X + ~£1.11 × sellers`.
5. **Fewest-sellers usually isn't achievable here — say so rather than fake it.**
   Because MageFinder only shows each card's *cheapest* seller, you can't build a
   true set-cover from it, and Mage Cards is typically too thin anyway (on Sythis
   the best-covered seller had just **5** cards, vs 48 on Cardmarket). Present the
   note as a **coverage snapshot ordered by card count**, with the fragmentation
   called out, not a tidy N-seller plan.
6. Unmatched / unavailable → list them. ⚠️ **MDFCs**: Cardmarket expands to
   `Front // Back`, but Mage Cards wants the **single front face** and may not
   stock it at all (Branchloft Pathway came back unavailable on Sythis).

Only fall back to per-card `/search?q=<name>` + card-page scraping (reading
`<seller> English <Condition> £<price>` rows, never aggregating a seller's
listings) if MageFinder is down. ⚠️ **Escaping trap** (from `buy-deck`): build
regexes with **no backslashes** (`[0-9]` not `\d`; `String.fromCharCode(163)` for
`£`) — zero matches everywhere is this bug.

### 2c. magicmadhouse.co.uk — one shop, stock-limited, £40 free delivery

**A single retailer, not a marketplace.** There are no sellers to cover, so
**skip the set-cover entirely**: MMH is one parcel, and the only questions are
*which cards it stocks* and *what the order totals*. Verified mechanics
(2026-08-18):

- **No login needed to browse or price**, and James is normally already signed in
  ("Hello, <his name>" in the header). Never log in for him.
- **No bulk/decklist tool exists.** The footer "BUYLIST" link is *them buying from
  you*, not a buy list. So it's **one search per card** — a 25-card list is 25 page
  loads. Say so up front on a long list and offer to scope it down.
- **Search:** `https://magicmadhouse.co.uk/search.php?search_query=<url-encoded
  name>`. The header reads `N Result(s) found for '<query>'`.
- ⚠️ **Parse the rendered text, not CSS classes.** The theme does not use
  `li.product` / `.price` / `.card-title` — those selectors return **zero** nodes and
  will look like "card not stocked". Read `document.body.innerText` and split on the
  literal `MAGIC: THE GATHERING` header; each tile is then
  `MAGIC: THE GATHERING` → `<Card name (Treatment)>` → `£<price>` → either
  **`Add to Cart`** (in stock) or **`out of stock`**.
- 🚫 **Out of stock is the norm, and it's the binding constraint.** Worked example:
  *Kindred Dominance* returned 10 printings, only 3 buyable (£4.99 base, £6.99
  Extended Art, £14.99 Surge Foil); *Impostor Syndrome* returned 4 printings, **all
  out of stock**. Treat every `out of stock` row as **not available here** — never
  quote its price as though you could buy it.
- **Take the cheapest in-stock row whose treatment is acceptable.** Treatment is in
  the product name in parentheses — `(Extended Art)`, `(Surge Foil)`,
  `(Borderless Art foil)`, `(Etched foil)`. The unparenthesised name is the plain
  printing and is normally cheapest. Flag any non-plain pick, same rule as the
  marketplaces.
- 🇬🇧 **English:** MMH splits language at the category level — `Singles (English)`
  / `Foils (English)` vs `Singles (Foreign)` / `Foils (Foreign)`, also offered as
  facets in the left-hand "SINGLE CARDS" panel. **Search results mix both**, so
  either filter to the English facet or reject any row whose name/category says
  Foreign. Never bank a foreign-language row.
- 💰 **Prices run above marketplace level** — it's retail, not a seller undercutting
  a seller. Expect it to lose on individual cards and win only via postage. Real
  comparison from the 2026-08-18 run: Kindred Dominance £4.99 MMH vs £4.30
  Cardmarket; Impostor Syndrome unbuyable at MMH vs £5.32 Cardmarket.
- 🚚 **Postage: "FREE UK DELIVERY AVAILABLE FROM £40"** (banner, site-wide). Below
  £40 the rates are **not published** — the Delivery & Returns page names the
  services (Royal Mail Tracked 48 / Tracked 24 / Express / DPD Saturday) but no
  prices, so **sub-£40 postage must be read from the cart at checkout, never
  invented**. Above £40 it is genuinely £0, which is what makes §3's top-up worth
  doing.

Report MMH as **coverage + one basket total**: how many of the N it can supply, the
cards subtotal, whether that clears £40, and the postage (£0, or the figure read
from the cart).

### 2d. trolltradercards.com — one shop, CrystalCommerce, flat £2 postage

**A single retailer like Magic Madhouse, so skip the set-cover** — it's one parcel or
nothing. Verified mechanics (2026-08-24):

- **No login needed to browse or price.** Never log in for James.
- 🚫 **The "BUYLIST" nav link is them buying from YOU** — a "Most Wanted List" with the
  prices Troll will pay for your cards (Underground Sea £331.52, Gaea's Cradle £572.87).
  It is **not** a bulk buy tool and has no decklist textarea. Same trap as MMH's BUYLIST.
  *(It is genuinely useful for selling spares — just not for this skill.)*
- **No bulk/decklist add exists**, so it's **one search per card**. Say so up front on a
  long list.
- **Search:** `https://www.trolltradercards.com/products/search?q=<url-encoded name>`.
- ✅ **Better than MMH: every row names the set.** Rows render as
  `NAME | Set name | £price | [Out of Stock]`, and alt treatments appear both in the
  product name (`- FOIL`, `- BORDERLESS`, `- EXTENDED ART`, `- SHOWCASE SCROLLS`) and as a
  ` - Alt Art` suffix on the set name. That makes exact-printing matching genuinely
  workable here, unlike MMH where you are guessing.
- 🎯 **Use Advanced Search to filter to in-stock only** — it saves reading a page of
  unbuyable rows:
  `/products/advanced_search?search%5Bfuzzy_search%5D=<name>&search%5Bin_stock%5D=1&utf8=%E2%9C%93`
  It also exposes `search[category_ids_with_descendants][]` (a 561-option set picker),
  `search[sell_price_gte]` / `[_lte]`, and sort — handy for pinning a treatment.
- **Parse the rendered text, not CSS classes** (same as MMH). Split
  `document.body.innerText` on the literal `View Product` and take the **last 3–4 lines**
  of each chunk — that's the tile. A chunk without `Out of Stock` is buyable.
- 🚫 **Out of stock is the norm here too** and is not a price. Only bank a row with no
  `Out of Stock` marker (or use the `in_stock` filter above).
- 🚚 **Postage: flat £2.00 Tracked UK** (£6.00 EU/ROW), published on `/delivery`. **No
  free-delivery threshold**, so there is no top-up play — unlike MMH's £40. One parcel,
  £2, done.
- 💰 **Expect retail pricing.** On the 2026-08-24 *Limit break upgrade* run it stocked only
  3 of 9 wanted printings and was dearer than Cardmarket on **every single one**:
  Blackblade Reforged £4.75 vs £1.75, Forge Anew £6.02 vs £3.95, Firion borderless £2.91
  vs £1.70. Its flat £2 parcel could not close a £6.28 card-price gap. **Its niche is a
  card the marketplaces don't have, or a list big enough that one £2 parcel beats several
  marketplace ones** — not per-card price.

Report Troll Trader as **coverage + one basket total**: how many of the N it stocks at the
wanted printing, the cards subtotal, and £2 postage.

## 3. Solve the cheapest cross-site split 🏆

**This is the headline answer and the plan that gets bought.** James: *"I don't
care about ordering from several places if it saves money."* So stop reporting
three isolated site totals and actually solve the combined buy.

### The solve

You now have, per card, the cheapest **available English** offer on each site
(Cardmarket per seller, Mage Cards per seller, MMH as one shop). Assign each card
to one source so **landed cost is minimised**:

1. **Start from the per-site cheapest plans** you already have — they're the
   baseline, and the split can never be worse than the best of them.
2. **Cost a parcel, not a card.** Adding a card to a basket you're already opening
   is free postage; opening a new seller costs that seller's postage (Cardmarket
   varies ≈£1.05–£4.25 but is typically **≈£1.17**, Mage Cards flat £1.11, Troll Trader
   flat £2.00, MMH £0 over £40 and unpublished below it). A card is only
   worth moving if `saving > postage of the parcel it opens`.
3. **Greedy then improve** — take the best single-site plan, then for each seller
   holding only one or two cheap cards ask whether dropping that seller and buying
   its cards from a basket you're already opening comes out cheaper. Single-card
   parcels are where the money leaks: a £1.00 card behind £4.07 of postage is a
   £5.07 card.
4. **Cheap cards should ride along, not travel alone.** Cards under ~£1 are almost
   never worth their own parcel — push them into whichever basket is already open,
   even at a slightly higher card price.
5. 🔁 **Consolidate onto shared sellers before you report anything** — see §3a.
   Never hand over a plan where every card came from a different seller until you
   have checked whether one seller stocks several of them.
6. **Report the split as a saving** against the best single-site total, and state
   the parcel count. `£X across N parcels — £Y cheaper than the best single-site
   plan (£Z, M parcels)`.

⚠️ **Never present a split total that omits a parcel's postage.** Every source in
the split contributes postage unless it's MMH over £40. Sum it explicitly and show
the arithmetic.

### 3a. 🔁 Seller consolidation — pay a little more per card to post far less

**Postage, not card price, is what makes a small order expensive.** On a real run
(Wishlist *Limit break upgrade*, 2026-08-24) taking each card from its own cheapest
Mage Cards seller gave **5 cards from 5 sellers: £12.17 of cards + £5.55 of postage
= £17.72**, i.e. **31% of the bill was envelopes**. Taking the per-card cheapest is
not the cheapest plan — it is usually the *most fragmented* one, and fragmentation
is the expensive part.

**So on every site with per-seller postage (Cardmarket, Mage Cards), after pricing
and before reporting:**

1. **Build a seller → cards-they-stock map** from the full roster. Any seller
   holding **two or more** of the wanted cards is a consolidation candidate, even
   if it is not cheapest on any single one of them.
2. **Cost the swap properly.** Moving a card onto a seller you are already using
   costs `their price − the cheapest price` and saves **that card's whole parcel**:

   ```
   premium = Σ(consolidated seller's price − cheapest price)  for each moved card
   saved   = (parcels before − parcels after) × postage
   consolidate when  premium < saved
   ```

3. **The rule of thumb:** with £1.11 flat postage, a card is worth moving to an
   existing basket if it costs **up to ~£1.10 more** there. With Cardmarket's
   ≈£1.17–£4.25 the headroom is bigger still — a £4.07 parcel justifies paying
   several pounds more per card to avoid opening it.
4. **Iterate until nothing improves**, then report both numbers so the trade is
   visible: `5 sellers £17.72 → 2 sellers £14.30 (cards £12.08 + £2.22), saving
   £3.42`. Show the premium paid as well as the postage saved.
5. ⚠️ **A one-card seller is a red flag, not a result.** Before shipping a plan,
   look at every seller supplying exactly one card and ask what it would cost to
   buy that card from a basket already open. If the answer is less than their
   postage, the plan was wrong. If the card genuinely exists nowhere else, say so
   explicitly — "only X stocks it, so this parcel is unavoidable".
6. **This applies within a site *and* across the split.** Consolidating five Mage
   Cards sellers to two, then checking whether those two are still worth opening at
   all once Cardmarket's basket is in play, are the same question asked twice.

### The Magic Madhouse £40 top-up 🎁

MMH postage is **free from £40**, so an MMH subtotal in the **£25–£40** band is a
trap worth converting: the postage saved plus the cards he wanted anyway can make a
bigger order *cheaper in total* than a smaller one. James: *"I always have extra
cards I want."*

**When the MMH share of the split lands between £25 and £40, do this:**

1. Work out the gap to £40 and what postage it saves (read the sub-£40 rate from
   the cart — don't guess).
2. **Draw top-up candidates from lists he already keeps**, never invented wants:
   - `03 - Personal/MTG/_To-Buy.md` — the general shopping list, first choice.
   - Any deck note's `🛒 Cards to Complete the Deck` buy list.
   - `- [ ]` unowned lines in the `_Collection - *` notes.
   Prefer cards that are **in stock at MMH**, **plain English printings**, and
   priced so the subtotal lands just over £40 rather than far past it.
3. **Propose the top-up, don't silently add it.** Show the candidates with prices
   and the before/after: `MMH £31.40 + £3.95 postage = £35.35 → add 3 cards
   (£9.10) = £40.50 delivered free, so £5.15 more cards for £5.15 less postage`.
   He chooses what goes in.
4. **If the gap can't be closed sensibly, say so** and take the postage. Never pad
   an order with cards he didn't ask for just to hit a threshold — that's spending
   more to "save" money unless the top-up cards are genuinely wanted.
5. Sanity check the direction: a top-up is only a win if
   `cost of added cards ≤ postage saved` **or** the added cards were going to be
   bought anyway. State which of the two applies.

## 4. Write one note per marketplace

Create `03 - Personal/MTG/_buying-test/` if it doesn't exist. Write **five files**
per run, same base name, source suffix appended — one per site plus the combined
plan:

- `<YYYY-MM-DD> <base list name> -card-market.md`
- `<YYYY-MM-DD> <base list name> -mage-cards.md`
- `<YYYY-MM-DD> <base list name> -magic-madhouse.md`
- `<YYYY-MM-DD> <base list name> -troll-trader.md`
- `<YYYY-MM-DD> <base list name> -SPLIT.md` ← **the combined cheapest plan, §3**

The **`-SPLIT.md` note is the deliverable**; the four per-site notes are its
evidence. Lead the split note with the grand total, the parcel count, the saving
against the best single-site plan, a **card → source** table for all N cards, and
the MMH £40 top-up proposal if one applies.

⚠️ If James asked for the breakdown somewhere specific instead (e.g. "put it in
`_To-Buy.md`"), **do that and skip the note pile** — one clear place beats five
files. Ask before writing four notes he didn't request.

Base name = the deck/file stem (deck mode) or `<collection short> <SET> <Section>`
(collection mode) — e.g. `2026-08-05 Final Fantasy FIN Regular -card-market.md`.
Use **today's date** (the real current date) as the prefix so runs sort and don't
clobber each other. If a same-named pair already exists for today, append ` (2)`
before the suffix.

Each note follows this shape (fill the marketplace name/postage per file):

```markdown
---
tags: [mtg, shopping, seller-coverage]
marketplace: Cardmarket        # or Mage Cards
mode: deck                     # or collection
source: <deck name | Collection · SET · Section>
cards-requested: <N>           # collection mode: unowned cards priced
run-date: <YYYY-MM-DD>
project: "[[02 - Projects/MTG Deck Importer/_Current State|MTG Deck Importer]]"
---

# 🛒 <list name> — <Marketplace> · fewest-seller plan

**For:** <[[the deck note or _Collection note this run priced]]> · **Project:** [[02 - Projects/MTG Deck Importer/_Current State|MTG Deck Importer]]

**<available>/<N> cards available** · **<S> sellers** · **cards £<X>** ·
**postage £<P>** · **grand total £<X+P>**

> Objective: fewest sellers covering the deck. See the sibling
> `-<other>.md` note for the other marketplace's plan on the same list.

## 📊 Summary

- **Found:** <available> of <N> cards, coverable by **<S> sellers**.
- **Grand total:** £<X+P> (cards £<X> + postage £<P>).
- **Top seller:** <name> stocks <k> of your cards on its own.
- **Missing (<u>):** <Card A>, <Card B>, … — or **none, full coverage ✅**.

List every missing card by name here, not just a count — the gaps are the
point of the summary.

## Sellers (fewest that cover the deck)

**Ordered by card count — the seller with the most of your cards first.** (Applies
to every per-site note: re-sort the Cardmarket wizard's per-seller breakdown by
count descending too. Magic Madhouse is a single block, so it has no ordering.)

### 1. <Seller name> — <k> cards · £<subtotal> + £<postage> postage
| Card | Condition | Price |
|---|---|---:|
| ... | NM | £0.00 |

### 2. <Seller name> — ...

## ❌ Unavailable here (<u> cards)
| Card | Cheapest seen |
|---|---:|
| ... | — |

## 🛒 Basket
**<Filled / not filled>** — <which plan went in, item count, total, and any
discrepancy against the table above>. Nothing was checked out.

## ⚠️ Non-plain / flagged picks
- <Card> — only stock was a **foil** at £X (plain not found)
- <Card> — only stock was **<language>**, excluded from totals and counted as
  unavailable (EN not found)

## 📋 Cards searched (copy-paste)
<A fenced code block listing EVERY card this run actually priced — one
`1 Card Name` per line. **Deck mode: names only.** **Collection mode: keep the
`(SET) number` id** (`1 Alien Symbiosis (SPM) 50`) so it re-imports as the exact
printing. If a value filter (`--under`/`--over`) narrowed the run, this list is
the FILTERED set — i.e. exactly what was looked for, nothing more. Ready to paste
into Moxfield / a testing site.>
```

**Always end each note with the `📋 Cards searched` block** — both files carry the
same list (it's what was searched, not what a given seller had). It's the whole
run made re-usable: paste it to rebuild the list, test a deck, or re-run later.

## 5. Fill the baskets 🛒

**This step is not optional and it is not `buy-deck`'s job any more.** James runs
this skill to decide a shop and then *do* it; a finished run whose basket is empty
means he types the whole order in again by hand. So end every run with the goods
sitting in baskets, ready for him to review and pay.

1. **Load the plan you actually recommended** — normally the **§3 cross-site
   split**, since that's the headline answer. **One plan only** — never load a
   site's own plan *and* its share of the split, or he buys duplicates.
2. **A split means filling more than one basket** — that's the point of it, and it
   is now expected rather than forbidden. Fill **each site's share of the chosen
   split, and nothing else**. Keep a running tally per site so you can verify each
   basket against its slice of the plan. If instead you recommended a single-site
   plan, only that site's basket gets filled.
3. **Cardmarket:** return to the kept results URL
   (`/Wants/ShoppingWizard/Results/<id>`) and click **ADD ALL TO CART**. ⚠️ **Buyers
   with fewer than six completed purchases cannot add from a wants list** — if
   that block appears, say so and fall back to per-seller adds from the product
   pages, or tell James it must be done by hand.
4. **Mage Cards:** on the MageFinder results page use **Add all to cart (£X)**, or
   the per-seller **Add to cart** buttons if only some sellers are in the plan.
5. **Magic Madhouse:** no bulk add — click **Add to Cart** on each chosen product
   tile, one card at a time. ⚠️ **Only in-stock tiles have the button**; an
   out-of-stock row can't be added, so if one sold out since pricing, say so rather
   than substituting silently. **Then check the cart against £40** — if the top-up
   (§3) was agreed, add those cards too and confirm delivery shows free.
6. **Verify, then report every basket.** Re-read each cart and confirm the **item
   count and total match that site's slice of the written plan**. State them all in
   chat with a combined grand total, and flag any discrepancy — stock moves between
   pricing and adding, so a card can vanish or change price in the gap.
7. 🛑 **Stop at the baskets.** Never proceed to checkout, never enter payment or
   address details, never accept terms. Hand him the loaded baskets and the totals.

If a run genuinely shouldn't end in a basket — a speculative price check, a
`--under 0.10` curiosity run — **say so and ask** rather than deciding silently.

## 6. Report back

Report to James in chat:
- **The cross-site split total and parcel count**, and what it saves against the
  best single-site plan — that's the headline.
- **A one-line head-to-head** of the three sites: who covers more, in fewer
  parcels, for less money.
- **The MMH £40 verdict** — cleared it, topped up to clear it, or took the postage.
- **Which baskets are now loaded**, with item count and total each, plus the
  combined figure. The baskets are what make the run actionable.

## Notes & guardrails

- **Coverage before cost.** If a source can't stock a card at all, that's a
  coverage fact — surface it; don't silently drop the card from the totals.
- 💸 **Money wins, parcels are a footnote.** James has said outright he'll take
  several parcels to save money, so the **cross-site split leads** and
  fewest-sellers is reported as the price of convenience, not as the answer. Do not
  quietly re-rank toward fewer parcels because it looks tidier.
- 🧾 **Every plan total is a landed cost.** Cards + every parcel's postage, arithmetic
  shown. A "cheapest" figure that hides postage is worse than useless — it's the
  error that made Mage Cards look like the winner at £25.48 when it was really
  ≈£48.79 over 21 parcels.
- 🎁 **Check the £40 Magic Madhouse threshold on every run** where MMH supplies
  anything. Between £25 and £40 it's usually worth topping up from `_To-Buy.md`;
  **propose, never pad silently**, and only when the added cards were wanted anyway
  or cost less than the postage saved.
- **Flag every non-plain pick** (foil, retro, Secret Lair) — in deck mode,
  printing-agnostic matching will grab an expensive alt-art rather than miss a
  card. One line per flag so he can veto.
- **Collection mode: verify the printing, don't just trust the name.** The
  section defines the treatment (Regular vs Full-art vs Showcase). Flag every pick
  whose set/collector number or version doesn't match the wanted printing, and
  every card where only the wrong treatment is stocked — a collector filling
  "Regular" does not want a full-art substitute.
- 🇬🇧 **English or it doesn't count.** A non-EN listing is not a cheaper copy of
  the card, it's a different card as far as James is concerned. Pin the language
  filter where the site has one (Cardmarket §2a step 3, Magic Madhouse's
  English/Foreign categories), read the `Lang.` column where it doesn't (Mage
  Cards), and never let a foreign-language row into a total. If English stock
  genuinely doesn't exist for a card, that's an **unavailable**, not a substitution.
- 🚫 **Out of stock is not a price.** Magic Madhouse lists out-of-stock printings
  with prices attached and they are not buyable. Quoting one is how a plan becomes
  fiction — check for `Add to Cart` before banking any MMH row.
- 🛒 **Fill the baskets, stop before checkout** (§5). Filling baskets used to be
  `buy-deck`'s job only — it isn't any more, because a plan he can't act on wastes
  the run. One plan; as many baskets as that plan needs; each verified against its
  slice of the written totals. Payment and checkout remain off-limits, always.
