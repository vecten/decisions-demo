"""OpenAI's Decisions API (`POST /v1/decisions`, public beta since 2026-10-06) as a Pydantic AI decision model.

Pydantic AI's `DecisionModel` turns an output type into typed questions (a `bool` is a yes/no, a `Literal` a
pick-one, a described `IntEnum` a rubric) and reads the answers back into the output, the per-field confidence
and the full distributions. TypeSafe's Jev is one subclass of it; this is another, so Jev and Luna are asked the
same questions, built from the same field docstrings and option descriptions, and report in the same shape.
Only the wire format differs, and that is all this module does. Pydantic AI 2.54 has no OpenAI Decisions model.

Two things the OpenAI wire can't carry as Pydantic AI builds them, rendered into the question's text instead:
  - instructions are a JSON object ({field, question, goal, background}); OpenAI takes a string, so each key
    becomes a line, in order.
  - a yes/no's criteria (what yes and no mean); OpenAI's predicate has none, so they become two more lines.

Luna can refuse one question and answer the rest. The output type needs every field, so a refusal fails the
whole call, and the runner records the row as an error naming the refused question. Probabilities come back
rounded to 0.01.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from pydantic import JsonValue
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.models.decision import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionAnswer,
    DecisionModel,
    DecisionModelSettings,
    DecisionQuestion,
    DecisionRequest,
    DecisionResponse,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
)
from pydantic_ai.usage import RequestUsage


def _text(value: JsonValue) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def _instructions(question: DecisionQuestion) -> str:
    """The same content Jev receives as a JSON object, as `key: value` lines."""
    parts = question.instructions if isinstance(question.instructions, dict) else {'question': question.instructions}
    lines = [f'{k}: {_text(v)}' for k, v in parts.items() if v is not None]
    if isinstance(question, NoulQuestion) and question.criteria:
        lines += [f'{k}: {_text(v)}' for k, v in (('if yes', question.criteria.true), ('if no', question.criteria.false)) if v is not None]
    return '\n'.join(lines)


def _question(name: str, question: DecisionQuestion) -> dict:
    q: dict[str, object] = {'type': '', 'name': name, 'instructions': _instructions(question)}
    if isinstance(question, NoulQuestion):
        q['type'] = 'predicate'
    elif isinstance(question, ChoiceQuestion):
        q['type'] = 'choice'
        q['choices'] = [{'value': k} | ({'description': _text(v)} if v is not None else {}) for k, v in question.criteria.items()]
    elif isinstance(question, ScoreQuestion):
        q['type'] = 'score'
        q['levels'] = [{'label': str(i), 'description': _text(v)} for i, v in enumerate(question.criteria)]
    return q


def _answer(answer) -> DecisionAnswer:
    if answer.type == 'predicate':
        return NoulAnswer(noul=answer.probability)
    if answer.type == 'choice':
        return ChoiceAnswer(choice=answer.choice, confidence=answer.confidence,
                            probabilities={p.value: p.probability for p in answer.probabilities})
    if answer.type == 'score':
        return ScoreAnswer(score=answer.score, confidence=answer.confidence,
                           probabilities={p.value: p.probability for p in answer.probabilities},
                           legend={p.value: p.label for p in answer.probabilities})
    raise UnexpectedModelBehavior(f'Luna refused question {answer.name!r}' if answer.type == 'refusal' else f'unexpected answer {answer!r}')


@dataclass(init=False)
class LunaModel(DecisionModel):
    """`gpt-6-luna` on `client.decisions.create`, every question about one record in one call, as Jev does."""

    # Not documented for the beta; measured 2026-10-09: a 256th option is a 400 (`array_above_max_length`), as on
    # Jev. Up to 200 questions per request. Rubrics are unlimited as far as the API says.
    max_choice_options = 255
    max_score_levels = None

    _model_name: str = field(repr=False)
    _client: AsyncOpenAI = field(repr=False)

    def __init__(self, model_name: str = 'gpt-6-luna', *, client: AsyncOpenAI | None = None):
        self._model_name = model_name
        self._client = client or AsyncOpenAI()
        super().__init__()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def system(self) -> str:
        return 'openai'

    @property
    def base_url(self) -> str:
        return str(self._client.base_url)

    async def decide(self, request: DecisionRequest, model_settings: DecisionModelSettings) -> DecisionResponse:
        names = list(request.questions)
        try:
            response = await self._client.decisions.create(
                model=self._model_name,
                input=_text(request.state),
                questions=[_question(n, q) for n, q in request.questions.items()],
                timeout=model_settings.get('timeout'),
                extra_headers=model_settings.get('extra_headers'),
                extra_body=model_settings.get('extra_body'),
            )
        except APIStatusError as e:
            raise ModelHTTPError(status_code=e.status_code, model_name=self._model_name, body=e.body) from e
        except APIConnectionError as e:
            raise ModelAPIError(model_name=self._model_name, message=str(e)) from e
        # Answers come back in question order; the name is echoed too, but optional in the SDK's types.
        answers = {a.name or names[k]: _answer(a) for k, a in enumerate(response.answers)}
        if answers.keys() != set(names):
            raise UnexpectedModelBehavior(f'Luna answered {sorted(answers)}, asked {names}')
        # Usage also reports cached and cache-write tokens, but neither is billed differently: every input token
        # is $0.10 per million. Only input_tokens is passed on, so core.report.cost prices them all the same.
        return DecisionResponse(answers=answers, model_name=response.model,
                                usage=RequestUsage(input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens))
