# Data

The code is MIT licensed (see [LICENSE](LICENSE)). The data this repository produced is licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/): reuse it with credit to this repository. Third-party
data is not redistributed; the commands below rebuild it on your machine.

## In this repository (CC BY 4.0)

| File | What it is | Made by |
|---|---|---|
| `data/company_sample.jsonl` | Demo 3's 500-company sample: YC id, company name, YC's industry and subindustry, batch, and a hash of each description. No descriptions. | sampled by `domains fetch` |
| `data/domain_definitions.json` | Demo 3's taxonomy: one definition per domain and per subindustry | domains by hand, subindustries by Claude Sonnet, reviewed |
| `data/results/domains.*.jsonl` | Every demo 3 model run: outputs, probabilities, rationales, latency, tokens | Jev (TypeSafe), Claude Sonnet 5.5 (Anthropic) |
| `data/domain_adjudicated.jsonl` | Labels with one-sentence explanations for the companies the runs disputed | Claude Opus 5.5 (Anthropic) |
| `data/figures/*.png` | Demo 3's Noul heatmaps | `report domains` |
| `data/deals.jsonl` | Demo 2's 50 synthetic emails, with intended labels and a reason per label | OpenAI `gpt-6.1-sol` |
| `data/results/dealflow.*.jsonl` | Demo 2's triage runs, partner notes and the generator's usage | Jev, Claude Sonnet 5.5 |

The company names and YC labels in `company_sample.jsonl` are facts from YC's directory, included so the sample
can be identified and scored; the licence covers this repository's selection and the columns it computed.
Model rationales and explanations describe the companies in the models' own words.

## Rebuilt locally (not in this repository)

| File | How to get it |
|---|---|
| `data/companies.jsonl` | `uv run domains fetch` rebuilds the published sample, with descriptions, from the YC mirror. It lists any company whose description YC has edited since the sample was drawn (the hash no longer matches), and any that left the directory. Model runs need this file; the report doesn't. |
| `data/commands.jsonl`, `data/labels.jsonl`, `data/results/guardrail.*` | Demo 1 mines your own Claude Code sessions: `uv run mine-sessions`, `uv run label`, then `run-act`. Personal, so never committed. |

## Credits

- Company data: [Y Combinator's company directory](https://www.ycombinator.com/companies), through
  [yc-oss/api](https://github.com/yc-oss/api), an unofficial public mirror that declares no licence. The
  descriptions belong to YC and the companies, which is why they are rebuilt locally rather than committed.
- Decision model: Jev by [TypeSafe](https://typesafe.ai).
- Language models: Claude Sonnet 5.5 and Claude Opus 5.5 by [Anthropic](https://www.anthropic.com); demo 2's
  emails by `gpt-6.1-sol` from [OpenAI](https://openai.com).
