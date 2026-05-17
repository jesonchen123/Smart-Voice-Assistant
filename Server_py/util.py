"""
Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
SPDX-license-identifier: BSD-3-Clause
"""

import json
import os


def read_files(dir_path: str, suffix: str) -> dict:
    """读取目录下所有指定后缀的 JSON 文件，返回 {文件名: JSON内容} 的字典。"""
    scenes = {}
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), dir_path)
    if not os.path.isdir(base):
        return scenes
    for name in os.listdir(base):
        if not name.endswith(suffix):
            continue
        with open(os.path.join(base, name), 'r', encoding='utf-8') as f:
            scenes[name[:-len(suffix)]] = json.load(f)
    return scenes


class AssertError(Exception):
    pass


def assert_(expression, msg: str):
    """与 Node 端 util.js#assert 对齐: 当值为假或字符串含空格时抛错。"""
    if not expression or (isinstance(expression, str) and ' ' in expression):
        print(f"\x1b[31m校验失败: {msg}\x1b[0m")
        raise AssertError(msg)


def wrapped_response(api_name: str, logic, contain_response_metadata: bool = True):
    """同步版本的 wrapper: 包装 logic 执行，捕获异常并返回标准结构。"""
    response_metadata = {"Action": api_name}
    try:
        res = logic()
        if contain_response_metadata:
            return {
                "ResponseMetadata": response_metadata,
                "Result": res,
            }
        return res
    except Exception as e:
        response_metadata["Error"] = {
            "Code": -1,
            "Message": str(e),
        }
        return {"ResponseMetadata": response_metadata}


async def wrapped_response_async(api_name: str, logic, contain_response_metadata: bool = True):
    """异步版本: 支持 awaitable logic, 用于 FastAPI 路由。"""
    response_metadata = {"Action": api_name}
    try:
        res = await logic()
        if contain_response_metadata:
            return {
                "ResponseMetadata": response_metadata,
                "Result": res,
            }
        return res
    except Exception as e:
        response_metadata["Error"] = {
            "Code": -1,
            "Message": str(e),
        }
        return {"ResponseMetadata": response_metadata}
