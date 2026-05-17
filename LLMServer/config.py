"""
集中配置入口。所有密钥和 endpoint 通过 .env 注入，代码中不出现真实值。
启动时不强校验占位符，调用相应能力时才会抛出明确错误。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


# ---------------- 豆包 Ark ----------------
ARK_API_KEY = _get("ARK_API_KEY")
ARK_CHAT_ENDPOINT_ID = _get("ARK_CHAT_ENDPOINT_ID")
ARK_BASE_URL = _get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")

# ---------------- 火山知识库 ----------------
VIKING_KB_AK = _get("VIKING_KB_AK")
VIKING_KB_SK = _get("VIKING_KB_SK")
VIKING_KB_HOST = _get("VIKING_KB_HOST", "api-knowledgebase.mlp.cn-beijing.volces.com")
VIKING_KB_REGION = _get("VIKING_KB_REGION", "cn-north-1")
VIKING_KB_SERVICE = _get("VIKING_KB_SERVICE", "air")
VIKING_KB_COLLECTION_NAME = _get("VIKING_KB_COLLECTION_NAME")
VIKING_KB_PROJECT = _get("VIKING_KB_PROJECT", "default")
VIKING_KB_TOP_K = int(_get("VIKING_KB_TOP_K", "4"))
VIKING_KB_SEARCH_PATH = _get(
    "VIKING_KB_SEARCH_PATH", "/api/knowledge/collection/search_knowledge"
)

# ---------------- 服务 ----------------
SERVER_HOST = _get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(_get("SERVER_PORT", "3002"))
LOG_LEVEL = _get("LOG_LEVEL", "INFO")


PLACEHOLDER_PREFIXES = ("", "your_", "<", "ep-xxxx", "ep-xxxxx")


def is_placeholder(value: str) -> bool:
    """判断是否仍为占位符 / 未填值"""
    if not value:
        return True
    low = value.lower()
    return any(low.startswith(p) for p in PLACEHOLDER_PREFIXES if p)


def assert_filled(name: str, value: str) -> None:
    if is_placeholder(value):
        raise RuntimeError(
            f"配置项 {name} 尚未填写，请在 LLMServer/.env 中设置后重试。"
        )
