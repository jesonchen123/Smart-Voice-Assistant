from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from rag.pipeline import build_messages


class SharedPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_messages_uses_shared_rag_and_prompt_when_enabled(self):
        rag_results = [{"text": "退款政策支持 7 天内申请", "source": "退款政策"}]

        async def fake_render(prompt_key, **kwargs):
            self.assertEqual(prompt_key, "aigc.voice.rag.with_context")
            self.assertIn("退款政策支持", kwargs["variables"]["context"])
            return "共享 RAG 命中 Prompt"

        with patch("config.SHARED_PLATFORM_ENABLED", True), patch(
            "llm.shared_platform_client.search_rag", new=AsyncMock(return_value=rag_results)
        ) as search_rag, patch(
            "llm.shared_platform_client.render_prompt", new=AsyncMock(side_effect=fake_render)
        ) as render_prompt:
            messages, chunks = await build_messages(
                [{"role": "system", "content": "old"}, {"role": "user", "content": "退款政策"}],
                top_k=2,
            )

        search_rag.assert_awaited_once_with("退款政策", top_k=2)
        render_prompt.assert_awaited_once()
        self.assertEqual(chunks, rag_results)
        self.assertEqual(messages[0], {"role": "system", "content": "共享 RAG 命中 Prompt"})
        self.assertEqual(messages[1], {"role": "user", "content": "退款政策"})

    async def test_build_messages_uses_shared_no_context_prompt_when_rag_misses(self):
        with patch("config.SHARED_PLATFORM_ENABLED", True), patch(
            "llm.shared_platform_client.search_rag", new=AsyncMock(return_value=[])
        ), patch(
            "llm.shared_platform_client.render_prompt", new=AsyncMock(return_value="共享未命中 Prompt")
        ) as render_prompt:
            messages, chunks = await build_messages(
                [{"role": "user", "content": "不存在的问题"}],
                top_k=2,
            )

        render_prompt.assert_awaited_once_with("aigc.voice.rag.no_context")
        self.assertEqual(chunks, [])
        self.assertEqual(messages[0]["content"], "共享未命中 Prompt")


if __name__ == "__main__":
    unittest.main()
