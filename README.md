# decisions-demo

Sketch of a demo harness for decision models.

```
uv sync
export TYPESAFE_API_KEY=...   ANTHROPIC_API_KEY=...   OPENAI_API_KEY=...   LOGFIRE_TOKEN=...

uv run hook_demo.py                                   # 0. the gist, timed
uv run mine-sessions                                  # 1. ~/.claude/projects -> data/commands.jsonl (then read it!)
uv run label                                          # 2. Opus reference labels -> data/labels.jsonl (current file: Opus 5)
uv run run-act --act guardrail --backends sonnet,jev  # 3. precompute
uv run report guardrail                               # 4. scoreboard, disagreements, routing fit
uv run run-act --act guardrail --backends sonnet_no_thinking   # act 1's thinking-off Sonnet column
uv run dealflow generate && uv run dealflow triage --backends jev,sonnet && uv run dealflow notes
uv run report dealflow

uv run domains fetch                                  # act 3: 500 YC companies -> data/companies.jsonl, plus a cost preview
uv run domains define                                 # Sonnet writes subindustry definitions once; edit data/domain_definitions.json
uv run domains classify                               # 4 Jev question shapes and Sonnet on all 500, sonnet_thinking on 100
uv run domains classify --runs jev_flat,jev_sequential,jev_fanout,jev_nouls --tag rerun   # Jev stability run
uv run domains classify --runs sonnet --retry-errors  # re-ask only rows that failed (overloaded, timeouts)
uv run domains adjudicate --dry-run                   # how many companies a run disputes with YC, and the Opus cost
uv run domains adjudicate                             # Opus labels them blind -> data/domain_adjudicated.jsonl (hand-editable)
```

## Models, and when Sonnet thinks

Every backend gets the same Pydantic model for a question. The backends live in `src/decisions_demo/core/backends.py`:

| Backend name | Model | Thinking |
|---|---|---|
| `jev` | `typesafe:jev-latest` | none; returns a probability per option (`probabilities` in the results) |
| `sonnet` | `anthropic:claude-sonnet-5-5` | **set per act**, see below |
| `sonnet_thinking` | `anthropic:claude-sonnet-5-5` | always on (adaptive) |
| `sonnet_no_thinking` | `anthropic:claude-sonnet-5-5` | always off (`between_tools`, see below) |
| `opus` | `anthropic:claude-opus-5-5` | always on (adaptive, effort pinned to high; Opus 5.5 defaults to medium), in every act |

Earlier Sonnet 5 runs are kept in `data/results/sonnet-5/`, outside the reports.

What `sonnet` means is set by `sonnet_thinking=` on the act's `Act` in its `act.py`. The runner passes it to
`make_backends(names, act.sonnet_thinking)`. Results files are named after the backend
(`data/results/<act>.<backend>.jsonl`), so `sonnet` is the act's main Sonnet run and the explicit names are the
comparison runs.

| Act | `sonnet` (main run) | Comparison run | Opus |
|---|---|---|---|
| 1 guardrail | thinking on; also where routed commands escalate to | `sonnet_no_thinking`, all 100 commands | reference labels (`label`) |
| 2 dealflow | thinking on, for triage and the partner notes | none | not used |
| 3 domains | thinking off | `sonnet_thinking`, the first 100 of the 500 (the sample is shuffled) | adjudication of disputed companies |

Thinking changes more than depth: it decides how Pydantic AI sends the schema to Claude (`claude_settings` in
`core/backends.py`).

- **Off:** Sonnet 5.5 rejects `{"type": "disabled"}`, so "off" is `{"type": "between_tools"}`, the closest
  setting. The schema goes as a tool definition, verbatim. Claude can only answer with the listed options and
  reads the same option descriptions Jev does. The tool definition is identical on every call of a batch, so it
  is prompt-cached (`anthropic_cache_tool_definitions`), which cuts act 3's Sonnet input cost by about 75%.
  Sonnet 5.5 caches from 512 tokens, so act 1's schema caches too.
  The 5.5 models can't be forced to call a tool, so with thinking off Sonnet first writes a short visible
  rationale (about 150 tokens), then calls the tool. The answer is only ever the tool call's arguments,
  validated against the schema; the text is stored as `rationale` in each result and shown in the reports.
  So on 5.5 "off" means "reasons in plain text instead of in thinking blocks", not "no reasoning".
- **On:** thinking can't be combined with a forced tool call, so Pydantic AI switches to native structured output
  in strict mode, uncached. Strict mode keeps each option's description but stops enforcing the option list, so
  an answer outside it is caught by Pydantic validation and retried. Strict mode also requires a `type` on every
  option. Act 3 builds its options with `schemas.options()`, which adds one; Pydantic AI's `Choices` and
  `BoolCriteria` don't, which is one reason act 3's Nouls run on Jev only.

`core/report.py` prices cache writes at 1.25x and cache reads at 0.1x of the input price, so reported costs
include the caching.

Nothing is implemented beyond what you see; this is the skeleton to hand to Claude Code.
