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
# 或: uvicorn app:app --host 0.0.0.0 --port 3000 --reload
```

启动后:
- 服务: http://localhost:3000
- Swagger 文档: http://localhost:3000/docs
- 健康检查: http://localhost:3000/health

## 接口契约

### 1) 创建会话上下文 (session 模式)

`POST /v1/context/create` — 在服务端 SessionManager 里分配一个 sid, 存好基础人设
(system prompt), 返回给前端 / RTC 持久化。本接口**不会**调用 Ark,
真正的 Ark Responses API 是在每轮 `/v1/chat/completions` 时调的。

```json
// 请求 (system_prompt 可省略, 不传用内置面试助手人设)
{ "system_prompt": "你是李雷, 你只会说我是李雷" }

// 响应
{ "context_id": "sess-xxxxxxxxxxxxxxxx", "system_prompt_preview": "..." }
```

### 2) 对话

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
  "top_k": 4,
  "context_id": "ctx-xxx"   // ← 可选, 带上即走 session 模式
}
```

- **带 `context_id`** (推荐): 历史由火山服务端缓存; 服务端只取 `messages` 中最后一条 user 消息, 跑 RAG, 把召回片段拼到这条 user 前再发, system 沿用 context 创建时的人设。
- **不带 `context_id`** (兼容旧调用): 无状态, 历史由调用方在 `messages` 里拼; system 由服务端按 RAG 命中情况动态生成。
- `stream=true`: SSE, 标准 OpenAI 格式 (`data: {...}\n\n` + `data: [DONE]\n\n`)
- `stream=false`: 一次性返回 JSON, 额外字段 `_rag_chunks` 列出召回的片段，方便调试

## 调试接口 (不调 LLM, 零 token 开销)

用于验证"检索到的内容是否正确拼进了 prompt"。

```bash
# 看完整链路: 检索 + prompt 拼装
curl "http://localhost:3000/debug/rag?q=你们的退款政策%E6%98%AF%E6%80%8E%E6%A0%B7%E7%9A%84"

# 只看检索结果, 不做 prompt 拼装
curl "http://localhost:3000/debug/search?q=退款政策&top_k=3"
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
# 非流式 (无 session)
curl -X POST http://localhost:3000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages":[{"role":"user","content":"你们的退款政策是怎样的?"}],
    "stream": false
  }'

# 流式 (无 session)
curl -N -X POST http://localhost:3000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages":[{"role":"user","content":"你们的退款政策是怎样的?"}],
    "stream": true
  }'

# 关闭 RAG, 验证纯 LLM
curl -X POST http://localhost:3000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages":[{"role":"user","content":"你好"}],
    "stream": false,
    "rag_enabled": false
  }'

# ============== session 模式 ==============

# 1. 先建一个会话, 拿 context_id
curl -X POST http://localhost:3000/v1/context/create \
  -H "Content-Type: application/json" -d '{}'
# => {"context_id": "ctx-xxxxxxxxxxxx", ...}

# 2. 用 context_id 连续问几轮, 不需要自己拼历史
CTX=ctx-xxxxxxxxxxxx
curl -X POST http://localhost:3000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"context_id\":\"$CTX\",\"messages\":[{\"role\":\"user\",\"content\":\"我叫小明\"}],\"stream\":false,\"rag_enabled\":false}"

curl -X POST http://localhost:3000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"context_id\":\"$CTX\",\"messages\":[{\"role\":\"user\",\"content\":\"我刚才说我叫什么?\"}],\"stream\":false,\"rag_enabled\":false}"
```
## 使用ngrok将本地localhost:3002映射到公网可以访问的地址
```bash
ngrok http 3002
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
| `SERVER_PORT` | ⬜ | 监听端口, 默认 `3000` |

### MCP_Cluster 共享平台调度

| 变量 | 默认值 | 说明 |
|------|------|------|
| `SHARED_PLATFORM_ENABLED` | `false` | 开启后，LLMServer 内部模型调用改走 MCP_Cluster，外部 `/v1/chat/completions` 入口不变 |
| `SHARED_PLATFORM_BASE_URL` | `http://localhost:8400` | MCP_Cluster 共享平台地址；本地建议固定 8400，避免和其它 backend_api 的 8000 冲突 |
| `SHARED_PLATFORM_PROJECT_ID` | `aigc_rtc` | 共享平台项目 ID |
| `SHARED_PLATFORM_TASK_TYPE` / `SHARED_PLATFORM_MODEL_POLICY_ID` | `voice_dialogue` / `voice_low_latency` | 对应共享平台 seed 中的智能客服模型策略 |

## 创建知识库 (一次性)

如果还没在火山控制台建过 collection, 配好 AK/SK 后直接命令行建:

```bash
python -m scripts.create_collection lzm_test2
# 或自定义参数
python -m scripts.create_collection my_kb --dim 2048 --model doubao-embedding-and-m3
```

文档入库 / 切分等仍走火山知识库控制台或它自己的文档管理 API, 本服务不负责。

## 设计说明 / 当前限制

- **两种历史模式**:
  - *session 模式 (推荐)*: 前端先调 `/v1/context/create` 拿 `context_id` 存住, 之后每轮 `/v1/chat/completions` 带上 id, 历史由火山 **Responses API** 在云端缓存。
  - *无状态模式 (兼容)*: 不带 `context_id`, 历史由调用方在 `messages` 数组里逐轮自传, 走标准 chat.completions。
- **底层为什么是 Responses API 而不是 Context API**: 火山 Context API 仅 Pro 系列模型支持, Lite/Seed 系列报 `truncation_strategy.type` 不支持。Responses API 用 `previous_response_id` 串链, 模型兼容性更广。代价是每轮 Ark 都返回新 id, 所以服务端必须维护 `sid → latest_response_id` 的映射 (见 [`llm/session_manager.py`](llm/session_manager.py))。
- **响应格式统一**: 内部用的 Responses API 输出结构跟 Chat Completions 不一样, 但出口处统一包成 OpenAI Chat Completions 格式 (含流式 chunk 翻译), 让 RTC / 前端调用方无感。
- **RAG 与 session 共存**: session 模式下 system prompt 在 create 时一次性写入 Manager (首轮调用时随 input 一起发); 每轮新召回的 chunks 以"参考资料"前缀拼到本轮 user 消息里, 既不污染人设也能让模型基于最新资料作答。
- **零结果策略**: 检索为空时不直接拒绝, 仍调 LLM 但 system / user prompt 提示"未检索到资料", 降低幻觉风险。
- **检索失败兜底**: 知识库 HTTP/签名/解析任一环节失败, 都视为零结果继续走 LLM, 不阻塞回答 (会在控制台打印错误)。
- **session 存储**: 进程内字典 + asyncio.Lock, **服务重启会丢失所有会话**, 前端拿到 404 时需要重新调 `/v1/context/create`。生产环境改 redis/sqlite 即可。
- **未实现**: rerank、鉴权、session TTL 清理。

## 接入 RTC (session 已自动化)

`Server_py/scenes/Custom.json` 的 `LLMConfig.Url` 指向本服务 `/v1/chat/completions` 即可让语音通话走 RAG 链路。

session 缓存的接入已经在 `Server_py` 里自动化了:

1. 用户点 "开始通话" → 前端调 `Server_py /proxy?Action=StartVoiceChat`。
2. `Server_py` 在转发给 RTC OpenAPI 前, 自动调本服务 `/v1/context/create` 拿到 `context_id`,
   并把它拼到 `LLMConfig.Url` 的 query string 上 (`...?context_id=ctx-xxx`)。
3. RTC 之后调本服务时 URL 自带 `context_id`, 本服务从 query 读到后走 session 分支。
4. 挂断重拨 → `Server_py` 再建一个新的, 上一通的记忆不会串到下一通。

`Server_py` 需要知道本服务的内网地址, 默认 `http://localhost:3000`,
通过环境变量 `LLM_SERVER_INTERNAL_URL` 可覆盖 (注意: 这里要填**内网**地址,
而 `LLMConfig.Url` 里仍是 RTC 服务端能访问的公网 ngrok 地址)。

如果 `/v1/context/create` 调用失败, `Server_py` 会**降级**为不注入 context_id,
本服务自动回退到 stateless 模式, 不阻塞通话。
