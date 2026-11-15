"""Bedrock transport for the agent layer.

The only module that talks to the model. It knows nothing about agents, tool
allowlists or output schemas: the loop and the agents own those.

Responsibilities
  - one lazily built client (bedrock-mantle endpoint, Bedrock API key)
  - role -> model ID resolution from env, so models can be swapped in config
  - one call = one responses.create(..., store=False)
  - retries for transient failures only, with capped exponential backoff
  - normalised result: text, parsed tool calls, replayable output items, usage
  - mock mode for tests and for teammates without a key

Env
  AWS_BEARER_TOKEN_BEDROCK   Bedrock API key (short-term keys expire <= 12 h)
  AWS_REGION                 default us-east-1
  MODEL_REASONING            default openai.gpt-oss-120b
  MODEL_FAST                 default openai.gpt-oss-20b
  LLM_BASE_URL               optional override of the full base URL
  LLM_TIMEOUT_S              default 60
  LLM_MODE                   "live" (default) or "mock"
  LLM_ECHO_REASONING         "on" (default) or "off": keep reasoning items in
                             output_items. Turn off if Bedrock rejects them
                             when they are sent back on the next turn.
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Optional, Union

log = logging.getLogger("agents.llm")


# ---------------------------------------------------------------- errors ----

class LLMError(RuntimeError):
    """Base class for everything this module raises."""


class LLMAuthError(LLMError):
    """Missing, expired or unauthorised key (401/403). Never retried."""


class LLMBadRequestError(LLMError):
    """Request rejected (400/404/422), e.g. unknown model, unsupported
    feature, or 'not available for this account'. Never retried."""


class LLMTransientError(LLMError):
    """Connection, timeout, rate-limit or 5xx failure that persisted after
    all retries."""


# ----------------------------------------------------------------- types ----

@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]
    parse_error: Optional[str] = None   # set if the model's arguments were not valid JSON


@dataclass
class LLMResult:
    text: Optional[str]                              # final answer, if any
    tool_calls: list[ToolCall] = field(default_factory=list)
    output_items: list[dict[str, Any]] = field(default_factory=list)  # echo into next input
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_s: float = 0.0
    model: str = ""


# ---------------------------------------------------------------- config ----

_DEFAULT_MODELS = {
    "reasoning": "openai.gpt-oss-120b",
    "fast": "openai.gpt-oss-20b",
}


def model_for(role: str) -> str:
    """Resolve a role ('reasoning' | 'fast') to a model ID."""
    if role not in _DEFAULT_MODELS:
        raise LLMError(f"Unknown role {role!r}; expected one of {sorted(_DEFAULT_MODELS)}")
    return os.getenv(f"MODEL_{role.upper()}", _DEFAULT_MODELS[role])


_client = None


def get_client():
    """Build the client once. Loads .env on first use, not at import time."""
    global _client
    if _client is not None:
        return _client

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    key = os.getenv("AWS_BEARER_TOKEN_BEDROCK")
    if not key:
        raise LLMAuthError(
            "AWS_BEARER_TOKEN_BEDROCK is not set. Generate a short-term key in "
            "the Bedrock console and put it in .env."
        )

    from openai import OpenAI

    region = os.getenv("AWS_REGION", "us-east-1")
    base_url = os.getenv("LLM_BASE_URL") or f"https://bedrock-mantle.{region}.api.aws/v1"
    _client = OpenAI(
        base_url=base_url,
        api_key=key,
        timeout=float(os.getenv("LLM_TIMEOUT_S", "60")),
        max_retries=0,          # retries are handled here, once
    )
    return _client


def reset_client() -> None:
    """Drop the cached client, e.g. after rotating the key."""
    global _client
    _client = None


# ------------------------------------------------------------------ mock ----

_mock_queue: Deque[LLMResult] = deque()


def set_mock_results(results: list[LLMResult]) -> None:
    """Queue canned results for LLM_MODE=mock. Each call() pops one."""
    _mock_queue.clear()
    _mock_queue.extend(results)


# ------------------------------------------------------------------ core ----

def _parse_response(resp: Any, model: str, latency_s: float) -> LLMResult:
    echo_reasoning = os.getenv("LLM_ECHO_REASONING", "on") == "on"
    tool_calls: list[ToolCall] = []
    items: list[dict[str, Any]] = []

    for item in getattr(resp, "output", None) or []:
        item_type = getattr(item, "type", None)
        if item_type == "reasoning" and not echo_reasoning:
            continue
        items.append(item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else dict(item))

        if item_type == "function_call":
            raw = getattr(item, "arguments", "") or "{}"
            try:
                args = json.loads(raw)
                if not isinstance(args, dict):
                    raise ValueError("arguments must be a JSON object")
                err = None
            except ValueError as e:
                args, err = {}, f"invalid tool arguments: {e}"
            tool_calls.append(
                ToolCall(
                    call_id=getattr(item, "call_id", None) or getattr(item, "id", ""),
                    name=getattr(item, "name", ""),
                    arguments=args,
                    parse_error=err,
                )
            )

    usage = getattr(resp, "usage", None)
    text = getattr(resp, "output_text", None) or None
    return LLMResult(
        text=text if not tool_calls else (text or None),
        tool_calls=tool_calls,
        output_items=items,
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        latency_s=round(latency_s, 3),
        model=model,
    )


def call(
    role: str,
    instructions: str,
    input_items: Union[str, list[dict[str, Any]]],
    tools: Optional[list[dict[str, Any]]] = None,
    *,
    max_attempts: int = 4,
) -> LLMResult:
    """One model call. Returns an LLMResult; raises an LLMError subclass.

    tools use the Responses format:
      {"type": "function", "name": ..., "description": ..., "parameters": {JSON schema}}
    To continue after tool calls, send the previous output_items plus one
    {"type": "function_call_output", "call_id": ..., "output": "<json string>"}
    per call. With store=False the server keeps no state between calls.
    """
    if os.getenv("LLM_MODE", "live") == "mock":
        if not _mock_queue:
            raise LLMError("LLM_MODE=mock but no mock results are queued")
        return _mock_queue.popleft()

    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        InternalServerError,
        NotFoundError,
        PermissionDeniedError,
        RateLimitError,
        UnprocessableEntityError,
    )

    model = model_for(role)
    kwargs: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": input_items,
        "store": False,                     # nothing retained server-side
    }
    if tools:
        kwargs["tools"] = tools

    transient = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)
    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        t0 = time.monotonic()
        try:
            resp = get_client().responses.create(**kwargs)
            result = _parse_response(resp, model, time.monotonic() - t0)
            log.info(
                "llm call ok role=%s model=%s latency_s=%.2f in=%s out=%s tool_calls=%d",
                role, model, result.latency_s, result.input_tokens,
                result.output_tokens, len(result.tool_calls),
            )
            return result
        except (AuthenticationError, PermissionDeniedError) as e:
            raise LLMAuthError(
                f"{e}. Check that the key is current (short-term keys expire in "
                "<= 12 h) and that the identity has the Bedrock Mantle permissions."
            ) from e
        except (BadRequestError, NotFoundError, UnprocessableEntityError) as e:
            raise LLMBadRequestError(
                f"Model {model!r} rejected the request: {e}. If this says the model is "
                "not available for this account, the model is gated; if it says the "
                "route is unsupported, try the /openai/v1 base URL via LLM_BASE_URL."
            ) from e
        except transient as e:
            last_error = e
            if attempt == max_attempts:
                break
            delay = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.5)
            log.warning(
                "llm transient error role=%s attempt=%d/%d: %s; retrying in %.1fs",
                role, attempt, max_attempts, type(e).__name__, delay,
            )
            time.sleep(delay)
        except APIStatusError as e:
            raise LLMError(f"Unexpected API status {e.status_code}: {e}") from e

    raise LLMTransientError(
        f"Model call failed after {max_attempts} attempts: {last_error}"
    ) from last_error


if __name__ == "__main__":
    # Smoke test: python -m services.agents.llm
    logging.basicConfig(level=logging.INFO)
    out = call("fast", "You are a connectivity test.", "Reply with one word: ready")
    print(out.text, f"({out.model}, {out.latency_s}s)")