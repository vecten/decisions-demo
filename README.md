# decisions-demo

Sketch of a demo harness for decision models.

```
uv sync
export TYPESAFE_API_KEY=...   ANTHROPIC_API_KEY=...   OPENAI_API_KEY=...   LOGFIRE_TOKEN=...

uv run hook_demo.py                                   # 0. the gist, timed
uv run mine-sessions                                  # 1. ~/.claude/projects -> data/commands.jsonl (then read it!)
uv run label                                          # 2. Opus 5 reference labels -> data/labels.jsonl
uv run run-act --act guardrail --backends sonnet,jev  # 3. precompute
uv run report guardrail                               # 4. scoreboard, disagreements, routing fit
uv run dealflow generate && uv run dealflow triage --backends jev,sonnet && uv run dealflow notes
uv run report dealflow
```

Nothing is implemented beyond what you see; this is the skeleton to hand to Claude Code.
