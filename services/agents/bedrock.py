"""Amazon Bedrock for every agent: the client, the tool-calling loop (run_agent), validated structured
output (ask_for_model) and which model a run uses (resolve_model).

Importing this module has no side effects beyond reading .env: Bedrock settings are checked when a model
is actually called, so tests and the API can import it without AWS credentials."""

from __future__ import annotations

import logging
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from pydantic import ValidationError

from services.agents.model_io import LLM, extract_json

load_dotenv()

MAX_TOOL_ROUNDS = 8
MAX_TOKENS = 2048

MODEL_ERRORS = (RuntimeError, ValueError, BotoCoreError, ClientError)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# Bedrock client
# ---------------------------------------------------------

def get_bedrock_client():
    """
    Boto3 reads AWS_BEARER_TOKEN_BEDROCK from the environment.

    Requires a Boto3 version that supports Bedrock API keys.
    """

    if not os.getenv("AWS_BEARER_TOKEN_BEDROCK"):
        raise RuntimeError(
            "AWS_BEARER_TOKEN_BEDROCK is missing from .env"
        )

    return boto3.client(
        "bedrock-runtime",
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )


# ---------------------------------------------------------
# Bedrock tool-calling loop
# ---------------------------------------------------------

def run_agent(
    system_prompt: str,
    task: str,
    registry,
):
    """
    Runs an agent using the Bedrock Converse API.

    The agent can call only the tools registered for its stage.
    """

    model = os.getenv("BEDROCK_MODEL_ID")

    if not model:
        raise RuntimeError("BEDROCK_MODEL_ID is missing from .env")

    client = get_bedrock_client()

    messages = [
        {
            "role": "user",
            "content": [
                {"text": task}
            ],
        }
    ]

    tool_specs = registry.specs

    for _ in range(MAX_TOOL_ROUNDS):

        response = client.converse(
            modelId=model,
            system=[
                {"text": system_prompt}
            ],
            messages=messages,
            inferenceConfig={
                "maxTokens": MAX_TOKENS,
                "temperature": 0,
            },
            toolConfig={
                "tools": tool_specs,
            },
        )

        output = response["output"]["message"]
        messages.append(output)

        stop_reason = response.get("stopReason")

        if stop_reason == "tool_use":

            tool_results = []

            for block in output.get("content", []):

                if "toolUse" not in block:
                    continue

                tool_use = block["toolUse"]

                tool_name = tool_use["name"]
                tool_id = tool_use["toolUseId"]
                arguments = tool_use.get("input", {})

                logger.info("Agent called tool: %s", tool_name)

                try:
                    result = registry.execute(
                        tool_name,
                        arguments,
                    )

                    result_content = [
                        {
                            "json": {
                                "result": result
                            }
                        }
                    ]

                    status = "success"

                except Exception:
                    logger.exception(
                        "Tool execution failed: %s",
                        tool_name,
                    )

                    result_content = [
                        {
                            "text": "Tool execution failed. "
                                    "Do not assume the operation succeeded."
                        }
                    ]

                    status = "error"

                tool_results.append(
                    {
                        "toolResult": {
                            "toolUseId": tool_id,
                            "content": result_content,
                            "status": status,
                        }
                    }
                )

            if not tool_results:
                raise RuntimeError(
                    "Bedrock requested tool use but supplied no tools."
                )

            messages.append(
                {
                    "role": "user",
                    "content": tool_results,
                }
            )

            continue

        if stop_reason == "end_turn":

            text_blocks = [
                block["text"]
                for block in output.get("content", [])
                if "text" in block
            ]

            if not text_blocks:
                raise ValueError(
                    "Agent returned no text response."
                )

            return "\n".join(text_blocks)

        raise RuntimeError(
            f"Unexpected Bedrock stop reason: {stop_reason}"
        )

    raise RuntimeError(
        "Agent exceeded the maximum number of tool rounds."
    )


# ---------------------------------------------------------
# Structured model output
# ---------------------------------------------------------

def ask_for_model(
    system_prompt: str,
    task: str,
    registry,
    schema,
    llm: LLM | None = None,
):
    """
    Runs an agent and validates its response with Pydantic.

    llm defaults to Bedrock (run_agent); tests pass a fake.
    A correction attempt preserves the original task and instructions.
    """

    llm = llm or run_agent
    original_task = task

    for attempt in range(2):

        response = llm(system_prompt, task, registry)

        try:
            payload = extract_json(response)
            return schema.model_validate(payload)

        except (ValueError, ValidationError) as error:

            if attempt == 1:
                raise ValueError(
                    f"Agent returned invalid structured output: {error}"
                ) from error

            task = (
                original_task
                + "\n\nYour previous response failed schema validation."
                + "\nCorrect the response and return only valid JSON."
                + f"\nValidation feedback: {str(error)}"
            )

    raise RuntimeError("Unable to obtain valid model output.")


# ---------------------------------------------------------
# Which model a run uses
# ---------------------------------------------------------

def resolve_model(llm: LLM | None, use_rules: bool = False) -> LLM | None:
    """The model the order agent calls: the one given, else Bedrock when configured, else None (rules only)."""

    if use_rules:
        return None
    if llm is not None:
        return llm
    if os.getenv("BEDROCK_MODEL_ID"):
        return lambda system, task, registry: run_agent(system, task, registry)
    return None
