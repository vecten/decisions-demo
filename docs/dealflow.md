# Demo 2: deal-flow triage

**Question:** can a decision model triage a venture fund's inbound email, so that a language model only writes
for the deals a partner should actually see?

A fund's inbox mixes real deals, out-of-thesis pitches, warm introductions, vendors and spam. Triage is a set of
small typed decisions per email: stage, sector, thesis fit, priority, next step. This demo has Jev answer all of
them for every email, then has Sonnet write a two-sentence partner note only for the emails Jev ranked high,
and reports the cost of both halves side by side.

## Data

Synthetic inbound emails, written by Sonnet in one call (`dealflow generate`) against a fixed fund thesis:

> Seed and Series A, B2B software only (fintech infrastructure, devtools, vertical SaaS). Europe and US. No
> consumer, no hardware, no pre-revenue climate. Ticket 1–4M EUR. We take warm intros from portfolio founders
> seriously.

The committed set has 49 emails: Sonnet was asked for 50, and the count isn't enforced.

The requested mix is roughly 40% in-thesis deals, 20% out-of-thesis (consumer, hardware, growth/PE), 15% warm
intros from portfolio founders, 15% not deals (vendors, recruiters, spam, conference invites) and 10%
deliberately ambiguous. The generator also writes an `intended_label` for each email in the same schema the
models answer, which serves as the reference. The state every backend sees is the thesis followed by the email.

## The question

```python
class DealTriage(BaseModel):
    """Triage an inbound email for a venture fund, given the fund thesis in the context."""
    stage: Literal['pre_seed', 'seed', 'series_a', 'series_b_plus', 'growth_pe', 'not_a_deal']
    sector: Literal['fintech', 'devtools', 'healthtech', 'climate', 'consumer', 'b2b_saas', 'other']
    fits_thesis: bool
    warm_intro: bool
    priority: Priority       # 0 ignore, 1 later, 2 this week, 3 today
    next_action: Literal['pass', 'request_deck', 'intro_call', 'forward_to_partner']
```

Each field's docstring in `dealflow/schemas.py` is the question asked. `Priority` is an ordered scale: each level
carries its meaning in the JSON schema (`PRIORITY_LEVELS`), so Jev treats it as a rubric and Claude knows which
direction is urgent.

## Runs

| Run | Model | Thinking | Why |
|---|---|---|---|
| `jev` | `typesafe:jev-latest` | none | triages every email |
| `sonnet` | `claude-sonnet-5-5` | on | triages every email, for comparison |
| `notes` | `claude-sonnet-5-5` | on | writes a partner note only where Jev's priority is "this week" or "today" |

Sonnet thinks in both of its roles (`sonnet_thinking=True` in `dealflow/act.py`). See
[How thinking changes the request](../README.md#how-thinking-changes-the-request) for what that changes.

## Running it

```
uv run dealflow generate                  # Sonnet writes data/deals.jsonl with intended labels
uv run dealflow triage --backends jev,sonnet
uv run dealflow notes                     # partner notes for Jev's high-priority emails only
uv run report dealflow
```

## What the report shows

- **Scoreboard:** agreement with the intended labels per field, p50 and p95 latency, cost and errors for Jev
  and Sonnet.
- **Partner notes:** how many notes were written and what that half cost.

## Assumptions and caveats

- The emails and their labels come from the same model family as one of the backends under test. The intended
  labels are the generator's intent, not an independent judgment, and Sonnet may be flattered by them.
- 49 emails is enough to show the shape of the pipeline, not to rank models on stage or sector accuracy.
- The emails are short (150 to 360 characters): written 50 to a reply, Sonnet kept them brief despite being
  asked to vary length. Real inbound mail is longer and messier, which makes triage harder.
- The thesis is fictional and simple on purpose; real theses have exceptions a literal reader will miss.
