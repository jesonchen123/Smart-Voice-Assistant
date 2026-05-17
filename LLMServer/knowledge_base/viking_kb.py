"""
火山知识库 API 封装。
使用官方 volcengine SDK 的 SignerV4 做签名, 上层走 httpx 异步发请求,
参考: 火山知识库 OpenAPI 官方示例 (Credentials(ak, sk, "air", "cn-north-1"))
"""

import json
from typing import Optional

import httpx
from volcengine.auth.SignerV4 import SignerV4
from volcengine.base.Request import Request
from volcengine.Credentials import Credentials

import config


# ---------------- 内部: 构造已签名的 Request ----------------

def _prepare_request(
    method: str,
    path: str,
    params: Optional[dict] = None,
    data: Optional[dict] = None,
) -> Request:
    config.assert_filled("VIKING_KB_AK", config.VIKING_KB_AK)
    config.assert_filled("VIKING_KB_SK", config.VIKING_KB_SK)

    # params 值需要标量化, 否则签名结果对不齐
    if params:
        for k in list(params.keys()):
            v = params[k]
            if isinstance(v, (int, float, bool)):
                params[k] = str(v)
            elif isinstance(v, list):
                params[k] = ",".join(map(str, v))

    r = Request()
    r.set_shema("http")  # 官方示例如此; 实际外发仍走 https
    r.set_method(method)
    r.set_connection_timeout(10)
    r.set_socket_timeout(10)
    r.set_headers({
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Host": config.VIKING_KB_HOST,
    })
    if params:
        r.set_query(params)
    r.set_host(config.VIKING_KB_HOST)
    r.set_path(path)
    if data is not None:
        r.set_body(json.dumps(data, ensure_ascii=False))

    credentials = Credentials(
        config.VIKING_KB_AK,
        config.VIKING_KB_SK,
        config.VIKING_KB_SERVICE,
        config.VIKING_KB_REGION,
    )
    SignerV4.sign(r, credentials)
    return r


async def _call(
    method: str,
    path: str,
    data: Optional[dict] = None,
    params: Optional[dict] = None,
) -> dict:
    req = _prepare_request(method, path, params=params, data=data)
    url = f"https://{config.VIKING_KB_HOST}{req.path}"
    body = req.body
    if isinstance(body, str):
        body = body.encode("utf-8")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.request(
            method=req.method,
            url=url,
            headers=req.headers,
            content=body,
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"viking_kb HTTP {resp.status_code}: {resp.text[:500]}"
        )
    return resp.json()


# ---------------- 响应归一化 ----------------

def _normalize_chunks(raw: dict) -> list[dict]:
    """
    把火山知识库返回的 data 归一化为 [{text, score, doc_id, source}]。
    遇到陌生字段命名时, 在此扩展兜底分支即可。
    """
    result = []
    data = raw.get("data") or {}
    items = (
        data.get("result_list")
        or data.get("points")
        or data.get("chunks")
        or data.get("items")
        or []
    )
    for it in items:
        if not isinstance(it, dict):
            continue
        text = (
            it.get("content")
            or it.get("text")
            or it.get("chunk_text")
            or (it.get("doc_info") or {}).get("content")
            or ""
        )
        if not text:
            continue
        result.append({
            "text": text,
            "score": it.get("score") or it.get("similarity") or 0,
            "doc_id": (
                it.get("doc_id")
                or it.get("document_id")
                or (it.get("doc_info") or {}).get("doc_id")
                or ""
            ),
            "source": (
                it.get("source")
                or (it.get("doc_info") or {}).get("doc_name")
                or (it.get("doc_info") or {}).get("source")
                or ""
            ),
        })
    return result


# ---------------- 业务 API ----------------

async def search_with_debug(
    query: str,
    top_k: Optional[int] = None,
    collection_name: Optional[str] = None,
) -> dict:
    """
    内部调试用: 返回 {chunks, raw, request_body, error}, 方便排查零召回原因。
    """
    name = collection_name or config.VIKING_KB_COLLECTION_NAME
    config.assert_filled("VIKING_KB_COLLECTION_NAME", name)
    body = {
        "name": name,
        "query": query,
        "limit": top_k or config.VIKING_KB_TOP_K,
    }
    try:
        raw = await _call("POST", config.VIKING_KB_SEARCH_PATH, data=body)
        biz_err = None
        if raw.get("code") not in (None, 0, "0", "Success"):
            biz_err = f"biz error: {raw}"
            print(f"[viking_kb] {biz_err}")
        chunks = _normalize_chunks(raw)
        if not chunks and raw.get("data"):
            print(
                f"[viking_kb] 0 chunks normalized but raw data is non-empty, "
                f"check response schema: keys={list((raw.get('data') or {}).keys())}"
            )
        return {
            "chunks": chunks,
            "raw": raw,
            "request_body": body,
            "error": biz_err,
        }
    except Exception as e:
        print(f"[viking_kb] search failed: {e}")
        return {
            "chunks": [],
            "raw": None,
            "request_body": body,
            "error": f"{type(e).__name__}: {e}",
        }


async def search(
    query: str,
    top_k: Optional[int] = None,
    collection_name: Optional[str] = None,
) -> list[dict]:
    """业务用: 只返回 chunks, 失败静默返 []。"""
    debug = await search_with_debug(query, top_k=top_k, collection_name=collection_name)
    return debug["chunks"]


async def create_collection(
    name: str,
    embedding_model: str = "doubao-embedding-and-m3",
    embedding_dimension: int = 2048,
    cpu_quota: int = 1,
    chunking_strategy: str = "custom_balance",
    multi_modal: Optional[list] = None,
    quant: str = "int8",
    index_type: str = "hnsw_hybrid",
) -> dict:
    """
    创建知识库 collection。一次性管理操作, 不在 RAG 请求路径上。
    默认参数对齐官方文档示例。
    """
    body = {
        "name": name,
        "data_type": "unstructured_data",
        "preprocessing": {
            "chunking_strategy": chunking_strategy,
            "multi_modal": multi_modal or ["image_ocr"],
        },
        "index": {
            "cpu_quota": cpu_quota,
            "embedding_model": embedding_model,
            "embedding_dimension": embedding_dimension,
            "quant": quant,
            "index_type": index_type,
        },
    }
    return await _call("POST", "/api/knowledge/collection/create", data=body)
