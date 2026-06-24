"""
豆包 Ark Chat 调用封装。

两个分支:
1) 无状态 chat: 走 openai SDK 的 chat.completions (Ark 完全兼容)。
2) 有状态 chat: 走 openai SDK 的 responses (Ark 的 Responses API 扩展),
   靠 previous_response_id 串联对话历史, 配合 extra_body.caching 启用上下文缓存。

对外接口都返回 Chat Completions 格式的字典 / 流式 chunk,
让 RTC / 调用方完全无感, 不需要知道我们底层用的是 responses 还是 completions。
"""

import time
import uuid as _uuid
from typing import AsyncIterator, Optional

from openai import AsyncOpenAI

import config

from llm import shared_platform_client
_async_client: Optional[AsyncOpenAI] = None


def get_client() -> AsyncOpenAI:
    global _async_client
    if _async_client is None:
        config.assert_filled("ARK_API_KEY", config.ARK_API_KEY)
        _async_client = AsyncOpenAI(
            api_key=config.ARK_API_KEY,
            base_url=config.ARK_BASE_URL,
        )
    return _async_client


# ============== 无状态: 标准 chat.completions ==============

async def stream_chat(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
) -> AsyncIterator[dict]:
    """返回 openai SDK 的 ChatCompletionChunk 异步迭代器, 上层转 SSE。"""
    if config.SHARED_PLATFORM_ENABLED:
        async for chunk in shared_platform_client.stream_chat_chunks(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield chunk
        return

    config.assert_filled("ARK_CHAT_ENDPOINT_ID", config.ARK_CHAT_ENDPOINT_ID)
    client = get_client()
    stream = await client.chat.completions.create(
        model=config.ARK_CHAT_ENDPOINT_ID,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    )
    async for chunk in stream:
        yield chunk


async def complete_chat(
    messages: list[dict],
    temperature: float = 0.3,
    max_tokens: Optional[int] = None,
) -> dict:
    """非流式调用 chat.completions, 用于调试 / curl 验证。"""
    if config.SHARED_PLATFORM_ENABLED:
        return await shared_platform_client.complete_chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    config.assert_filled("ARK_CHAT_ENDPOINT_ID", config.ARK_CHAT_ENDPOINT_ID)
    client = get_client()
    resp = await client.chat.completions.create(
        model=config.ARK_CHAT_ENDPOINT_ID,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=False,
    )
    return resp.model_dump()


# ============== 有状态: Responses API + previous_response_id ==============

def _wrap_chat_completion(text: str, model: str, response_id: str) -> dict:
    """把 Responses API 的整段 output 包成 OpenAI Chat Completions 格式。"""
    return {
        "id": response_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": None,
    }


def _wrap_chat_chunk(delta_text: str, model: str, response_id: str,
                     finish_reason: Optional[str] = None) -> dict:
    """把 Responses 流式的一段文本增量包成 OpenAI Chat Completions chunk 格式。"""
    delta = {"content": delta_text} if delta_text else {}
    if finish_reason:
        delta = delta or {}
    return {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason,
        }],
    }


def _extract_output_text(resp) -> str:
    """从 Responses API 返回里抠出最终的输出文本。SDK 对象兼容 .output_text。"""
    text = getattr(resp, "output_text", None)
    if text:
        return text
    # 兜底: 翻 output 列表里 type=message 的 content
    output = getattr(resp, "output", None) or []
    parts: list[str] = []
    for item in output:
        item_type = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
        if item_type != "message":
            continue
        content = getattr(item, "content", None) or (item.get("content") if isinstance(item, dict) else None) or []
        for c in content:
            c_type = getattr(c, "type", None) or (c.get("type") if isinstance(c, dict) else None)
            if c_type in ("output_text", "text"):
                t = getattr(c, "text", None) or (c.get("text") if isinstance(c, dict) else "")
                if t:
                    parts.append(t)
    return "".join(parts)


def _build_input(system_prompt: str, user_text: str, has_previous: bool) -> list[dict]:
    """
    Responses API input 数组。首轮带 system + user, 后续轮 previous_response_id
    已经承载了之前所有上下文, 只需要新的 user 消息。
    """
    if has_previous:
        return [{"role": "user", "content": user_text}]
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]


def _is_previous_id_expired(exc: Exception) -> bool:
    """
    Ark Responses API 的对话链有 TTL (官方约 24h),
    过期后用旧 previous_response_id 调会报 not found / invalid。
    通过错误文本兜底识别, 触发"全量历史重发"重建链。
    """
    msg = str(exc).lower()
    return (
        "previous_response_id" in msg
        and ("not found" in msg or "invalid" in msg or "expired" in msg or "404" in msg)
    )


def _build_input_from_history(
    system_prompt: str,
    history: list[dict],
    current_user_text: str,
) -> list[dict]:
    """
    链过期兜底用: 把 DB 里历史消息 + 本轮 user 一起塞进 input,
    不带 previous_response_id, 让 Ark 重新建链。
    history 每条形如 {role, content}, 顺序从老到新。
    """
    msgs: list[dict] = [{"role": "system", "content": system_prompt}]
    for m in history:
        role = m.get("role")
        content = m.get("content") or ""
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content": current_user_text})
    return msgs


async def responses_complete(
    system_prompt: str,
    user_text: str,
    previous_response_id: Optional[str],
    temperature: float = 0.3,
    max_tokens: Optional[int] = None,
    history_fallback: Optional[list[dict]] = None,
) -> tuple[dict, str]:
    """
    非流式有状态调用。返回 (chat_completion_dict, new_response_id)。
    new_response_id 由调用方更新到 session_manager。

    history_fallback: 如果 previous_response_id 过期, 用这段历史 (DB 里的
    user/assistant 消息列表, 顺序从老到新) 重发一次, 重建链。
    """
    if config.SHARED_PLATFORM_ENABLED:
        input_messages = _build_input_from_history(
            system_prompt,
            history_fallback or [],
            user_text,
        )
        data = await shared_platform_client.complete_chat(
            messages=input_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return data, data.get("id", "")
    config.assert_filled("ARK_CHAT_ENDPOINT_ID", config.ARK_CHAT_ENDPOINT_ID)
    client = get_client()

    async def _call(prev_id: Optional[str], input_msgs: list[dict]):
        kwargs = dict(
            model=config.ARK_CHAT_ENDPOINT_ID,
            input=input_msgs,
            extra_body={"caching": {"type": "enabled"}},
            temperature=temperature,
        )
        if prev_id:
            kwargs["previous_response_id"] = prev_id
        if max_tokens is not None:
            kwargs["max_output_tokens"] = max_tokens
        return await client.responses.create(**kwargs)

    try:
        resp = await _call(previous_response_id,
                           _build_input(system_prompt, user_text, bool(previous_response_id)))
    except Exception as e:
        if previous_response_id and _is_previous_id_expired(e) and history_fallback is not None:
            print(f"[ark] previous_response_id 过期, 用 {len(history_fallback)} 条历史重建链")
            resp = await _call(None, _build_input_from_history(system_prompt, history_fallback, user_text))
        else:
            raise

    text = _extract_output_text(resp)
    new_id = getattr(resp, "id", "") or ""
    return _wrap_chat_completion(text, getattr(resp, "model", config.ARK_CHAT_ENDPOINT_ID), new_id), new_id


async def responses_stream(
    system_prompt: str,
    user_text: str,
    previous_response_id: Optional[str],
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    history_fallback: Optional[list[dict]] = None,
) -> AsyncIterator[tuple[dict, Optional[str]]]:
    """
    流式有状态调用。yield (chat_completion_chunk_dict, maybe_new_response_id)。
    response_id 通常在 response.created 事件里就拿得到, 之后的 chunk 里 maybe_new_response_id 为 None。

    history_fallback: 链过期时拿来重建链, 同非流式分支。
    """
    if config.SHARED_PLATFORM_ENABLED:
        input_messages = _build_input_from_history(
            system_prompt,
            history_fallback or [],
            user_text,
        )
        local_response_id = "chatcmpl-" + _uuid.uuid4().hex[:16]
        reported = False
        async for chunk in shared_platform_client.stream_text(
            messages=input_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield _wrap_chat_chunk(chunk, "shared-platform", local_response_id), (local_response_id if not reported else None)
            reported = True
        yield _wrap_chat_chunk("", "shared-platform", local_response_id, finish_reason="stop"), None
        return
    config.assert_filled("ARK_CHAT_ENDPOINT_ID", config.ARK_CHAT_ENDPOINT_ID)
    client = get_client()

    async def _start_stream(prev_id: Optional[str], input_msgs: list[dict]):
        kwargs = dict(
            model=config.ARK_CHAT_ENDPOINT_ID,
            input=input_msgs,
            extra_body={"caching": {"type": "enabled"}},
            temperature=temperature,
            stream=True,
        )
        if prev_id:
            kwargs["previous_response_id"] = prev_id
        if max_tokens is not None:
            kwargs["max_output_tokens"] = max_tokens
        return await client.responses.create(**kwargs)

    model_name = config.ARK_CHAT_ENDPOINT_ID
    response_id = ""
    reported_id = False
    fallback_chunk_id = "chatcmpl-" + _uuid.uuid4().hex[:16]

    try:
        stream = await _start_stream(
            previous_response_id,
            _build_input(system_prompt, user_text, bool(previous_response_id)),
        )
    except Exception as e:
        if previous_response_id and _is_previous_id_expired(e) and history_fallback is not None:
            print(f"[ark] (stream) previous_response_id 过期, 用 {len(history_fallback)} 条历史重建链")
            stream = await _start_stream(
                None,
                _build_input_from_history(system_prompt, history_fallback, user_text),
            )
        else:
            raise
    async for event in stream:
        ev_type = getattr(event, "type", "") or ""

        # 拿到正式 id 越早越好, 后续 chunk 的 id 用它对齐
        new_id_to_report: Optional[str] = None
        if not reported_id:
            ev_response = getattr(event, "response", None)
            if ev_response is not None:
                got = getattr(ev_response, "id", "") or ""
                if got:
                    response_id = got
                    new_id_to_report = got
                    reported_id = True

        chunk_id = response_id or fallback_chunk_id

        if ev_type == "response.output_text.delta":
            delta = getattr(event, "delta", "") or ""
            if delta:
                yield _wrap_chat_chunk(delta, model_name, chunk_id), new_id_to_report
                new_id_to_report = None  # 已经传过了
        elif ev_type == "response.completed":
            yield _wrap_chat_chunk("", model_name, chunk_id, finish_reason="stop"), new_id_to_report
        # 其它事件 (created/in_progress/output_item.added/...) 我们不向下游冒泡, 减少噪音
