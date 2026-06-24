"""
调试接口: 不调 LLM, 只验证 RAG 链路 (检索 + prompt 拼装)。
快速且零 token 开销, 用来人眼检查召回质量和最终 prompt。
"""

from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

import config
from knowledge_base import viking_kb
from llm import shared_platform_client
from rag.pipeline import build_messages

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/rag")
async def debug_rag(
    q: str = Query(..., description="用户问题"),
    top_k: Optional[int] = Query(None, description="覆盖默认 top_k"),
):
    """
    输入一个 query, 返回:
      - chunks: 知识库召回的片段
      - hit: 是否命中 (至少一条 chunk)
      - final_messages: 拼好的、准备喂给 LLM 的完整 messages
    不会真正调用大模型。
    """
    messages_in = [{"role": "user", "content": q}]
    final_messages, chunks = await build_messages(messages_in, top_k=top_k)
    return JSONResponse(content={
        "query": q,
        "hit": bool(chunks),
        "chunk_count": len(chunks),
        "chunks": chunks,
        "final_messages": final_messages,
    })


@router.get("/search")
async def debug_search(
    q: str = Query(..., description="用户问题"),
    top_k: Optional[int] = Query(None, description="覆盖默认 top_k"),
    collection: Optional[str] = Query(None, description="覆盖默认 collection"),
    raw: bool = Query(False, description="是否返回火山原始响应体, 用于排查零召回"),
):
    """
    只跑知识库检索, 不做 prompt 拼装。
    raw=true 时把火山原始响应一起返回, 用于排查"为什么我有关键词但召回 0 条"。
    """
    if config.SHARED_PLATFORM_ENABLED:
        chunks = await shared_platform_client.search_rag(q, top_k=top_k)
        return JSONResponse(content={
            "query": q,
            "hit": bool(chunks),
            "chunk_count": len(chunks),
            "chunks": chunks,
            "error": None,
            "backend": "mcp_cluster",
        })
    debug = await viking_kb.search_with_debug(q, top_k=top_k, collection_name=collection)
    body = {
        "query": q,
        "hit": bool(debug["chunks"]),
        "chunk_count": len(debug["chunks"]),
        "chunks": debug["chunks"],
        "error": debug["error"],
        "backend": "viking_kb",
    }
    if raw:
        body["request_body"] = debug["request_body"]
        body["raw_response"] = debug["raw"]
    return JSONResponse(content=body)
