# LLM RAG Server

独立的 RAG HTTP 服务，对接火山知识库 + 豆包 Ark。
接口 OpenAI 兼容 (`/v1/chat/completions`)，将来 RTC Custom LLM 直接把 URL 指过来即可。

## 架构

```
user query
   │
   ▼
POST /v1/chat/completions
   ├─ 取最后一条 user message
   ├─ 调火山知识库 SearchKnowledge (top_k=4)
   ├─ 拼 system prompt
   │     - 命中: system + 检索片段 + 用户问题
   │     - 零结果: 让模型诚实回复 + 提示咨询人工
   └─ 调豆包 Ark Chat 流式 ──▶ SSE
```

## 目录结构

```
LLMServer/
├── app.py                          # FastAPI 入口
├── config.py                       # 读 .env, 集中所有占位符
├── .env.example                    # 配置模板, 复制为 .env 后填写
├── requirements.txt
├── llm/
│   ├── router.py                   # /v1/chat/completions (SSE)
│   └── ark_client.py               # 豆包 Ark 流式 (走 openai SDK)
├── knowledge_base/
│   └── viking_kb.py                # 火山知识库 SearchKnowledge, 含 V4 签名
└── rag/
    ├── pipeline.py                 # 编排: 取 query → 检索 → 拼 prompt
    └── prompt.py                   # 中文 system 模板 (含零结果分支)
```

## 启动

```bash
cd LLMServer
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 编辑 .env, 填入 ARK_API_KEY / ARK_CHAT_ENDPOINT_ID
# 以及 VIKING_KB_AK / VIKING_KB_SK / VIKING_KB_COLLECTION_NAME

python app.py
# 或: uvicorn app:app --host 0.0.0.0 --port 3002 --reload
```

启动后:
- 服务: http://localhost:3002
- Swagger 文档: http://localhost:3002/docs
- 健康检查: http://localhost:3002/health

## 接口契约

`POST /v1/chat/completions` (OpenAI 兼容 + 自定义扩展)

```json
{
  "model": "doubao",
  "messages": [
    {"role": "user", "content": "我想了解产品的退款政策"}
  ],
  "stream": true,
  "temperature": 0.7,
  "rag_enabled": true,
  "top_k": 4
}
```

- `stream=true`: SSE, 标准 OpenAI 格式 (`data: {...}\n\n` + `data: [DONE]\n\n`)
- `stream=false`: 一次性返回 JSON, 额外字段 `_rag_chunks` 列出召回的片段，方便调试

## 调试接口 (不调 LLM, 零 token 开销)

用于验证"检索到的内容是否正确拼进了 prompt"。

```bash
# 看完整链路: 检索 + prompt 拼装
curl "http://localhost:3002/debug/rag?q=你们的退款政策%E6%98%AF%E6%80%8E%E6%A0%B7%E7%9A%84"

# 只看检索结果, 不做 prompt 拼装
curl "http://localhost:3002/debug/search?q=退款政策&top_k=3"
```

`/debug/rag` 返回:
```json
{
  "query": "...",
  "hit": true,
  "chunk_count": 3,
  "chunks": [{"text": "...", "score": 0.82, "source": "..."}],
  "final_messages": [
    {"role": "system", "content": "你是企业智能客服助手...【参考资料】[1] ..."},
    {"role": "user",   "content": "..."}
  ]
}
```

零结果时 `hit=false`, `final_messages` 里 system 走"未检索到资料"分支, 一眼能看出来。

## 快速验证

```bash
# 非流式
curl -X POST http://localhost:3002/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages":[{"role":"user","content":"你们的退款政策是怎样的?"}],
    "stream": false
  }'

# 流式
curl -N -X POST http://localhost:3002/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages":[{"role":"user","content":"你们的退款政策是怎样的?"}],
    "stream": true
  }'

# 关闭 RAG, 验证纯 LLM
curl -X POST http://localhost:3002/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages":[{"role":"user","content":"你好"}],
    "stream": false,
    "rag_enabled": false
  }'
```

## 配置说明 (`.env`)

| 变量 | 必填 | 说明 |
|------|------|------|
| `ARK_API_KEY` | ✅ | 豆包 Ark API Key |
| `ARK_CHAT_ENDPOINT_ID` | ✅ | 豆包 Chat 推理接入点 ID (形如 `ep-xxx`) |
| `ARK_BASE_URL` | ⬜ | 默认北京区, 如换 region 可改 |
| `VIKING_KB_AK` | ✅ | 火山账号 AccessKey |
| `VIKING_KB_SK` | ✅ | 火山账号 SecretKey |
| `VIKING_KB_HOST` | ⬜ | 知识库 API 域名 |
| `VIKING_KB_REGION` | ⬜ | 签名 region, 默认 `cn-north-1` (注意与 host 里的 `cn-beijing` 不一致, 这是官方约定) |
| `VIKING_KB_SERVICE` | ⬜ | 签名 service 名, 默认 `air` |
| `VIKING_KB_COLLECTION_NAME` | ✅ | 知识库集合名 |
| `VIKING_KB_PROJECT` | ⬜ | 项目名, 默认 `default` |
| `VIKING_KB_TOP_K` | ⬜ | 检索条数, 默认 `4` |
| `VIKING_KB_SEARCH_PATH` | ⬜ | 检索接口路径, 按实际文档调整 |
| `SERVER_PORT` | ⬜ | 监听端口, 默认 `3002` |

## 创建知识库 (一次性)

如果还没在火山控制台建过 collection, 配好 AK/SK 后直接命令行建:

```bash
python -m scripts.create_collection lzm_test2
# 或自定义参数
python -m scripts.create_collection my_kb --dim 2048 --model doubao-embedding-and-m3
```

文档入库 / 切分等仍走火山知识库控制台或它自己的文档管理 API, 本服务不负责。

## 设计说明 / 当前限制

- **无状态**: 不维护会话历史。每次请求独立检索, 由调用方 (前端或 RTC) 在 `messages` 里传上下文。
- **零结果策略**: 检索为空时不直接拒绝, 仍调 LLM 但 system prompt 明确告知"未检索到资料"+"建议咨询人工", 降低幻觉风险。
- **检索失败兜底**: 知识库 HTTP/签名/解析任一环节失败, 都视为零结果继续走 LLM, 不阻塞回答 (会在控制台打印错误)。
- **文档入库**: 完全交给火山知识库控制台 / 它自己的文档管理 API, 本服务不负责。
- **未实现**: RTC 集成、多轮 session 缓存、rerank、鉴权。需要时再加。

## 接入 RTC (后续)

把 [`../Server/scenes/Custom.json`](../Server/scenes/Custom.json) 中的 `LLMConfig` 改为火山 RTC 的 Custom LLM 模式 (具体字段以火山文档为准), URL 填到本服务的 `/v1/chat/completions`, 即可让语音通话走 RAG 链路, 无需改本服务一行代码。
