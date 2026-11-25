import asyncio
import json
import os
import subprocess
import unittest
from unittest.mock import patch

import llm
import settings as settings_mod


class TestClaudeCliProvider(unittest.TestCase):
    def test_default_model_registered(self):
        self.assertIn("claude", settings_mod._DEFAULT_MODELS)
        self.assertEqual(settings_mod._DEFAULT_MODELS["claude"], "")

    def test_check_claude_status_not_installed(self):
        with patch.object(llm, "_find_claude_exe", return_value=None):
            status = llm.check_claude_status()
            self.assertFalse(status["installed"])
            self.assertFalse(status["logged_in"])
            self.assertIsNone(status["version"])
            self.assertIn("not installed", status["status_text"].lower())

    def test_check_claude_status_logged_in_json(self):
        def fake_run(cmd, **kwargs):
            if "--version" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="2.1.317 (Claude Code)\n", stderr="")
            payload = {
                "loggedIn": True,
                "authMethod": "claude.ai",
                "subscriptionType": "pro",
            }
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

        with (
            patch.object(llm, "_find_claude_exe", return_value="claude.exe"),
            patch.object(llm.subprocess, "run", side_effect=fake_run),
        ):
            status = llm.check_claude_status()
            self.assertTrue(status["installed"])
            self.assertTrue(status["logged_in"])
            self.assertEqual(status["version"], "2.1.317 (Claude Code)")
            self.assertEqual(status["status_text"], "Logged in via claude.ai (Pro)")

    def test_check_claude_status_logged_out_json(self):
        def fake_run(cmd, **kwargs):
            if "--version" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="2.1.317 (Claude Code)\n", stderr="")
            return subprocess.CompletedProcess(cmd, 1, stdout=json.dumps({"loggedIn": False}), stderr="")

        with (
            patch.object(llm, "_find_claude_exe", return_value="claude.exe"),
            patch.object(llm.subprocess, "run", side_effect=fake_run),
        ):
            status = llm.check_claude_status()
            self.assertTrue(status["installed"])
            self.assertFalse(status["logged_in"])
            self.assertIn("claude auth login", status["status_text"])

    def test_call_claude_passes_stdin_flags_model_and_clean_env(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(cmd, 0, stdout='[{"start": 1.0, "end": 35.0}]', stderr="")

        with (
            patch.dict(
                os.environ,
                {
                    "ANTHROPIC_API_KEY": "sk-ant-should-not-leak",
                    "CLAUDECODE": "1",
                    "CLAUDE_CODE_ENTRYPOINT": "cli",
                },
                clear=False,
            ),
            patch.object(llm, "_find_claude_exe", return_value="claude.exe"),
            patch.object(llm.subprocess, "run", side_effect=fake_run),
        ):
            res = asyncio.run(llm._call_provider_with_retry("claude", "", "sonnet", "Find clips"))
            self.assertEqual(res, '[{"start": 1.0, "end": 35.0}]')
            self.assertEqual(captured["kwargs"]["input"], "Find clips")
            self.assertIn("-p", captured["cmd"])
            self.assertIn("--no-session-persistence", captured["cmd"])
            self.assertIn("--tools", captured["cmd"])
            self.assertIn("--model", captured["cmd"])
            self.assertIn("sonnet", captured["cmd"])
            child_env = captured["kwargs"]["env"]
            self.assertNotIn("ANTHROPIC_API_KEY", child_env)
            self.assertNotIn("CLAUDECODE", child_env)
            self.assertNotIn("CLAUDE_CODE_ENTRYPOINT", child_env)

    def test_call_claude_omits_model_when_default_or_blank(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

        with (
            patch.object(llm, "_find_claude_exe", return_value="claude.exe"),
            patch.object(llm.subprocess, "run", side_effect=fake_run),
        ):
            for model in ("", "auto", "default"):
                asyncio.run(llm._call_claude("prompt", "", model))
                self.assertNotIn("--model", captured["cmd"])

    def test_call_claude_auth_error_maps_to_401(self):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Not logged in. Please run claude auth login.")

        with (
            patch.object(llm, "_find_claude_exe", return_value="claude.exe"),
            patch.object(llm.subprocess, "run", side_effect=fake_run),
        ):
            with self.assertRaises(llm.ProviderError) as ctx:
                asyncio.run(llm._call_claude("prompt", "", ""))
            self.assertEqual(ctx.exception.status, 401)
            self.assertFalse(llm.is_retryable(ctx.exception))
            self.assertIn("claude auth login", llm.friendly_error(ctx.exception))

    def test_call_claude_rate_limit_maps_to_429(self):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="You've hit your limit for Claude usage")

        with (
            patch.object(llm, "_find_claude_exe", return_value="claude.exe"),
            patch.object(llm.subprocess, "run", side_effect=fake_run),
        ):
            with self.assertRaises(llm.ProviderError) as ctx:
                asyncio.run(llm._call_claude("prompt", "", ""))
            self.assertEqual(ctx.exception.status, 429)
            self.assertTrue(llm.is_retryable(ctx.exception))

    def test_call_claude_missing_binary_maps_to_400(self):
        with patch.object(llm, "_find_claude_exe", return_value=None):
            with self.assertRaises(llm.ProviderError) as ctx:
                asyncio.run(llm._call_claude("prompt", "", ""))
            self.assertEqual(ctx.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
