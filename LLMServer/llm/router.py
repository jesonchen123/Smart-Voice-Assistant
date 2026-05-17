"""
/v1/chat/completions — OpenAI 兼容路由。
- stream=true: 透传上游 SSE 给前端 / RTC Custom LLM。
- stream=false: 一次性 JSON, 便于 curl 调试。
将来 RTC 把 Custom LLM URL 指到这里即可, 无需改契约。
"""

import json
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from llm import ark_client
from rag.pipeline import build_messages

router = APIRouter()


class ChatRequest(BaseModel):
    model: Optional[str] = None
    messages: list[dict]
    stream: bool = True
    temperature: float = 0.3
    max_tokens: Optional[int] = None
    # 自定义扩展
    rag_enabled: bool = True
    top_k: Optional[int] = None


@router.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest):
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
                    # ChatCompletionChunk -> dict -> SSE
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
    # 把召回的 chunks 一并返回, 方便调试 (非流式专用)
    data["_rag_chunks"] = chunks
    return JSONResponse(content=data)


@router.get("/health")
async def health():
    return {"status": "ok"}
