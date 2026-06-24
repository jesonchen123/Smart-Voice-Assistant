"""MCP_Cluster shared LLM Gateway client.

The LLMServer keeps its public OpenAI-compatible API. This module only changes
where the internal model call is dispatched when SHARED_PLATFORM_ENABLED=true.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import AsyncIterator, Optional

import httpx

import config


async def _request_payload(
    *,
    messages: list[dict],
    temperature: float,
    max_tokens: Optional[int],
) -> dict:
    task_config = await get_task_config()
    payload = {
        "project_id": config.SHARED_PLATFORM_PROJECT_ID,
        "env": config.SHARED_PLATFORM_ENV,
        "task_type": config.SHARED_PLATFORM_TASK_TYPE,
        "model_policy_id": task_config.get("model_policy_id") or config.SHARED_PLATFORM_MODEL_POLICY_ID,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    return payload


def _format_error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"Shared platform error {response.status_code}: {response.text}"
    error = data.get("error") or {}
    code = error.get("code", "UNKNOWN")
    message = error.get("message", response.text)
    return f"Shared platform error {response.status_code} {code}: {message}"


async def _get_json(path: str) -> dict:
    async with httpx.AsyncClient(
        base_url=config.SHARED_PLATFORM_BASE_URL.rstrip("/"),
        timeout=config.SHARED_PLATFORM_TIMEOUT,
    ) as client:
        response = await client.get(path)
    if response.status_code >= 400:
        raise RuntimeError(_format_error(response))
    return response.json()


async def _post_json(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(
        base_url=config.SHARED_PLATFORM_BASE_URL.rstrip("/"),
        timeout=config.SHARED_PLATFORM_TIMEOUT,
    ) as client:
        response = await client.post(path, json=payload)
    if response.status_code >= 400:
        raise RuntimeError(_format_error(response))
    return response.json()


async def get_task_config() -> dict:
    return await _get_json(
        f"/api/v1/configs/{config.SHARED_PLATFORM_PROJECT_ID}/"
        f"{config.SHARED_PLATFORM_ENV}/tasks/{config.SHARED_PLATFORM_TASK_TYPE}"
    )


def _wrap_completion(data: dict) -> dict:
    request_id = data.get("request_id") or "chatcmpl-" + uuid.uuid4().hex[:16]
    model = data.get("model") or "shared-platform"
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": data.get("content", ""),
                },
                "finish_reason": data.get("finish_reason") or "stop",
            }
        ],
        "usage": data.get("usage"),
    }


def _wrap_chunk(delta_text: str, model: str, response_id: str, finish_reason: str | None = None) -> dict:
    delta = {"content": delta_text} if delta_text else {}
    return {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }


async def complete_chat(
    *,
    messages: list[dict],
    temperature: float,
    max_tokens: Optional[int],
) -> dict:
    data = await _post_json(
        "/api/v1/llm/generate",
        await _request_payload(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ),
    )
    return _wrap_completion(data)


async def render_prompt(
    prompt_key: str,
    *,
    version: str | None = None,
    variables: dict | None = None,
) -> str:
    payload = {
        "project_id": config.SHARED_PLATFORM_PROJECT_ID,
        "env": config.SHARED_PLATFORM_ENV,
        "prompt_key": prompt_key,
        "variables": variables or {},
    }
    if version is not None:
        payload["version"] = version
    data = await _post_json("/api/v1/prompts/render", payload)
    return data.get("rendered_content", "")


async def stream_text(
    *,
    messages: list[dict],
    temperature: float,
    max_tokens: Optional[int],
) -> AsyncIterator[str]:
    async with httpx.AsyncClient(
        base_url=config.SHARED_PLATFORM_BASE_URL.rstrip("/"),
        timeout=config.SHARED_PLATFORM_TIMEOUT,
    ) as client:
        async with client.stream(
            "POST",
            "/api/v1/llm/stream",
            json=await _request_payload(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        ) as response:
            if response.status_code >= 400:
                raise RuntimeError(_format_error(response))
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                text = data.get("text", "")
                if text:
                    yield text


async def stream_chat_chunks(
    *,
    messages: list[dict],
    temperature: float,
    max_tokens: Optional[int],
) -> AsyncIterator[dict]:
    response_id = "chatcmpl-" + uuid.uuid4().hex[:16]
    async for text in stream_text(
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    ):
        yield _wrap_chunk(text, "shared-platform", response_id)
    yield _wrap_chunk("", "shared-platform", response_id, finish_reason="stop")


def _normalize_rag_result(item: dict) -> dict:
    return {
        "text": item.get("content", ""),
        "score": item.get("score", 0),
        "doc_id": item.get("doc_id", ""),
        "source": item.get("title") or item.get("source_type") or item.get("doc_id", ""),
        "kb_id": item.get("kb_id", ""),
        "metadata": item.get("metadata") or {},
    }


async def search_rag(query: str, *, top_k: Optional[int] = None) -> list[dict]:
    if not query:
        return []
    task_config = await get_task_config()
    if task_config.get("rag_enabled") is False:
        return []
    kb_id = task_config.get("rag_policy_id")
    if not kb_id:
        return []
    data = await _post_json(
        "/api/v1/rag/search",
        {
            "project_id": config.SHARED_PLATFORM_PROJECT_ID,
            "env": config.SHARED_PLATFORM_ENV,
            "kb_ids": [kb_id],
            "query": query,
            "top_k": top_k or 4,
        },
    )
    return [_normalize_rag_result(item) for item in data.get("results", [])]
