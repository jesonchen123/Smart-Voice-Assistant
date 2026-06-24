from __future__ import annotations

import unittest
import sys
import types
from unittest.mock import AsyncMock, patch

sys.modules.setdefault("httpx", types.ModuleType("httpx"))
dotenv = types.ModuleType("dotenv")
dotenv.load_dotenv = lambda *_args, **_kwargs: None
sys.modules.setdefault("dotenv", dotenv)

volcengine = sys.modules.setdefault("volcengine", types.ModuleType("volcengine"))
volcengine_auth = sys.modules.setdefault("volcengine.auth", types.ModuleType("volcengine.auth"))
volcengine_signer = types.ModuleType("volcengine.auth.SignerV4")
volcengine_base = sys.modules.setdefault("volcengine.base", types.ModuleType("volcengine.base"))
volcengine_request = types.ModuleType("volcengine.base.Request")
volcengine_credentials = types.ModuleType("volcengine.Credentials")


class _SignerV4:
    @staticmethod
    def sign(_request, _credentials):
        return None


class _Request:
    pass


class _Credentials:
    def __init__(self, *_args, **_kwargs):
        pass


volcengine_signer.SignerV4 = _SignerV4
volcengine_request.Request = _Request
volcengine_credentials.Credentials = _Credentials
sys.modules.setdefault("volcengine.auth.SignerV4", volcengine_signer)
sys.modules.setdefault("volcengine.base.Request", volcengine_request)
sys.modules.setdefault("volcengine.Credentials", volcengine_credentials)

from knowledge_base import viking_kb


class VikingKbSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_collection_name_is_rejected_before_upstream_call(self):
        with patch("config.VIKING_KB_COLLECTION_NAME", "daniel"), patch(
            "knowledge_base.viking_kb._call", new=AsyncMock(return_value={"data": {}})
        ) as call:
            debug = await viking_kb.search_with_debug(
                "产品有什么",
                top_k=1,
                collection_name="1",
            )

        call.assert_not_awaited()
        self.assertEqual(debug["chunks"], [])
        self.assertEqual(debug["request_body"]["name"], "1")
        self.assertIn("Invalid collection name", debug["error"])
        self.assertIn("omit collection", debug["error"])


if __name__ == "__main__":
    unittest.main()
