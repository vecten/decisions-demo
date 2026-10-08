# Decision models in practice

An agent makes many small decisions per task: is this command safe, does this email fit the thesis, which
sector is this company in. Today each one is usually a full language-model call: seconds of generation, output
billed at several times the input price, and a confidence that comes back as prose.

Decision models answer the same typed question without generating text. You send a Pydantic model; they return
the answer and a probability for every option. This repository asks the same questions of a decision model
(TypeSafe's Jev) and of Claude, on three tasks, and measures agreement, latency, cost and calibration. Its
design is one pattern throughout: **the decision model answers everything, and only the cases it is unsure
about go to the language model.**

It is the code and data behind a series of blog posts. For the background on what decision models are, why
they are faster and cheaper, and what they can't do, see [docs/decision-models.md](docs/decision-models.md).

## The demos

**1. Guardrail: should an agent run this shell command?**
100 real Bash commands from Claude Code sessions, judged by Jev and Sonnet on four questions (accept, review or
reject; destructive; outside the working directory; misleading). Then a confidence band decides which commands
Jev settles alone and which go to Sonnet. [docs/guardrail.md](docs/guardrail.md)

**2. Deal-flow triage for a venture fund.**
50 synthetic inbound emails, from three-line intros to long founder emails and forwarded threads, triaged
against a fund thesis: stage, sector, fit, priority, next step. An OpenAI model writes the emails and the
reference labels, so Claude is never scored against its own family's labels. Jev triages every email; Sonnet
writes a partner note only for the few Jev ranks high, and the report prices every step. [docs/dealflow.md](docs/dealflow.md)

**3. Investing domains: classifying 500 real companies into a two-level taxonomy.**
Y Combinator companies classified into domain → subindustry from a fund's own written definitions. Four ways of
asking Jev the same question, Sonnet as the comparison, Opus adjudicating the disputed cases, and calibration
of Jev's probabilities. [docs/domains.md](docs/domains.md)

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```
uv sync
cp .env.example .env        # add TYPESAFE_API_KEY and ANTHROPIC_API_KEY; OPENAI_API_KEY and LOGFIRE_TOKEN are optional
uv run --env-file .env jev_vs_sonnet.py     # one command, one schema, two models, timed
```

Every demo runs as a few commands that write JSONL into `data/`, and a `report` command that reads those files
and never calls a model. Each demo's doc lists its commands. Demo 3's results are committed, so
`uv run report domains` works straight after cloning, without API keys.

## How it works

### One schema, every backend

Each question is a Pydantic model whose field docstrings are the questions, written to be read literally. The
same model is sent to every backend through [Pydantic AI](https://ai.pydantic.dev). Jev turns a `bool` into a
yes/no question, a `Literal` or `Enum` into a choice, and an ordered `IntEnum` into a rubric, and returns a
probability for every option. Claude gets the same model as structured output.

| Backend | Model | Thinking |
|---|---|---|
| `jev` | `typesafe:jev-latest` | none; returns a probability per option |
| `sonnet` | `anthropic:claude-sonnet-5-5` | set per demo (below) |
| `sonnet_thinking` | `anthropic:claude-sonnet-5-5` | always on (adaptive) |
| `sonnet_no_thinking` | `anthropic:claude-sonnet-5-5` | always off |
| `opus` | `anthropic:claude-opus-5-5` | always on, effort high; used for reference labels and adjudication |
| `luna_fallback` | `openai:gpt-6-luna` | optional comparison in demo 1, through structured outputs |

Backends live in `src/decisions_demo/core/backends.py`. Each demo is a `Demo` in the code
(`src/decisions_demo/<demo>/demo.py`): a dataset, the text each backend sees per record, and settings such as
`sonnet_thinking`, which decides what `sonnet` means in that demo.

| Demo | `sonnet` (main run) | Comparison run |
|---|---|---|
| 1 guardrail | thinking on; also where uncertain commands are routed | `sonnet_no_thinking` |
| 2 dealflow | thinking off, for triage; the partner notes use `sonnet_thinking` | none |
| 3 domains | thinking off | `sonnet_thinking` on 100 of the 500 companies |

### How thinking changes the request

Thinking changes more than how hard Claude works: it decides how Pydantic AI sends the schema
(`claude_settings` in `core/backends.py`).

- **Thinking off.** The 5.5 models don't accept `{"type": "disabled"}`, so "off" is
  `{"type": "between_tools"}`, the closest setting. The schema goes as a tool definition, verbatim: Claude can
  only answer with the listed options and reads the same option descriptions Jev does. The tool definition is
  identical on every call of a batch, so it is prompt-cached (`anthropic_cache_tool_definitions`), which cuts
  demo 3's Sonnet input cost by about three quarters. The 5.5 models can't be forced to call a tool, so Claude
  first writes a short visible rationale and then calls it. The answer is only ever the tool call's arguments,
  validated against the schema; the text is stored as `rationale` in each result. On these models "off" means
  "reasons briefly in plain text", not "no reasoning".
- **Thinking on.** Thinking can't be combined with a forced tool call, so Pydantic AI uses native structured
  output in strict mode, uncached. Strict mode keeps each option's description but stops enforcing the option
  list itself, so an answer outside it is caught by Pydantic validation and retried. Strict mode also requires a
  `type` on every option, which Pydantic AI's `Choices` and `BoolCriteria` don't emit; demo 3 builds its options
  with its own `schemas.options()` for that reason, and its yes/no questions run on Jev only.

### Results, cost and latency

- Every run writes `data/results/<demo>.<run>.jsonl`: one row per record with the parsed output, Jev's
  confidence and full probabilities, Claude's rationale, wall-clock latency, and input, output and cache tokens.
  A failed call is kept as a row with an `error`.
- Cost is computed from those tokens and the price table in `core/report.py`, with cache writes at 1.25× and
  cache reads at 0.1× the input price. Jev bills input only.
- Latency is measured with several requests in flight per backend (`core/runner.py`); the reports state the
  concurrency next to the numbers.
- Reference labels come from a careful model (Opus with thinking) or, in demo 2, from the OpenAI model that
  wrote the data, plus hand corrections. They are references, not ground truth, and each demo's doc says how they were made.

### Repository layout

```
jev_vs_sonnet.py           one command, two models, timed
docs/                      background and one document per demo
data/                      inputs, reference labels, results and figures (see Data below)
src/decisions_demo/
  cli.py                   run-demo and report, across demos
  core/                    backends, batch runner, scoreboard and cost
  guardrail/               demo 1: mine-sessions, label, report
  dealflow/                demo 2: generate, triage, notes, report
  domains/                 demo 3: fetch, define, classify, adjudicate, report
```

## Data and licence

The code is MIT ([LICENSE](LICENSE)); the data this repository produced is CC BY 4.0. Third-party data is not
redistributed: YC's company descriptions are rebuilt on your machine by `uv run domains fetch`, and demo 1 mines
your own Claude Code sessions. [DATA.md](DATA.md) lists which file is which, and credits Y Combinator and the
yc-oss mirror.
