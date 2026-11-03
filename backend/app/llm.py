"""Meta Model API client. Every model call goes through `complete`, which caches responses in
SQLite (a repeated call is free), retries transient failures, validates structured output
against a Pydantic model, and logs tokens and cost per call.

The API is OpenAI-compatible: chat completions with strict JSON-schema output and image input.
Only standard models are used: `-contributor` models train on their inputs, and real
research notes must never feed them.
"""

import base64
import hashlib
import json
import os
import time
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app import store
from app.models import Asset

BASE_URL = os.environ.get("MODEL_BASE_URL") or "https://api.meta.ai/v1"
MODEL = os.environ.get("MODEL_NAME") or "muse-spark-1.3"
PRICES = {  # dollars per million input / output tokens; reasoning tokens bill as output
    "muse-spark-1.3": (1.25, 4.25),
    "muse-spark-1.2": (1.25, 4.25),
    "muse-spark-1.1": (1.25, 4.25),
}
RETRIES = 3
TIMEOUT = 600  # seconds; reading a whole lab history in one call can take minutes


class LLMError(RuntimeError):
    pass


class Usage(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    seconds: float = 0.0
    cached: bool = False


def strict_schema(model: type[BaseModel]) -> dict:
    """The model's JSON schema in the strict form structured output needs: every property
    required (optional ones are nullable instead), no extra properties, no defaults."""
    schema = model.model_json_schema()

    def tighten(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                tighten(child)
            return
        if not isinstance(node, dict):
            return
        node.pop("default", None)
        node.pop("title", None)
        for key, value in node.items():
            # "properties" and "$defs" map names to schemas; only their values are schemas.
            for child in value.values() if key in ("properties", "$defs") else [value]:
                tighten(child)
        if "properties" in node:
            node["required"] = list(node["properties"])
            node["additionalProperties"] = False

    tighten(schema)
    return schema


def image_part(asset: Asset) -> dict:
    data = base64.b64encode(asset.data).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{asset.media_type};base64,{data}"}}


def post(body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
    """Send one request as a stream and put the answer back together. The API's gateway times
    out a long silent request (504), and a stream keeps the connection busy while the model
    thinks. Returns the status and the response as a non-streamed call would, or the error."""
    streamed = {**body, "stream": True, "stream_options": {"include_usage": True}}
    with httpx.stream(
        "POST", f"{BASE_URL}/chat/completions", headers=headers, json=streamed, timeout=TIMEOUT
    ) as response:
        if response.status_code >= 400:
            return response.status_code, response.read().decode(errors="replace")[:300]
        parts: list[str] = []
        finish: str | None = None
        usage: dict[str, int] = {}
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = line.removeprefix("data:").strip()
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            usage = chunk.get("usage") or usage
            for choice in chunk.get("choices") or []:
                parts.append((choice.get("delta") or {}).get("content") or "")
                finish = choice.get("finish_reason") or finish
    message = {"content": "".join(parts)}
    return response.status_code, {
        "choices": [{"message": message, "finish_reason": finish}],
        "usage": usage,
    }


def complete[T: BaseModel](
    messages: list[dict],
    output: type[T],
    *,
    purpose: str,
    effort: str = "low",
    max_tokens: int = 32000,
    model: str = MODEL,
) -> tuple[T, Usage]:
    """One structured completion: the response parsed into `output`.

    Raises LLMError when the API keeps failing or the output never fits the schema.
    """
    if model.endswith("-contributor"):
        raise LLMError("contributor models train on their inputs; use a standard model")
    body = {
        "model": model,
        "messages": messages,
        "reasoning_effort": effort,
        "max_completion_tokens": max_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": output.__name__,
                "strict": True,
                "schema": strict_schema(output),
            },
        },
    }
    key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    with store.connect() as conn:
        cached = store.get_llm_call(conn, key)
    if cached is not None:
        return output.model_validate_json(cached), Usage(cached=True)

    api_key = os.environ.get("MODEL_API_KEY")
    if not api_key:
        raise LLMError("MODEL_API_KEY is not set; add it to the .env file at the repo root")
    headers = {"Authorization": f"Bearer {api_key}"}
    usage = Usage()
    for attempt in range(RETRIES):
        started = time.monotonic()
        try:
            status, data = post(body, headers)
        except httpx.TransportError as error:  # timeouts and dropped connections
            if attempt == RETRIES - 1:
                raise LLMError(f"model API unreachable: {error}") from error
            time.sleep(2**attempt)
            continue
        if status == 429 or status >= 500:
            if attempt == RETRIES - 1:
                raise LLMError(f"model API error {status}: {data}")
            time.sleep(2 ** (attempt + 1))
            continue
        if status >= 400:
            raise LLMError(f"model API error {status}: {data}")

        tokens = data.get("usage", {})
        price_in, price_out = PRICES.get(model, (0.0, 0.0))
        usage.tokens_in += tokens.get("prompt_tokens", 0)
        usage.tokens_out += tokens.get("completion_tokens", 0)
        usage.seconds += time.monotonic() - started
        usage.cost = (usage.tokens_in * price_in + usage.tokens_out * price_out) / 1e6
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
        if choice.get("finish_reason") == "length":
            raise LLMError(f"{purpose}: output hit the {max_tokens}-token limit")
        try:
            parsed = output.model_validate_json(content)
        except ValidationError as error:
            if attempt == RETRIES - 1:
                raise LLMError(f"{purpose}: output did not fit the schema: {error}") from error
            # Show the model its own answer and the problem; strict mode makes this rare.
            body["messages"] = [
                *messages,
                {"role": "assistant", "content": content},
                {"role": "user", "content": f"That output is invalid: {error}. Answer again."},
            ]
            continue
        with store.connect() as conn:
            store.save_llm_call(
                conn,
                key,
                model=model,
                purpose=purpose,
                response=content,
                tokens_in=usage.tokens_in,
                tokens_out=usage.tokens_out,
                cost=usage.cost,
                seconds=round(usage.seconds, 2),
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
        return parsed, usage
    raise LLMError(f"{purpose}: no usable response")
