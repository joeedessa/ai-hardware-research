# QA log — bugs, causes, guards

A running record of what has broken in this project, why, and what now stops it
happening again. Append to it; do not prune it. The value is not the list of
incidents — it is the **failure classes** in §2, which is where the same mistake
keeps arriving in a new costume.

Two rules for using this file:

- **When something breaks, add it to §4 and then ask which class in §2 it belongs
  to.** If it belongs to none, add a new class. A bug that fits an existing class
  means the guard for that class is missing or too weak.
- **When adding a guard, say where it lives** (CI, a code comment, a checklist
  item here). A guard nobody can find is a note, not a guard.

Last updated 2026-09-19.

---

## 1. Pre-flight checklist

Run the relevant block before committing. Most incidents below would have been
caught by one line of it.

**Any change to `index.html`**
- [ ] `node --check` on every `<script>` block — CI does this on push, but a
      syntax error means a *blank page with no console error*, so catching it
      locally is cheaper. See class A.
- [ ] Reload with a cache-busting query (`?cb=1`). The preview browser will
      happily serve a stale file and make a correct fix look broken. See class K.

**Any change to colours, themes or CSS**
- [ ] Check **both** themes, and check **foreground and background together** —
      the recurring shape is a token applied to one and not the other.
- [ ] For anything the browser renders itself (inputs, selects, scrollbars),
      confirm `color-scheme` is doing its job. See class H.

**Any change to an external-link mapping (Yahoo, TradingView, news)**
- [ ] Verify **every** new or changed symbol against a live HTTP status, not
      against inference. Exchange-code conventions are not guessable. See class B.

**Any change to `scripts/fetch_market.py`**
- [ ] Run a `light` pass and a `full` pass, then diff the record counts. Nothing
      should shrink on a light pass. See class E.
- [ ] Spot-check one derived percentage against the source venue's own figure.
      See class C.
- [ ] Check any date/session logic at the boundary — the exact open minute, the
      exact close minute, and the day rollover. See class D.

**Ingesting a new source memo**
- [ ] Grep the universe for the same author's earlier source ids and re-read every
      record citing them — the new piece may reverse an attribution. See class N.
- [ ] Pull embedded images from the PDF; tables and baskets are often images. See K.

**Adding or editing a company record**
- [ ] Confirm the ticker resolves to the **intended entity** — check market cap
      and business description, not just that the symbol returns data. See class J.
- [ ] Confirm price history exists for the symbol we store.
- [ ] Confirm the chart link returns 200.
- [ ] If the record's conviction or froth tag is set after the relevant forward
      track's inception, set `conv_since` / `froth_since` to today. See class M.
- [ ] Confirm any generated text respects the record's own conviction tier — do
      not let a template assert a claim the data does not make. See class I.

---

## 2. Failure classes

### A. Silent failure in a no-build single-file app
There is no bundler and no type checker. A syntax error anywhere in a `<script>`
block kills the whole block, and the page renders as if nothing happened — no
console error, no stack trace, just missing UI.

*Instances:* an escaped apostrophe (`hasn\'t`) inside a JS string took the entire
app down silently.

*Guard:* `.github/workflows/ci.yml` extracts every script block and runs
`node --check` on push. **This guard exists because of that one bug** — keep it.

### B. Convention mismatch at a system boundary
Every external service has its own symbol namespace. Ours is not theirs, and the
differences are arbitrary — they cannot be derived, only looked up.

*Instances:* 20 broken Yahoo links (`.SH`→`.SS`, Hong Kong four-digit padding,
TPEx specials). Three broken TradingView prefixes shipped on inference —
`.OL`→OMXOSE (correct: `OSL`), `.VI`→WBAG (correct: `VIE`), `.KQ`→KOSDAQ
(correct: `KRX`; Korea Exchange runs both boards under one prefix).

*Lesson:* a mapping that looks obvious is exactly the one that is wrong, because
nobody checks the obvious ones. The TradingView errors sat in production
unnoticed until an unrelated lookup exposed them.

A second shape of the same class: **a correction table wired into one consumer and
not the others.** `SYM_FIX` (formerly `YH_SPECIAL`) records that Phison and Auras
trade on TPEx rather than TWSE and that Schaeffler's Xetra line is `SHA0` — facts
about the securities, not about Yahoo. It was consulted by `yahooSymbol()` only, so
prices were correct while three TradingView chart links 404'd. Renamed and applied
in both paths.

*Guard:* `scripts/check_links.py`, run weekly and on any change to `index.html`,
`data/companies.json` or the checker itself, via `.github/workflows/check-links.yml`.
It **parses** `SYM_FIX` and `TV_EXCH` out of `index.html` rather than reimplementing
them — see class L for why that matters.

### C. Unit and derivation errors in market data
Repairing data with a magnitude heuristic destroys the genuine small values.

*Instance:* extended-hours moves were "fixed" with a rule that multiplied by 100
when the value looked too small. It inflated real moves 100× — Akamai's true
+0.86% was published as +85.88%. Thirteen values were corrupted.

*Lesson:* never infer a unit from magnitude. Derive from the primary values —
here, recompute the percentage from the prices themselves. The corrected version
was validated against a known-good external figure (Onto 268.70→303.60 =
+12.99%, matching the venue's 12.988461).

*Guard:* pre/post moves derive from `postMarketPrice`/`regularMarketPrice` and
`preMarketPrice`/`regularMarketPreviousClose`. Spot-check one value per change.

### D. Time, sessions and "latest" ≠ "today"
This universe spans ~20 exchanges. Every date assumption has a counterexample
somewhere in it.

*Instances:* intraday passes reported the previous day's move, because the latest
daily bar is not today's bar (fixed with live `lp`/`ld1` fields). Releases timed
exactly at the bell — Tokyo 15:30, Frankfurt 17:30, Taipei 13:30 — were
classified as mid-session, because the boundary comparison was exclusive (fixed
to `mins <= open` / `mins >= close`). A May earnings print attached itself to an
August calendar event, because nothing required the result date to be near the
event date (fixed with a ±1 day gate).

*Lesson:* test the boundary minute, not the middle of the session. Test the day
rollover, not the middle of the week.

### E. Partial passes that destroy data
A pass that skips work must **carry forward**, never write empty. "I did not
look" and "there is nothing" are different states and must not share a
representation.

*Instance:* the light refresh pass wrote an empty list for the long-form voices
it deliberately skips, blanking the section until the next full pass.

*Guard:* light passes carry previous values forward. **Gap:** no automated
assertion that a light pass never shrinks a collection — see §5.

### F. Gating on the slower of two sources
When either of two sources can satisfy a trigger, the condition must be an OR.

*Instance:* earnings flash cards were gated on the arrival of structured
figures, so the wire headline — which lands materially earlier — was invisible
even though it carried enough to be useful. Fixed to trigger on `rep || wire`.

### G. Staleness windows that turn a signal surface into wallpaper
An "act now" page with no age decay stops being read.

*Instance:* a CoreWeave CDS headline sat on the breaking page for nine days
because the critical-signal window was eight days. The user noticed, not us:
*"nothing has changed in a few days."*

*Guard:* window cut to five days, plus first-seen date tracking, an age display,
and a "new today" badge so carried-over items are visibly distinct from new ones.

*Lesson:* any surface claiming urgency needs the age visible on its face. If a
user has to ask whether it updated, it has already failed.

### H. Theming by omission
The recurring shape: a design token applied to the foreground and not the
background, or applied to elements we draw but not to elements the browser draws.

*Instances:* dark-mode contrast failures on the Froth Monitor and the demand-theme
cards (colour on colour). Search inputs in Companies and Glossary set
`color: var(--color-text-primary)` but no background, so they fell through to the
user agent's white field default — near-white text on white, invisible.

*Guard:* `color-scheme` declared on `:root` for both the `data-theme` override and
the `prefers-color-scheme` branch, so browser-rendered controls follow the theme.
Explicit background/colour/placeholder/focus tokens on `input`, `textarea` and
`select`, so a control added later cannot inherit the bug by omission.

**Gap:** no automated contrast check across both themes — see §5.

### I. Generated text asserting more than the record supports
Templates that interpolate a company's data can state claims the data never made.

*Instances:* the Ask-Claude research brief asked "is the chokepoint claim true?"
for a conviction-1 name we explicitly do *not* call a chokepoint. Peer
comparisons ranked by conviction rather than by chain-layer comparability, so a
name was compared against companies from an entirely different layer while its
true comparable was cut.

*Lesson:* a template must branch on the record's own tier. The conviction scale
means something (1=monitor, 2=interested, 3=chokepoint) and generated prose has
to honour it.

### J. Identity errors — right symbol, wrong entity
A ticker resolving is not an identity confirmed.

*Instances:* `CAPS` was taken to be Capstone Energy; it is Capstone Holding Corp,
a ~$4M building-products company. "Smart Optics", described as Swedish and
recently US-listed, is Smartoptics Group ASA — **Norwegian**, Oslo-listed, and its
US line (`SMOPF`, OTC Pink) is an unsponsored cross-quote trading ~4k shares a day
against 170k in Oslo.

A listing can also go stale after verification. Capstone Energy+ was checked for
*entity* on 2026-08-08 and stored as OTC `CGEH` — but it had uplisted to Nasdaq as
`CEPL` on 2026-07-08. Yahoo kept resolving the dead symbol for a while and then
stopped, after which the pipeline carried forward $9.85 while CEPL traded near $6.69.
Doosan Tesna had the same failure from the Korean side (`131970.KS` for a KOSDAQ name).
The pipeline's "N misses" line had been reporting both.

A third instance, 2026-09-19: Citrini writes Robotis as `108490 KS`. Bloomberg's
`KS` covers **both** Korean boards; Robotis is on KOSDAQ. Yahoo's `.KS` alias for it
returned a quote of 22,400 while the real KOSDAQ line (`.KQ`) was at 306,500 — the chart
endpoint and the quote endpoint even disagreed with each other. Stored as `.KQ`.

*Lesson:* verify entity and listing **separately**. Confirm the business
description and market cap match the intended company, then confirm which line
actually has depth. A name match is not an identity match, and a quote existing
is not a listing.

### L. Verification harnesses that reimplement production logic
A checker that re-derives what the app does will drift from it, and then it lies —
in both directions. False negatives hide real bugs; false positives waste a
diagnosis and can trigger a "fix" to something that was never broken.

*Instance:* a throwaway script written to enumerate TradingView URLs omitted the
Hong Kong zero-stripping rule that `tvSymbol()` already had. It reported SMIC's
chart link as 404. The app was correct; the checker was wrong. Two minutes were
spent diagnosing a bug that did not exist.

*Guard:* `scripts/check_links.py` parses `SYM_FIX` and `TV_EXCH` out of
`index.html` and fails loudly if it cannot find them, so a rename breaks the
checker instead of silently degrading it.

A second instance, 2026-09-19: while quantifying class M, I compared the corrected
track against a "naive" baseline I had rebuilt myself. It included two names the live
dashboard had never scored, and it made the froth lens appear to flip from right to
wrong. It had not — against the real published figures the lens was already behind.
The comparison baseline must be **what users actually saw** (the committed
`performance.json`), not a reconstruction of it.

*Lesson:* a verification tool must **read** the real mapping, not restate it. If
that is impossible, the tool's disagreement with production is a finding about the
tool until proven otherwise.

### M. Forward records scored with hindsight
A "since inception" track computed from **today's** members lets every later addition
rewrite history. A name tagged after it moved gets scored across a window in which we
held no view of it — in either direction.

*Instances:* the froth tracks start 2026-08-01, but only 18 names carried a froth 1
or 3 tag that day; 52 more were tagged on 2026-08-05 and 2 on 2026-08-08, and all were
scored from 08-01. The conviction-3 track starts 2026-05-30, but Delta (2308.TW)
earned its 3 on 2026-08-03 and was credited with its −34.7% fall from May. Found on
2026-09-19 while adding Unitree, which listed after the froth inception and would
have booked its post-IPO slide.

*Effect, production vs production:* insulated +2.3% → −1.6%; high froth +3.1% →
−0.1%; the **spread** that actually tests the lens went from −0.8 to −1.5 points
against it. Conviction-3 went from −12.2% to −10.6%. Hindsight had been inflating the
froth tracks by 2–4 points and hiding about half the lens's shortfall.

*Guard:* records carry `conv_since` / `froth_since` (backfilled from git history for
all 55 affected records); `si()` in `scripts/fetch_market.py` scores each name from
`max(inception, own tag date)`. The track notes now say so.

*Still open — exits:* a name that LOSES its tag drops out of the track entirely,
taking its membership-period return with it. Micron was conviction-3 from 05-30 to
08-03 and fell −19.9% in that time; the corrected −10.6% omits it and is flattered by
it. Fixing that needs a membership ledger with entry and exit dates — see §5.

*Lesson:* any record that claims to test a judgment must know when each judgment was
made, per item, and must keep the ones it later abandoned.

### N. Stale attribution to a source that has moved
A record that says "Source X's clearest play" keeps saying it after Source X changes
its mind. The claim is still quoted accurately; it is just no longer true.

*Instance:* Schaeffler's record described it as "Citrini's clearest non-China humanoid
design-win play" (May 2025). Citrini's September 2026 field trip used Schaeffler as the
**counter-example** to that thesis. Our July Optimus notes had the same problem from the
other side: "ramping at Fremont" when Tesla's own Q2 update said the lines were still
being installed.

*Guard:* when a source is ingested, grep the universe for its earlier source ids
(`citrini-*`) and re-read every record that cites them. Attributions carry dates in
the prose ("May 2025, Citrini called it…") so a reversal reads as history rather than a
contradiction.

### K. Tooling and process friction
Not product bugs, but they cost real time and produce false signals.

- **Stale browser cache during verification.** The preview served an old
  `index.html` and made a correct fix look like a failure. Always reload with a
  cache-busting query before concluding a change did not work.
- **Scratchpad virtualenv does not persist** between sessions. Rebuild it; do not
  assume the interpreter path from earlier in a conversation still exists.
- **The preview server lost read access to `~/Desktop`** (a macOS privacy permission,
  not the launch config): Python's `http.server` calls `getcwd()` before it reads
  `--directory`, so it dies at import. Workaround: mirror `index.html`, `icon.svg`
  and `data/` to the scratchpad and serve that on a temporary port. Do not commit the
  temporary launch entry.
- **`timeout` is not on macOS.** A backgrounded `timeout 900 …` exits 0 having run
  nothing — read the output, not the exit code.
- **Paid-newsletter PDFs carry the payload as images.** The Citrini robotics piece
  extracts 94k characters of prose, but the basket — tickers and weights, the most
  actionable page — is a PNG. `pypdf`'s `page.images` pulls it out for reading.
- **Scanned PDF appendices have no text layer.** `pypdf` returns empty strings for
  them and gives no error. FCC DA 26-786's Appendices B and C — which carry the
  actual definitions — are images; the definitional language had to come from the
  agency FAQ and law-firm alerts instead.
- **Rebase conflicts with the market-data bot** on `data/*.json` are routine, since
  it commits on four schedules a day. Resolve with
  `git checkout --theirs data/*.json` and re-run the edit if needed.

---

## 3. Guards currently in place

| Guard | Where | Class it defends |
|---|---|---|
| `node --check` on every extracted script block | `.github/workflows/ci.yml` | A |
| `json.load` on every `data/*.json` | `.github/workflows/ci.yml` and the refresh workflow | data corruption |
| Derived (not heuristic) extended-hours percentages | `scripts/fetch_market.py` | C |
| Live `lp`/`ld1` fields separate from daily bars | `scripts/fetch_market.py` | D |
| Inclusive session-boundary comparisons | `scripts/fetch_market.py` | D |
| ±1 day gate on earnings-result-to-event matching | `scripts/fetch_market.py` | D |
| Light passes carry previous values forward | `scripts/fetch_market.py` | E |
| Flash-card trigger on `rep \|\| wire` | `scripts/fetch_market.py` | F |
| 5-day signal window + first-seen dates + age badges | `scripts/fetch_market.py`, breaking page | G |
| `color-scheme` on `:root` in both theme branches | `index.html` | H |
| Explicit form-control theming rules | `index.html` | H |
| Conviction-tier branching in the research-brief template | `index.html` | I |
| Weekly external chart-link check over the whole universe | `scripts/check_links.py`, `.github/workflows/check-links.yml` | B |
| Checker parses mappings from `index.html` rather than restating them | `scripts/check_links.py` | L |
| Per-record tag dates honoured by the forward tracks | `scripts/fetch_market.py` `si()`, `conv_since`/`froth_since` | M |
| `archive/` dated snapshots as revert points | repo | all |

---

## 4. Chronological log

Newest first. Format: date — symptom — root cause — fix.

**2026-09-19** — Froth tracks and the conviction-3 track credited names with moves made
before they were tagged. — Tracks scored today's members from inception. — Tag dates
backfilled from git for 55 records and honoured by `si()`; exits still open. Class M.

**2026-09-19** — Capstone Energy+ priced at a stale $9.85 (actual ~$6.69); Doosan
Tesna's price frozen. — `CGEH` died on the 2026-07-08 Nasdaq uplisting to `CEPL`;
Doosan Tesna stored as `.KS` on KOSDAQ. Both were in the pipeline's miss list and
nobody read it. — Renamed to `CEPL` and `131970.KQ`; quotes now 241/241. Class J.

**2026-09-19** — `fcc-da-26-786` shown as a raw id in eight company drawers. — Source
cited in August, never registered. — Registered in `sources.json`. Class N-adjacent.

**2026-09-19** — Adding Unitree would have flattered the froth track. — Forward tracks
scored today's members from inception; a post-inception listing fell back to its first
bar. — Per-record `conv_since`/`froth_since`, honoured by `si()`. Caught before shipping.
Class M.

**2026-09-19** — Schaeffler still credited as Citrini's top design-win pick after
Citrini reversed; Optimus described as "ramping" when Tesla said "installing". — Dated
claims read as current. — Both rewritten as dated history; Schaeffler, Sanhua and Tuopu
re-rated 2→1. Class N.

**2026-09-19** — Robotis given as `108490 KS` by the source. — Bloomberg's code spans
both Korean boards; Yahoo's `.KS` alias returned a wrong quote. — Stored as `108490.KQ`.
Class J.

**2026-08-09** — Three TradingView chart links 404 (Phison `8299.TW`, Auras
`3324.TW`, Schaeffler `SHA.DE`). — The `YH_SPECIAL` table encodes real exchange
corrections (Phison and Auras are TPEx not TWSE; Schaeffler's Xetra line is
`SHA0`) but was consulted only by `yahooSymbol()`. Prices were right, charts were
not. — Renamed to `SYM_FIX`, applied in `tvSymbol()` too, and `scripts/check_links.py`
built so the class is caught automatically. Found by the first run of that checker.
Class B.

**2026-08-09** — Checker reported SMIC's TradingView link as 404. — False positive:
the throwaway enumeration script did not replicate the `.HK` zero-stripping rule the
app already had. — Checker rewritten to parse the real mappings out of `index.html`.
Class L.

**2026-08-09** — Search text invisible in Companies and Glossary. — Inputs set a
themed `color` but no background, falling through to the UA's white field
default. — `color-scheme` on `:root` in both theme branches plus explicit
form-control tokens. Class H.

**2026-08-09** — "Smart Optics" described as Swedish and US-listed. — Actually
Smartoptics Group ASA, Norwegian, Oslo. The US line is a thin unsponsored OTC
cross-quote, not a listing. — Added as `SMOP.OL` with the OTC caveat recorded in
the froth note. Class J.

**2026-08-08** — Three TradingView chart links 404 (Nordic Semiconductor, AT&S,
Rainbow Robotics). — Exchange prefixes shipped on inference, never verified.
`.OL`, `.VI`, `.KQ` all wrong. — Corrected to `OSL`, `VIE`, `KRX`; every prefix
in use re-verified against a live page. Class B.

**2026-08-08** — Wrong entity behind `CAPS` in an investor inventory. — Ticker
resolved, identity never confirmed. — Corrected to Capstone Holding Corp; entity
verification added to the record checklist. Class J.

**~2026-08-07** — Breaking page unchanged for days; user asked whether it was
updating. — Critical-signal window was eight days, so a single headline
persisted. — Window cut to five days; first-seen tracking, age display and "new
today" badges added. Class G.

**~2026-08-06** — Extended-hours moves wrong; user reported "incorrect after
hour moves". — A ×100 heuristic applied when values "looked too small" inflated
genuine small moves 100× (AKAM +0.86% → +85.88%). — Derive from prices instead;
13 corrupted values purged; validated against a known-good external figure.
Class C.

**~2026-08-06** — Earnings shown for the wrong session; bell-time releases
misclassified. — Session-boundary comparisons were exclusive, so 15:30 Tokyo /
17:30 Frankfurt / 13:30 Taipei landed "mid-session". — Inclusive comparisons.
Class D.

**~2026-08-06** — A May earnings print displayed beside an August calendar
event. — Nothing required the reported date to be near the event date. — ±1 day
gate. Class D.

**~2026-08-06** — Intraday passes reported the previous day's move. — The latest
daily bar is not today's bar. — Added live `lp`/`ld1` fields. Class D.

**~2026-08-05** — Long-form voices section blank after a light pass. — The light
pass wrote an empty list for work it deliberately skips. — Carry previous values
forward. Class E.

**~2026-08-05** — Earnings flash cards appearing late. — Gated on structured
figures only, so the faster wire headline was invisible. — Trigger on
`rep || wire`. Class F.

**~2026-08-04** — Research brief asked whether a "chokepoint claim" held for a
conviction-1 name we never called a chokepoint. — Template did not branch on
tier. — Question now adapts to the conviction tier. Class I.

**~2026-08-04** — Peer comparisons pulled names from unrelated chain layers while
the true comparable was omitted. — Peers ranked by conviction rather than
comparability. — Split into "direct comparables (same chain layer)" and "loosely
comparable". Class I.

**~2026-08-03** — 20 Yahoo chart links broken. — The widget used our internal
ticker convention rather than Yahoo's. — Ported `yahoo_symbol()` into the app.
Class B.

**~2026-08-02** — Dark-mode contrast failures on the Froth Monitor and demand-theme
cards. — Colour on colour; tokens applied inconsistently. — Corrected; `--lift`
luminance token introduced. Class H.

**~2026-08-01** — Entire app silently blank. — An escaped apostrophe inside a JS
string (`hasn\'t`) broke the script block; no console error. — Rewritten without
the apostrophe, and **the CI syntax check was created in response**. Class A.

**Ticker linkification** — tickers at the end of a sentence failed to link. — The
regex lookahead `(?![\w.-])` rejected a following period, which is also a sentence
terminator. — Changed to `(?![\w-])(?!\.\w)`. Class B-adjacent (pattern that looks
obviously right).

---

## 5. Open gaps

Guards that should exist and do not. Each maps to a class that has already bitten
more than once.

1. ~~**Automated external-link checker** (class B).~~ **Closed 2026-08-09** —
   `scripts/check_links.py` + `.github/workflows/check-links.yml`. It found three
   live 404s within minutes of being written.
2. **Non-shrinking assertion on light passes** (class E). Nothing fails the build
   if a light pass writes fewer records than the previous run. The voices
   incident is repeatable for any other collection.
3. **Contrast check across both themes** (class H). Two separate dark-mode
   contrast failures have shipped. A scripted pass over the token pairs, or a
   render-and-sample check on key surfaces, would close it.
4. **Membership ledger for forward tracks** (class M). Tag dates now handle entries;
   exits still vanish with their history (Micron, −19.9% while conviction-3).
5. **Read the pipeline's miss list** (class J). The "N misses" line named `CGEH` and
   `131970.KS` for weeks. It should fail loudly — or open an alert — when a universe
   ticker stops resolving, instead of silently carrying the last price forward.
6. **Entity verification on record creation** (class J). Currently a checklist
   item here rather than anything enforced. At minimum, storing the market cap
   and a one-line business description at creation time would make a mismatch
   visible later.

---

## 6. Standing observations

- **The user has found several of these before the tooling did** — the stale
  breaking page, the extended-hours figures, the invisible search text. That is a
  reliable signal that the gap is in *automated verification of what is
  displayed*, not in the data pipeline, which is comparatively well guarded.
- **The dangerous bugs here are quiet.** Nothing on this list threw an exception.
  A broken chart link, a stale signal, a 100× percentage and an invisible input
  all render as a working page. Assume that anything not explicitly checked is
  unverified.
- **Inference is the recurring root cause.** Exchange prefixes, unit magnitudes,
  entity identity, session boundaries — every one of these was a case of a
  reasonable assumption substituted for a lookup. When the cost of checking is one
  HTTP request or one query, check.
