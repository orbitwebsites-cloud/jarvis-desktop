import os
import unittest
from unittest.mock import patch

from jarvis.providers import FreeModelRouter, FreeProvider, _message_text


class FreeProviderTests(unittest.TestCase):
    def test_openrouter_rejects_non_free_models(self):
        router = FreeModelRouter(timeout_seconds=10)
        provider = FreeProvider(
            "openrouter",
            "OPENROUTER_API_KEY",
            "https://openrouter.ai/api/v1",
            ("anthropic/claude-sonnet-5",),
            free_router_only=True,
        )
        with patch.object(
            router,
            "_available_models",
            return_value=["anthropic/claude-sonnet-5"],
        ):
            with self.assertRaisesRegex(RuntimeError, "No approved free model"):
                router._select_model(provider, "test-key")

    def test_openrouter_accepts_explicit_free_model(self):
        router = FreeModelRouter(timeout_seconds=10)
        provider = FreeProvider(
            "openrouter",
            "OPENROUTER_API_KEY",
            "https://openrouter.ai/api/v1",
            ("example/model:free",),
            free_router_only=True,
        )
        with patch.object(
            router,
            "_available_models",
            return_value=["example/model:free"],
        ):
            self.assertEqual(
                router._select_model(provider, "test-key"),
                "example/model:free",
            )

    def test_connection_summary_never_contains_keys(self):
        router = FreeModelRouter(timeout_seconds=10)
        with patch.dict(os.environ, {"GROQ_API_KEY": "secret-test-value"}):
            with patch.object(router, "omniroute_connections", return_value=[]):
                summary = router.connection_summary(None)
        self.assertNotIn("secret-test-value", repr(summary))

    def test_nonstandard_content_parts_are_normalized(self):
        body = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "Hello "},
                            {"type": "text", "text": "Boss"},
                        ]
                    }
                }
            ]
        }
        self.assertEqual(_message_text(body), "Hello Boss")


if __name__ == "__main__":
    unittest.main()
