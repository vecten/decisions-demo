# Decision models

An agent makes many small decisions per task: which tool next, is this command safe, is this ticket urgent,
does this email fit the thesis, which 20 of 500 search hits are worth reading. Today each of them is usually a
full language-model call. The answer is generated token by token, output tokens cost several times more than
input, the model often reasons first (more output), and its confidence comes back as prose ("fairly sure")
rather than a number you can put a threshold on.

A decision model answers the same typed question differently: **unstructured state in, typed probabilities
out, no text generated.** The demos in this repository use [Jev](https://typesafe.ai) from TypeSafe, which
Pydantic AI exposes as `typesafe:jev-latest`. You give it the same Pydantic model you would give a language
model, and it returns the parsed answer plus a probability for every option of every field.

## Same question, two kinds of model

A language model reads the prompt once (prefill) and then runs one more pass per output token (decode), and the
answer has to be parsed out of the text. A decision model, as far as its API reveals, stops after the first
pass and reads the answer off as a distribution over the options. TypeSafe has not published the architecture,
so treat that as what the API implies.

Three consequences follow:

- **No decode loop.** Output is free and latency is one pass over the input.
- **Questions ride together.** Every question about one document shares that pass, so ten questions cost
  roughly what one does.
- **No thinking tokens.** A language model gets more reliable by reasoning first, which costs output. A decision
  model is trained to return a probability directly.

Jev's published price is $0.042 per million input tokens, with no output tokens billed. The demos record the
measured latency and cost of every call next to the language model's, so the comparison here is from data,
not from the price list.

## The pattern: System One filters, System Two reasons

The demos share one design. The decision model answers everything, fast and cheaply, with a probability. The
cases where that probability is uncertain go to a language model, which can reason, explain and write.

```
items ──▶ decision model ──▶ confident (p far from the boundary) ──▶ act on the answer
                        └──▶ uncertain (p near the boundary)     ──▶ language model reasons and explains
```

The width of the uncertain band is the dial: narrow it to save cost, widen it for accuracy. Each demo fits it
on labelled examples and reports how many items the expensive model actually had to see.

## What you give up

- **No text.** A decision model cannot summarise, explain or fill a free-text field. Anything that needs words
  goes to a language model.
- **No why.** You get a number, not the part of the input that drove it.
- **Bias.** Simon Willison's test of asking it to rank towns as "good cities" ranked them by income
  ([post](https://simonw.substack.com/p/jev-introduces-a-new-shape-of-llm)). Keep it away from decisions about
  people.
- **Literal reading.** It answers the question as written. If you mean `$HOME` counts as "outside the
  directory", say so. Negations and boundaries have to be explicit, which is why the field docstrings in these
  demos are written as questions to be read literally.
- **Limited state.** The input is capped (32k tokens at the time of writing) and the model cannot compact its
  own context.

## Further reading

- Samuel Colvin's gist, the starting point for `jev_vs_sonnet.py`:
  https://gist.github.com/samuelcolvin/fa2d9abf8349b18e360f2d326218ffa7
- Pydantic AI's TypeSafe model (field mapping, confidence): https://pydantic.dev/docs/ai/models/typesafe/
- TypeSafe SDK (Noul, Choice, Score): https://docs.typesafe.ai/sdk/python/usage
- Jev as a Pydantic Evals scorer: https://pydantic.dev/articles/jev-evals
