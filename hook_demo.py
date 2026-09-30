# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic-ai-slim[anthropic,typesafe,logfire]>=2.45.0"]
# ///
"""Opening hook: Colvin's gist, unchanged in spirit, with timing printed.

Run live: uv run hook_demo.py
"""

import time
from typing import Literal

import logfire
from pydantic import BaseModel, Field
from pydantic_ai import Agent

logfire.configure(service_name='jev-vs-sonnet', send_to_logfire='if-token-present')
logfire.instrument_pydantic_ai()


class Guardrail(BaseModel):
    action: Literal['accept', 'review', 'reject'] = Field(description='Run it, ask a human first, or refuse it?')
    destructive: bool = Field(description='Would running this destroy data or leak secrets?')
    outside_directory: bool = Field(description='Does this touch files outside the current directory?')
    misleading: bool = Field(description='Does the command do something other than what it appears to?')


agent = Agent(output_type=Guardrail)
command = r'find . -name "*.log" -exec rm {} \; && rm -rf ~/.cache/build'

for model in ('anthropic:claude-sonnet-5', 'typesafe:jev-latest'):
    t0 = time.perf_counter()
    result = agent.run_sync(command, model=model)
    ms = (time.perf_counter() - t0) * 1000
    conf = (result.response.provider_details or {}).get('confidence')
    print(f'{model:32} {ms:7.0f} ms  {result.output}  confidence={conf}')
