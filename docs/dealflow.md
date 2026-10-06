# Demo 2: deal-flow triage

**Question:** can a decision model triage a venture fund's inbound email, so that a language model only writes
for the deals a partner should actually see?

A fund's inbox mixes real deals, out-of-thesis pitches, warm introductions, vendors and spam. Triage is a set of
small typed decisions per email: stage, sector, thesis fit, priority, next step. This demo has Jev answer all of
them for every email, then has Sonnet write a two-sentence partner note only for the emails Jev ranked high,
and reports the cost of both halves side by side.

## Data

50 synthetic inbound emails against a fixed fund thesis:

> Seed and Series A, B2B software only (fintech infrastructure, devtools, vertical SaaS). Europe and US. No
> consumer, no hardware, no pre-revenue climate. Ticket 1–4M EUR. We take warm intros from portfolio founders
> seriously.

**Who writes them.** The emails and their reference labels are written by OpenAI's `gpt-6.1-sol` at medium
reasoning effort (`dealflow generate`), not by Claude. The models being scored include Sonnet, and a model family grading
itself against labels it wrote would flatter itself, so the labels come from a different family. Each email
records the model id that was requested and the exact id the API reported.

**What they look like.** A fixed plan, drawn with a seed, gives every email a brief: its kind and its length.
The generator writes them in batches of ten, one email per brief, until there are exactly 50, and is told not to
reuse companies or senders from earlier batches.

| Kind | Emails | | Length | Emails |
|---|---:|---|---|---:|
| in-thesis cold pitch | 20 | | three-line intro | 12 |
| out of thesis | 10 | | one short paragraph | 14 |
| warm intro from a portfolio founder | 8 | | three or four paragraphs | 12 |
| not a deal (vendor, recruiter, spam, …) | 7 | | long founder email, six or more paragraphs | 10 |
| ambiguous (stage or sector borderline) | 5 | | forwarded thread (both are warm intros) | 2 |

**Reference labels.** For every email the generator first writes one line of reason per label
(`label_reasons`), then fills in the triage schema the models answer (`intended_label`) to match, as a careful
associate would. Reasons come first so the labels follow from them: when labels came first, some priority
numbers contradicted their own reasons. The brief
is kept with each email (`brief.kind`, `brief.length`), so results can be split by kind or length. The state
every backend sees is the thesis followed by the email.

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
| `sonnet` | `claude-sonnet-5-5` | off | triages every email, for comparison |
| `notes` | `claude-sonnet-5-5` | on | writes a partner note only where Jev's priority is "this week" or "today" |

Sonnet triages with thinking off (`sonnet_thinking=False` in `dealflow/act.py`), the same baseline as demo 3: it
gets the schema as a tool, exactly as Jev does. The partner notes are writing rather than classification, so
`dealflow notes` uses `sonnet_thinking` regardless. See
[How thinking changes the request](../README.md#how-thinking-changes-the-request) for what thinking changes.

## Running it

```
uv run dealflow generate                  # gpt-6.1-sol writes data/deals.jsonl, labels and reasons, in batches of 10
uv run dealflow triage --backends jev,sonnet
uv run dealflow notes                     # partner notes for Jev's high-priority emails only
uv run report dealflow
```

## What the report shows

- **Scoreboard:** agreement per field with the labels the OpenAI model wrote ("vs OpenAI-generated labels"),
  p50 and p95 latency, cost and errors for Jev and Sonnet.
- **Partner notes:** how many emails got one.
- **Cost per step:** generating the emails (a one-off), each triage run, the partner notes, and the pipeline as
  designed: Jev's triage plus the notes it asks for.

## Assumptions and caveats

- The intended labels are one model's careful reading, with reasons you can check, not ground truth. Writing
  them with a different model family removes the obvious self-agreement bias; it doesn't remove the generator's
  own taste.
- The generator knows the label when it writes the email, so the emails tend to state what the label needs.
  Real mail buries it.
- 50 emails is enough to show the shape of the pipeline, not to rank models on stage or sector accuracy.
- The thesis is fictional and simple on purpose; real theses have exceptions a literal reader will miss.
