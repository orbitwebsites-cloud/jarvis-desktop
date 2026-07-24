import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from jarvis.intelligence import IntelligenceBridge


class IntelligenceParsingTests(unittest.TestCase):
    @patch("jarvis.intelligence.subprocess.run")
    @patch.object(IntelligenceBridge, "_plan_via_local_endpoint", return_value=None)
    def test_bridge_uses_supported_chat_cli(self, _fast_plan, run):
        run.return_value = SimpleNamespace(
            returncode=0,
            stdout='{"kind":"chat","reply":"Ready","commands":[]}',
            stderr="",
        )
        bridge = IntelligenceBridge(enabled=True, timeout_seconds=10)
        bridge.executable = "hermes"
        bridge.free_router = MagicMock(configured=False)
        bridge._detect_local_endpoint_error = lambda: None
        plan = bridge.plan("hello")
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["hermes", "chat", "--quiet", "--query"])
        self.assertIn("--source", argv)
        self.assertEqual(plan.reply, "Ready")

    def test_parses_action_plan(self):
        plan = IntelligenceBridge._parse(
            '{"kind":"actions","reply":"On it.","commands":["open calculator"]}'
        )
        self.assertEqual(plan.kind, "actions")
        self.assertEqual(plan.commands, ["open calculator"])

    def test_limits_action_count(self):
        commands = [f"open website example{i}.com" for i in range(8)]
        raw = (
            '{"kind":"actions","reply":"Working.","commands":'
            + __import__("json").dumps(commands)
            + "}"
        )
        self.assertEqual(len(IntelligenceBridge._parse(raw).commands), 4)

    def test_accepts_fenced_json(self):
        plan = IntelligenceBridge._parse(
            '```json\n{"kind":"chat","reply":"Hello","commands":[]}\n```'
        )
        self.assertEqual(plan.reply, "Hello")

    def test_plain_text_becomes_chat(self):
        plan = IntelligenceBridge._parse("A plain intelligence response.")
        self.assertEqual(plan.kind, "chat")
        self.assertEqual(plan.reply, "A plain intelligence response.")

    def test_context_persists_and_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "context.json"
            bridge = IntelligenceBridge(enabled=False, context_path=path)
            bridge.record_turn("My project is Orion.", "Understood.")
            restored = IntelligenceBridge(enabled=False, context_path=path)
            self.assertEqual(
                restored._context_messages()[-2:],
                [
                    {"role": "user", "content": "My project is Orion."},
                    {"role": "assistant", "content": "Understood."},
                ],
            )
            for index in range(20):
                restored.record_turn(f"user {index}", f"assistant {index}")
            self.assertLessEqual(len(restored._context_messages()), 16)

    @patch.object(IntelligenceBridge, "_configured_base_url", return_value="http://localhost:20128/v1")
    @patch("jarvis.intelligence.socket.create_connection")
    @patch("jarvis.intelligence.urlopen")
    def test_local_endpoint_health_check_uses_head_without_downloading_catalog(
        self, urlopen, connect, _base_url
    ):
        connect.return_value = MagicMock()
        urlopen.return_value.__enter__.return_value = MagicMock()
        bridge = IntelligenceBridge(enabled=True)
        bridge.free_router = MagicMock(configured=False)
        bridge.free_router.connection_summary.return_value = []
        self.assertIsNone(bridge._detect_local_endpoint_error())
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "HEAD")
        self.assertTrue(request.full_url.endswith("/v1/models"))

    def test_direct_gateway_environment_overrides_legacy_configuration(self):
        with patch.dict(
            "os.environ",
            {
                "JARVIS_BASE_URL": "http://127.0.0.1:20128/v1",
                "JARVIS_MODEL": "mistral-test",
            },
        ):
            self.assertEqual(
                IntelligenceBridge._configured_base_url(),
                "http://127.0.0.1:20128/v1",
            )
            self.assertEqual(IntelligenceBridge._configured_model(), "mistral-test")


if __name__ == "__main__":
    unittest.main()
