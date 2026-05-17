"""
LLMServer 主入口。
独立的 RAG HTTP 服务: /v1/chat/completions (OpenAI 兼容)。
后续接 RTC 时把 Custom LLM URL 指到本服务即可。
"""

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
from debug_routes import router as debug_router
from llm.router import router as llm_router

app = FastAPI(title="LLM RAG Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(llm_router)
app.include_router(debug_router)


@app.get("/")
async def root():
    return {
        "service": "LLM RAG Server",
        "endpoints": {
            "chat": "/v1/chat/completions",
            "debug_rag": "/debug/rag?q=...",
            "debug_search": "/debug/search?q=...",
            "health": "/health",
            "docs": "/docs",
        },
    }


if __name__ == "__main__":
    print(f"LLM RAG Server is running at http://localhost:{config.SERVER_PORT}")
    uvicorn.run(
        "app:app",
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        reload=True,
        log_level=config.LOG_LEVEL.lower(),
    )
