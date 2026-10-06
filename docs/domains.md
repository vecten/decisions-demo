# Demo 3: investing domains

**Question:** can a decision model classify real companies into a fund's two-level taxonomy, zero-shot, from
the fund's own definitions, and how should the question be asked?

Funds tag every company they look at with the sectors they invest in, usually by hand and inconsistently. This
demo classifies 500 real Y Combinator companies into domain → subindustry with Jev and with Claude Sonnet,
compares four ways of asking Jev the same thing, routes the companies Jev is unsure about to Sonnet, and checks
how well Jev's probabilities are calibrated.

## Data

**Companies.** `domains fetch` samples from the [yc-oss](https://github.com/yc-oss/api) mirror of YC's public
company directory, an unofficial mirror that declares no licence:

- active companies from batches 2019 onwards with a description of at least 200 characters;
- 500 in total: 30 labelled Other and the rest spread as evenly as their pools allow across YC's six domains
  (Real Estate and Construction has only 47 eligible companies, so it contributes all of them and the others 84
  or 85);
- within each domain, subindustries in proportion to their pool, with a floor of 5 where the pool allows;
- one company, Roofr, kept in regardless of batch because it is a clear example of a disputable YC label;
- a fixed seed. The mirror changes daily, so the saved sample, not a re-fetch, is the fixed dataset.

The state every backend sees is the company's name, one-liner and long description, nothing YC added.

**Reference labels.** YC's own `industry → subindustry`, for example `B2B → Marketing`. YC's Education and
Government become Other, which has no second level. Companies YC files under a domain with no subindustry, or
as Unspecified, are left out. YC's labels are noisy: plenty of companies fit two domains, and some are filed
oddly. So the report scores agreement with YC, and accuracy against an adjudicated reference (below).

**Taxonomy and definitions.** `data/domain_definitions.json` holds one line per domain (six plus Other) and per
subindustry (50). Every option description and every Noul criterion is built from that file, so it is where a
fund would put its own definitions. The domain lines were written by hand. `domains define` had Sonnet write the
subindustry lines once, from five one-liners per subindustry of companies outside the sample. Edit the file
freely; `define` won't overwrite it without `--force`.

## The questions

Four shapes on Jev, all built from the same definitions (`domains/schemas.py`):

| Run | Shape |
|---|---|
| `jev_flat` | one Choice over all 50 subindustries plus Other; the domain is the subindustry's parent |
| `jev_sequential` | a domain Choice, then a Choice over the winning domain's subindustries: two calls |
| `jev_fanout` | the domain Choice plus all six per-domain subindustry Choices in one call; the subindustry is read from the winning domain |
| `jev_nouls` | six independent yes/no questions, "does this company operate in X?"; the multi-label footprint. Other has no Noul: it is every Noul being low |

Sonnet answers one nested structured output: a domain plus a subindustry valid for that domain, so an invalid
pair such as Fintech → Gaming can't be expressed.

Options are built at run time from the definitions file, so they can't be a Python `Enum` with member
docstrings. `schemas.options()` produces the same JSON shape (each option a `const` with its description) and
adds a `type` to each option, which Claude's strict mode requires.

## Runs

| Run | Model | Thinking | Companies |
|---|---|---|---|
| `jev_flat`, `jev_sequential`, `jev_fanout`, `jev_nouls` | `typesafe:jev-latest` | none | 500 |
| `…_rerun` of the four above | `typesafe:jev-latest` | none | 500, to measure run-to-run stability |
| `sonnet` | `claude-sonnet-5-5` | off | 500 |
| `sonnet_thinking` | `claude-sonnet-5-5` | on | the first 100 (the sample is shuffled) |
| adjudication | `claude-opus-5-5` | on, effort high | every disputed company |

Sonnet runs with thinking off here (`sonnet_thinking=False` in `domains/act.py`) so it receives the schema
exactly as Jev does, as a tool listing the same options and definitions, and so that the long schema is
prompt-cached across 500 calls. `sonnet_thinking` measures what thinking adds on a subset. See
[How thinking changes the request](../README.md#how-thinking-changes-the-request) for the mechanics, including
the short visible rationale Sonnet 5.5 writes when thinking is off.

## Adjudication

A company is disputed when any of the main runs (`jev_flat`, `jev_sequential`, `jev_fanout`, `sonnet`,
`sonnet_thinking`) disagrees with YC on domain and subindustry. `domains adjudicate` has Opus label every
disputed company **blind**: it sees the company text, not YC's label or any run's pick, and returns a primary
pair, an optional secondary pair if the company genuinely operates in two, and a one-sentence explanation. The
result is `data/domain_adjudicated.jsonl`. Hand-correct rows and set `"corrected": true`; a re-run keeps them.

The **reference** in the report is the adjudicated primary label where one exists and YC's label elsewhere
(every company that wasn't adjudicated had all main runs agreeing with YC). Opus is Claude, so Sonnet is likely
flattered against this reference: read it as Claude's careful reading, not ground truth.

## Running it

```
uv run domains fetch                       # sample -> data/companies.jsonl, and a cost estimate per run
uv run domains define                      # subindustry definitions, once -> data/domain_definitions.json
uv run domains classify                    # all runs; --runs to pick, --limit N for a quick check
uv run domains classify --runs jev_flat,jev_sequential,jev_fanout,jev_nouls --tag rerun
uv run domains classify --runs sonnet --retry-errors   # re-ask only rows that failed (overloaded, timeouts)
uv run domains adjudicate --dry-run        # how many companies are disputed, and the Opus cost
uv run domains adjudicate
uv run report domains
```

`fetch` prints an estimated cost for every run before you spend anything, and `adjudicate --dry-run` does the
same for Opus.

## What the report shows

- **Scoreboards:** per run, agreement with YC and accuracy against the reference, at level 1 (domain) and
  level 2 (subindustry), with latency, cost and errors; then level 2 given that level 1 is right.
- **Breakdown:** level-2 accuracy against the reference per domain, and per subindustry where the reference has
  at least 10 companies in it.
- **Routing:** Jev flat answers, Sonnet takes the companies where Jev's top probability is under 0.6 or its
  margin over the runner-up is under 0.2; a table of other thresholds, and the best rule for a given share of
  companies sent to Sonnet. Then the routed companies, with Jev's top options next to Sonnet's pick and
  rationale.
- **Confusion:** the most frequent disagreements with the reference, and a domain confusion matrix.
- **Shapes:** how often flat, sequential and fan-out agree with each other on the same company.
- **Calibration:** Jev's accuracy per 0.1 bucket of its top probability, at both levels, and the expected
  calibration error.
- **Nouls:** how often the B2B Noul fires per YC domain; precision and recall of the five sector Nouls against
  YC and against the reference (which adds Opus's secondary domain where there is one); mean per-company
  Jaccard; and how many companies have two or more sector Nouls, or none.
- **Duality:** a 2×2 of "two or more sector Nouls fire" against "the Choice is uncertain" (the routing rule).
  Uncertain and dual is a company that is two things; uncertain with at most one Noul is model confusion. Both
  off-diagonal groups are listed.
- **Stability:** for each Jev run against its rerun, the share of identical outputs, the share of changed
  labels, and the mean and maximum change in confidence.
- **Latency:** p50 and p95 per company as measured, with the number of requests in flight and calls per
  company, plus cost per 1,000 companies.
- **Heatmap:** every company by the six Nouls, rows grouped by YC label, saved to
  `data/figures/domains_nouls_heatmap_{light,dark}.png`.

## Assumptions and caveats

- YC's labels are one editor's choice per company, not a taxonomy anyone audited. Disagreeing with them is often
  right, which is why adjudication exists.
- The B2B Noul asks whether a company sells to businesses, which most fintech, health and industrial companies
  do. YC's B2B is a residual label: business software no sector label covers. The report therefore shows B2B as
  a fire rate and keeps it out of precision, recall, Jaccard and duality, which use the five sector Nouls.
- Many subindustries have fewer than 10 companies in the sample; per-subindustry numbers are shown only above
  that.
- Latency is measured with many requests in flight (32 for Jev, 6 for Sonnet). A single request is faster;
  the report states the concurrency next to each number.
- The subindustry definitions were written by Sonnet and then reviewed. Editing them changes every run's input,
  so rerun after editing.
