"""Three backends behind one interface.

Each backend takes a state string and a Pydantic output type and returns the
parsed output, a per-field confidence map (empty when the model has none),
wall-clock latency and token usage. Pydantic AI handles Jev and Sonnet already;
Luna is a stub until the Decisions API preview is visible.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

import logfire
from pydantic import BaseModel
from pydantic_ai import Agent

T = TypeVar('T', bound=BaseModel)


def configure_logfire(service_name: str = 'decisions-demo') -> None:
    logfire.configure(service_name=service_name, send_to_logfire='if-token-present')
    logfire.instrument_pydantic_ai()


@dataclass
class Decision:
    output: BaseModel
    confidence: dict[str, float] = field(default_factory=dict)
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    backend: str = ''

    def to_record(self) -> dict[str, Any]:
        return {
            'backend': self.backend,
            'output': self.output.model_dump(),
            'confidence': self.confidence,
            'latency_ms': round(self.latency_ms, 1),
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
        }


class Backend(Protocol):
    name: str

    async def decide(self, state: str, output_type: type[T], instructions: str | None = None) -> Decision: ...


class PydanticAIBackend:
    """Anything Pydantic AI can address by model string: 'typesafe:jev-latest', 'anthropic:claude-sonnet-5', ..."""

    def __init__(self, name: str, model: str, model_settings: dict[str, Any] | None = None):
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
        # Jev exposes per-field probabilities here; language models don't.
        confidence = dict(details.get('confidence', {}))
        usage = result.usage
        return Decision(
            output=result.output,
            confidence=confidence,
            latency_ms=latency,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            backend=self.name,
        )


class LunaDecisionsBackend:
    """OpenAI Decisions API (limited preview since 2026-09-29).

    Shape as announced: context (text or image) + question + finite answers -> one
    answer + confidence. Plan: walk the Pydantic model's fields, ask one decision
    per bool/Literal/IntEnum field, assemble the model, keep confidences.
    Fill in once the preview docs are in hand; until then the runner skips it.
    """

    name = 'luna'

    async def decide(self, state: str, output_type: type[T], instructions: str | None = None) -> Decision:
        raise NotImplementedError('Decisions API adapter pending preview access; use luna_fallback')


def make_backends(which: list[str]) -> list[Backend]:
    registry: dict[str, Backend] = {
        'jev': PydanticAIBackend('jev', 'typesafe:jev-latest', {'timeout': 10}),
        'sonnet': PydanticAIBackend('sonnet', 'anthropic:claude-sonnet-5'),
        # Slow, expensive reference judge for labels. Extended thinking on.
        'opus': PydanticAIBackend('opus', 'anthropic:claude-opus-5', {'anthropic_thinking': {'type': 'adaptive'}, 'anthropic_effort': 'high'}),
        # Same schema through structured outputs. Confidence is self-reported, not a calibrated probability.
        'luna_fallback': PydanticAIBackend('luna_fallback', 'openai:gpt-6-luna'),
        'luna': LunaDecisionsBackend(),
    }
    return [registry[w] for w in which]
