"""
一次性创建火山知识库 collection 的 CLI 脚本。
用法:
    python -m scripts.create_collection <collection_name>
    python -m scripts.create_collection my_kb --dim 2048 --model doubao-embedding-and-m3

需先在 LLMServer/.env 配好 VIKING_KB_AK / VIKING_KB_SK。
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledge_base import viking_kb  # noqa: E402


async def main():
    parser = argparse.ArgumentParser(description="创建火山知识库 collection")
    parser.add_argument("name", help="collection 名称")
    parser.add_argument("--model", default="doubao-embedding-and-m3", help="embedding 模型")
    parser.add_argument("--dim", type=int, default=2048, help="embedding 维度")
    parser.add_argument("--cpu-quota", type=int, default=1)
    parser.add_argument("--index-type", default="hnsw_hybrid")
    parser.add_argument("--quant", default="int8")
    args = parser.parse_args()

    print(f"创建 collection: {args.name} (model={args.model}, dim={args.dim})")
    resp = await viking_kb.create_collection(
        name=args.name,
        embedding_model=args.model,
        embedding_dimension=args.dim,
        cpu_quota=args.cpu_quota,
        quant=args.quant,
        index_type=args.index_type,
    )
    print("响应:", resp)


if __name__ == "__main__":
    asyncio.run(main())
