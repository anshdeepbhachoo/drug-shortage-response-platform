"""Helpers shared by every agent for passing data to and from a model."""

import json
from typing import Callable

from services.agents.tools import ToolRegistry

# llm(system_prompt, task, registry) -> model text.
# agent.run_agent (Bedrock) fits this; tests pass a fake.
LLM = Callable[[str, str, ToolRegistry], str]


def extract_json(text: str) -> dict:
    """Return the first JSON object in the model's text."""

    decoder = json.JSONDecoder()

    for index, character in enumerate(text):
        if character != "{":
            continue

        try:
            value, _ = decoder.raw_decode(text[index:])

            if isinstance(value, dict):
                return value

        except json.JSONDecodeError:
            continue

    raise ValueError("Model did not return a valid JSON object.")


def serialize(value) -> str:
    """JSON-encode untrusted data for a prompt, escaping angle brackets so it
    cannot close the markers it is wrapped in."""

    return (
        json.dumps(value, default=str)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
