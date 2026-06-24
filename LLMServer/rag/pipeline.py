"""
RAG 编排: 取用户最后一句 → 调火山知识库检索 → 拼 prompt。
设计为无状态的纯函数, 不维护会话。
"""

from typing import Optional

import config
from knowledge_base import viking_kb
from rag.prompt import SYSTEM_NO_CONTEXT, SYSTEM_WITH_CONTEXT, format_context
from llm import shared_platform_client


def extract_last_user_query(messages: list[dict]) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user":
            content = m.get("content")
            # 兼容 OpenAI 的 content list 格式
            if isinstance(content, list):
                parts = []
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        parts.append(p.get("text", ""))
                return "".join(parts).strip()
            return (content or "").strip()
    return ""


async def build_messages(
    messages: list[dict],
    top_k: Optional[int] = None,
) -> tuple[list[dict], list[dict]]:
    """
    返回 (new_messages, retrieved_chunks)。
    检索失败或零结果时, 自动回退到"未检索到资料"分支。
    """
    query = extract_last_user_query(messages)
    chunks: list[dict] = []
    if query:
        if config.SHARED_PLATFORM_ENABLED:
            chunks = await shared_platform_client.search_rag(query, top_k=top_k)
        else:
            chunks = await viking_kb.search(query, top_k=top_k)

    if chunks:
        context = format_context(chunks)
        if config.SHARED_PLATFORM_ENABLED:
            system_content = await shared_platform_client.render_prompt(
                "aigc.voice.rag.with_context",
                variables={"context": context},
            )
        else:
            system_content = SYSTEM_WITH_CONTEXT.format(context=context)
    else:
        if config.SHARED_PLATFORM_ENABLED:
            system_content = await shared_platform_client.render_prompt("aigc.voice.rag.no_context")
        else:
            system_content = SYSTEM_NO_CONTEXT

    # 去掉用户原本的 system, 用我们自己的 system 替换
    rest = [m for m in messages if m.get("role") != "system"]
    new_messages = [{"role": "system", "content": system_content}, *rest]
    return new_messages, chunks
