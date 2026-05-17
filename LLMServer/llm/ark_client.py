"""
豆包 Ark Chat 流式调用封装。
Ark 提供 OpenAI 兼容接口，所以直接用 openai SDK 指向 Ark base URL 即可，
省去自己处理 SSE 的工作量。
"""

from typing import AsyncIterator, Optional

from openai import AsyncOpenAI

import config

_client: Optional[AsyncOpenAI] = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        config.assert_filled("ARK_API_KEY", config.ARK_API_KEY)
        _client = AsyncOpenAI(
            api_key=config.ARK_API_KEY,
            base_url=config.ARK_BASE_URL,
        )
    return _client


async def stream_chat(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
) -> AsyncIterator[dict]:
    """
    返回 openai SDK 的 ChatCompletionChunk 异步迭代器。
    上层 router 负责把它转成 SSE。
    """
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
    """非流式调用，用于调试 / curl 验证。"""
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
