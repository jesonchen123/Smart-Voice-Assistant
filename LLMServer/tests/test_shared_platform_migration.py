from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from llm import ark_client
from llm import shared_platform_client


class SharedPlatformMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_chat_uses_shared_platform_when_enabled(self):
        captured = {}

        async def fake_complete_chat(**kwargs):
            captured.update(kwargs)
            return {
                "id": "llm_req_1",
                "object": "chat.completion",
                "created": 1,
                "model": "doubao",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "共享平台回复"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 3},
            }

        with patch("config.SHARED_PLATFORM_ENABLED", True), patch(
            "llm.shared_platform_client.complete_chat", new=AsyncMock(side_effect=fake_complete_chat)
        ) as shared_call:
            result = await ark_client.complete_chat(
                [{"role": "user", "content": "你好"}],
                temperature=0.2,
                max_tokens=128,
            )

        self.assertEqual(result["choices"][0]["message"]["content"], "共享平台回复")
        shared_call.assert_awaited_once()
        self.assertEqual(captured["messages"][0]["content"], "你好")
        self.assertEqual(captured["temperature"], 0.2)
        self.assertEqual(captured["max_tokens"], 128)

    async def test_responses_complete_replays_history_for_shared_platform_session(self):
        captured = {}

        async def fake_complete_chat(**kwargs):
            captured.update(kwargs)
            return {
                "id": "llm_req_history",
                "object": "chat.completion",
                "created": 1,
                "model": "doubao",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "记得你的名字"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": None,
            }

        with patch("config.SHARED_PLATFORM_ENABLED", True), patch(
            "llm.shared_platform_client.complete_chat", new=AsyncMock(side_effect=fake_complete_chat)
        ):
            result, response_id = await ark_client.responses_complete(
                system_prompt="你是客服助手",
                user_text="我叫什么？",
                previous_response_id="old-response-id",
                history_fallback=[
                    {"role": "user", "content": "我叫小明"},
                    {"role": "assistant", "content": "你好小明"},
                ],
            )

        self.assertEqual(response_id, "llm_req_history")
        self.assertEqual(result["choices"][0]["message"]["content"], "记得你的名字")
        self.assertEqual(captured["messages"][0], {"role": "system", "content": "你是客服助手"})
        self.assertEqual(captured["messages"][1], {"role": "user", "content": "我叫小明"})
        self.assertEqual(captured["messages"][2], {"role": "assistant", "content": "你好小明"})
        self.assertEqual(captured["messages"][3], {"role": "user", "content": "我叫什么？"})

    async def test_stream_chat_wraps_shared_platform_text_as_openai_chunks(self):
        async def fake_stream_text(**kwargs):
            yield "你"
            yield "好"

        with patch("config.SHARED_PLATFORM_ENABLED", True), patch(
            "llm.shared_platform_client.stream_text", fake_stream_text
        ):
            chunks = [
                chunk async for chunk in ark_client.stream_chat(
                    [{"role": "user", "content": "你好"}]
                )
            ]

        self.assertEqual(chunks[0]["object"], "chat.completion.chunk")
        self.assertEqual(chunks[0]["choices"][0]["delta"]["content"], "你")
        self.assertEqual(chunks[1]["choices"][0]["delta"]["content"], "好")
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")


class SharedPlatformClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_chat_uses_model_policy_from_config_center(self):
        with patch(
            "llm.shared_platform_client.get_task_config",
            new=AsyncMock(return_value={"model_policy_id": "policy_from_config"}),
        ), patch(
            "llm.shared_platform_client._post_json",
            new=AsyncMock(
                return_value={
                    "request_id": "llm_req_cfg",
                    "model": "doubao",
                    "content": "ok",
                    "finish_reason": "stop",
                    "usage": {"total_tokens": 1},
                }
            ),
        ) as post_json:
            result = await shared_platform_client.complete_chat(
                messages=[{"role": "user", "content": "hello"}],
                temperature=0.3,
                max_tokens=64,
            )

        self.assertEqual(result["id"], "llm_req_cfg")
        payload = post_json.await_args.args[1]
        self.assertEqual(payload["model_policy_id"], "policy_from_config")

    async def test_search_rag_uses_configured_rag_policy_and_normalizes_results(self):
        with patch(
            "llm.shared_platform_client.get_task_config",
            new=AsyncMock(return_value={"rag_enabled": True, "rag_policy_id": "aigc_voice_kb"}),
        ), patch(
            "llm.shared_platform_client._post_json",
            new=AsyncMock(
                return_value={
                    "results": [
                        {
                            "doc_id": "doc_1",
                            "kb_id": "aigc_voice_kb",
                            "title": "退款政策",
                            "content": "7 天内申请退款",
                            "score": 2,
                            "metadata": {"source": "seed"},
                        }
                    ]
                }
            ),
        ) as post_json:
            results = await shared_platform_client.search_rag("退款", top_k=3)

        payload = post_json.await_args.args[1]
        self.assertEqual(payload["kb_ids"], ["aigc_voice_kb"])
        self.assertEqual(results[0]["text"], "7 天内申请退款")
        self.assertEqual(results[0]["source"], "退款政策")


if __name__ == "__main__":
    unittest.main()
