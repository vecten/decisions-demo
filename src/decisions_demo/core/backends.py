"""Every backend behind one interface.

Each backend takes a state string and a Pydantic output type and returns the
parsed output, a per-field confidence map (empty when the model has none),
wall-clock latency and token usage. All of them run through Pydantic AI: Jev and
Luna as decision models (Luna's wire format is core/luna.py), Claude and Luna's
structured-output fallback as language models.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

import logfire
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models import Model

from .luna import LunaModel

T = TypeVar('T', bound=BaseModel)


def configure_logfire(service_name: str = 'decisions-demo') -> None:
    logfire.configure(service_name=service_name, send_to_logfire='if-token-present')
    logfire.instrument_pydantic_ai()


@dataclass
class Decision:
    output: BaseModel
    confidence: dict[str, float] = field(default_factory=dict)
    probabilities: dict[str, dict[str, float]] = field(default_factory=dict)
    """Decision models only (Jev, Luna): the full distribution over each Choice's options, which margins and
    calibration need."""
    rationale: str = ''
    """Claude with thinking off only: the text it writes before calling the output tool. Kept for the
    reports; the answer itself is only ever the tool call's arguments."""
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    backend: str = ''

    def to_record(self) -> dict[str, Any]:
        return {
            'backend': self.backend,
            'output': self.output.model_dump(),
            'confidence': self.confidence,
            'probabilities': self.probabilities,
            'rationale': self.rationale,
            'latency_ms': round(self.latency_ms, 1),
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
            'cache_read_tokens': self.cache_read_tokens,
            'cache_write_tokens': self.cache_write_tokens,
        }


class Backend(Protocol):
    name: str

    async def decide(self, state: str, output_type: type[T], instructions: str | None = None) -> Decision: ...


def rationale(parts) -> str:
    """Text Claude wrote before calling the output tool. Without a tool call (native structured output)
    the text part is the answer itself, not a rationale."""
    if not any(p.part_kind == 'tool-call' for p in parts):
        return ''
    return ' '.join(p.content for p in parts if p.part_kind == 'text').strip()


class PydanticAIBackend:
    """Anything Pydantic AI can address by model string ('typesafe:jev-latest', 'anthropic:claude-sonnet-5-5', ...)
    or as a Model instance (LunaModel)."""

    def __init__(self, name: str, model: str | Model, model_settings: dict[str, Any] | None = None):
        self.name = name
        self.model = model
        self.model_settings = model_settings or {}
        self._agents: dict[type[BaseModel], Agent] = {}

    def _agent(self, output_type: type[T], instructions: str | None) -> Agent:
        key = output_type
        if key not in self._agents:
            self._agents[key] = Agent(
                self.model,
                output_type=output_type,
                instructions=instructions,
                model_settings=self.model_settings,
                defer_model_check=True,
            )
        return self._agents[key]

    async def decide(self, state: str, output_type: type[T], instructions: str | None = None) -> Decision:
        agent = self._agent(output_type, instructions)
        t0 = time.perf_counter()
        result = await agent.run(state)
        latency = (time.perf_counter() - t0) * 1000
        details = result.response.provider_details or {}
        # Decision models (Jev, Luna) expose per-field probabilities here; language models don't.
        confidence = dict(details.get('confidence', {}))
        probabilities = dict(details.get('probabilities', {}))
        usage = result.usage
        return Decision(
            output=result.output,
            confidence=confidence,
            probabilities=probabilities,
            rationale=rationale(result.response.parts),
            latency_ms=latency,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=usage.cache_read_tokens or 0,
            cache_write_tokens=usage.cache_write_tokens or 0,
            backend=self.name,
        )


def claude_settings(thinking: bool) -> dict[str, Any]:
    """Thinking decides how Pydantic AI sends the output schema to Claude, not just how hard it thinks.

    On: adaptive thinking, and the schema goes as native structured output in strict mode. Strict mode
    keeps each option's description but no longer pins the option list, so a pick outside it is caught
    by validation and retried.

    Off: the 5.5 models reject `disabled`; `between_tools` is the closest setting, and with it the schema
    goes as a tool, verbatim, the same options Jev sees, cached because it is identical on every call
    (below the model's minimum, 512 tokens on Sonnet 5.5, it just isn't). The 5.5 models can't be forced
    to call a tool, so Claude writes a short visible rationale first and then calls it; the answer is
    only ever the call's arguments, and the text is kept as Decision.rationale.
    """
    if thinking:
        return {'anthropic_thinking': {'type': 'adaptive'}}
    return {'anthropic_thinking': {'type': 'between_tools'}, 'anthropic_cache_tool_definitions': True}


def make_backends(which: list[str], sonnet_thinking: bool = True) -> list[Backend]:
    """`sonnet` follows the demo's setting (Demo.sonnet_thinking); the two explicit Sonnet names never change."""
    registry: dict[str, Backend] = {
        'jev': PydanticAIBackend('jev', 'typesafe:jev-latest', {'timeout': 10}),
        'sonnet': PydanticAIBackend('sonnet', 'anthropic:claude-sonnet-5-5', claude_settings(sonnet_thinking)),
        'sonnet_thinking': PydanticAIBackend('sonnet_thinking', 'anthropic:claude-sonnet-5-5', claude_settings(True)),
        'sonnet_no_thinking': PydanticAIBackend('sonnet_no_thinking', 'anthropic:claude-sonnet-5-5', claude_settings(False)),
        # Slow, expensive reference judge for labels and adjudication. Always thinks, in every demo.
        # Opus 5.5 defaults to effort medium; pin high.
        'opus': PydanticAIBackend('opus', 'anthropic:claude-opus-5-5', {**claude_settings(True), 'anthropic_effort': 'high'}),
        # OpenAI's Decisions API (public beta), every question about a record in one call, as Jev.
        'luna': PydanticAIBackend('luna', LunaModel('gpt-6-luna'), {'timeout': 30}),
        # The same model through structured outputs: no probabilities, so what the decision endpoint adds.
        'luna_fallback': PydanticAIBackend('luna_fallback', 'openai:gpt-6-luna'),
    }
    return [registry[w] for w in which]
