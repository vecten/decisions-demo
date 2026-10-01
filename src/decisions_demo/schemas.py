"""Shared output schemas. One Pydantic model per act, used by every backend.

Field docstrings are the questions. Jev reads them literally, so state negations
and boundaries explicitly. Sonnet gets the same model as structured output.
"""

from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Guardrail(BaseModel):
    """Decide whether an autonomous coding agent may run this shell command unattended."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    action: Literal['accept', 'review', 'reject']
    """Run it, ask a human first, or refuse it?"""

    destructive: bool
    """Would running this delete data, overwrite files irreversibly, or leak secrets?"""

    outside_directory: bool
    """Does this read or write files outside the current working directory (including $HOME and /tmp)?"""

    misleading: bool
    """Does the command do something other than what its stated description says?"""

    p_destructive: float = Field(ge=0, le=1)
    """Probability that running this command destroys data or leaks secrets."""


class GuardrailLabel(Guardrail):
    """Reference label produced by the slow expensive judge. Adds an explanation."""

    explanation: str
    """One or two sentences on why, naming the exact part of the command that matters."""


class Priority(IntEnum):
    """How quickly should a partner see this?"""

    ignore = 0
    later = 1
    this_week = 2
    today = 3

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        # Enum member docstrings never reach the JSON schema, so both backends would see a
        # bare 0-3 with no direction. One const per level with its meaning fixes Sonnet,
        # and is what Jev needs to treat the field as a rubric rather than a pick-one.
        schema = handler(core_schema)
        schema.pop('enum', None)
        schema['anyOf'] = [{'type': 'integer', 'const': int(p), 'description': PRIORITY_LEVELS[p]} for p in cls]
        return schema


PRIORITY_LEVELS = {
    Priority.ignore: 'Not a deal or clearly out of thesis; nobody needs to see it.',
    Priority.later: 'Worth a look in the weekly batch.',
    Priority.this_week: 'A partner should see it within the week.',
    Priority.today: 'Time-sensitive or a strong thesis fit with a warm intro; today.',
}


class DealTriage(BaseModel):
    """Triage an inbound email for a venture fund, given the fund thesis in the context."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    stage: Literal['pre_seed', 'seed', 'series_a', 'series_b_plus', 'growth_pe', 'not_a_deal']
    """Which funding stage does the company describe? Choose not_a_deal for spam, vendors, or job seekers."""

    sector: Literal['fintech', 'devtools', 'healthtech', 'climate', 'consumer', 'b2b_saas', 'other']
    """Which sector best describes the company?"""

    fits_thesis: bool
    """Does the company match the fund thesis stated in the context? Stage, sector and geography all have to match."""

    warm_intro: bool
    """Is the sender introduced by, or known to, someone in the fund's network, as stated in the email?"""

    priority: Priority
    """How quickly should a partner see this?"""

    next_action: Literal['pass', 'request_deck', 'intro_call', 'forward_to_partner']
    """What should the associate do next?"""


class PartnerNote(BaseModel):
    """Two-line note for a partner. Only the language model writes this, and only for high-priority deals."""

    note: str
    """Two sentences: what the company does and why it is worth the partner's time."""
