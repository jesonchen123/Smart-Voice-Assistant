"""
LLM 路由 (OpenAI 兼容 + Responses API session 扩展 + 对话持久化)。

- POST /v1/context/create
    在 SessionManager 里分配 sid, 把基础人设写入, 同时建 DB 行。返回 context_id。
- POST /v1/chat/completions
    带 context_id 走 Responses API + previous_response_id 串链,
    每轮成功后 user/assistant 写入 messages 表。链过期时自动用 DB 历史重建。
- GET    /v1/conversations              列表
- GET    /v1/conversations/{sid}        单会话全部消息
- DELETE /v1/conversations/{sid}        删除会话和消息
"""

import json
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

import config
from llm import ark_client
from llm import shared_platform_client
from llm.session_manager import get_manager
from rag.pipeline import build_messages, extract_last_user_query
from rag.prompt import SYSTEM_BASE, prepend_context_to_user
from knowledge_base import viking_kb
from storage import sqlite as storage

router = APIRouter()


class ChatRequest(BaseModel):
    model: Optional[str] = None
    messages: list[dict]
    stream: bool = False
    temperature: float = 0.3
    max_tokens: Optional[int] = None
    rag_enabled: bool = True
    top_k: Optional[int] = 2
    context_id: Optional[str] = None


class CreateContextRequest(BaseModel):
    system_prompt: Optional[str] = None


@router.post("/v1/context/create")
async def create_context(req: CreateContextRequest):
    if req.system_prompt:
        system_prompt = req.system_prompt.strip()
    elif config.SHARED_PLATFORM_ENABLED:
        system_prompt = (await shared_platform_client.render_prompt("aigc.voice.persona.default")).strip()
    else:
        system_prompt = SYSTEM_BASE.strip()
    sid = await get_manager().new_session(system_prompt)
    return JSONResponse(content={
        "context_id": sid,
        "system_prompt_preview": system_prompt[:60] + ("..." if len(system_prompt) > 60 else ""),
    })


# ============== 历史查询接口 ==============

@router.get("/v1/conversations")
async def list_conversations(limit: int = Query(100, ge=1, le=500)):
    rows = await storage.list_conversations(limit=limit)
    return JSONResponse(content={"conversations": rows})


@router.get("/v1/conversations/{sid}")
async def get_conversation(sid: str):
    conv = await storage.get_conversation(sid)
    if conv is None:
        return JSONResponse(
            status_code=404,
            content={"error": {"message": f"会话 {sid} 不存在", "type": "NotFound"}},
        )
    messages = await storage.list_messages(sid)
    return JSONResponse(content={"conversation": conv, "messages": messages})


@router.delete("/v1/conversations/{sid}")
async def delete_conversation(sid: str):
    await get_manager().drop(sid)  # 内存 + DB 一起清
    return JSONResponse(content={"ok": True})


# ============== 对话 ==============

@router.post("/v1/chat/completions")
async def chat_completions(
    req: ChatRequest,
    context_id: Optional[str] = Query(None, description="也可走 URL ?context_id=...; body 优先"),
):
    effective_ctx = req.context_id or context_id
    if effective_ctx:
        req.context_id = effective_ctx
        return await _chat_with_session(req)

    # ============== 无状态分支 ==============
    if req.rag_enabled:
        messages, chunks = await build_messages(req.messages, top_k=req.top_k)
    else:
        messages, chunks = req.messages, []

    if req.stream:
        async def event_stream():
            try:
                async for chunk in ark_client.stream_chat(
                    messages,
                    temperature=req.temperature,
                    max_tokens=req.max_tokens,
                ):
                    if isinstance(chunk, dict):
                        data = chunk
                    else:
                        data = chunk.model_dump(exclude_none=True)
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                err = {"error": {"message": str(e), "type": e.__class__.__name__}}
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        data = await ark_client.complete_chat(
            messages,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": {"message": str(e), "type": e.__class__.__name__}},
        )
    data["_rag_chunks"] = chunks
    return JSONResponse(content=data)


async def _persist_turn(sid: str, user_text: str, assistant_text: str, new_response_id: str) -> None:
    """一轮成功后写库: user 一行, assistant 一行, 顺手回填 title (仅当还是默认)。"""
    if user_text:
        await storage.insert_message(sid, "user", user_text)
        # 首条用户消息当 title
        if (await storage.count_messages(sid)) <= 2:
            await storage.update_title_if_default(sid, user_text)
    if assistant_text:
        await storage.insert_message(sid, "assistant", assistant_text, response_id=new_response_id)


async def _chat_with_session(req: ChatRequest):
    sid = req.context_id or ""
    st = await get_manager().get(sid)
    if st is None:
        return JSONResponse(
            status_code=404,
            content={"error": {
                "message": f"context_id {sid} 不存在或已过期, 请重新调用 /v1/context/create",
                "type": "SessionNotFound",
            }},
        )

    user_text = extract_last_user_query(req.messages)
    if not user_text:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "messages 中找不到 user 消息", "type": "BadRequest"}},
        )

    chunks: list[dict] = []
    if req.rag_enabled:
        if config.SHARED_PLATFORM_ENABLED:
            chunks = await shared_platform_client.search_rag(user_text, top_k=req.top_k)
        else:
            chunks = await viking_kb.search(user_text, top_k=req.top_k)
    final_user = prepend_context_to_user(user_text, chunks)

    # 链过期兜底用: 把 DB 里历史拉出来 (顺序: 旧 -> 新)
    history_rows = await storage.list_messages(sid)
    history_fallback = [{"role": r["role"], "content": r["content"]} for r in history_rows]

    if req.stream:
        prev_id_snapshot = st.latest_response_id

        async def event_stream():
            assistant_buf: list[str] = []
            saw_new_id = ""
            try:
                async for chunk, maybe_new_id in ark_client.responses_stream(
                    system_prompt=st.system_prompt,
                    user_text=final_user,
                    previous_response_id=prev_id_snapshot or None,
                    temperature=req.temperature,
                    max_tokens=req.max_tokens,
                    history_fallback=history_fallback,
                ):
                    if maybe_new_id:
                        saw_new_id = maybe_new_id
                        await get_manager().update_latest(sid, maybe_new_id)
                    # 累积 assistant 文本用于落库
                    delta = (chunk.get("choices") or [{}])[0].get("delta", {}).get("content")
                    if delta:
                        assistant_buf.append(delta)
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                # 流结束后落库 (user 用原始 user_text, 剥掉 RAG 前缀)
                await _persist_turn(sid, user_text, "".join(assistant_buf), saw_new_id)
            except Exception as e:
                err = {"error": {"message": str(e), "type": e.__class__.__name__}}
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    prev_id_snapshot = st.latest_response_id
    try:
        data, new_id = await ark_client.responses_complete(
            system_prompt=st.system_prompt,
            user_text=final_user,
            previous_response_id=prev_id_snapshot or None,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            history_fallback=history_fallback,
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": {"message": str(e), "type": e.__class__.__name__}},
        )
    if new_id:
        await get_manager().update_latest(sid, new_id)
    assistant_text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    await _persist_turn(sid, user_text, assistant_text, new_id)

    data["_rag_chunks"] = chunks
    data["_previous_response_id"] = prev_id_snapshot
    data["_response_id"] = new_id
    return JSONResponse(content=data)


@router.get("/health")
async def health():
    return {"status": "ok"}
