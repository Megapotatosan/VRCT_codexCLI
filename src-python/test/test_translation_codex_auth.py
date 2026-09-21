"""Codex の ChatGPT 認証まわりのテスト (項目45)。

いちばん重要なのは最後のケース: **Codex が APIキーでログインしている状態を
「接続済み」と誤判定しないこと**。誤判定すると、ユーザーは ChatGPT の
サブスクで翻訳しているつもりなのに、実際には OpenAI API の従量課金が
発生する (項目24/25)。

`codex login status` の出力は stdout ではなく stderr に出る (codex-rs は
`eprintln!`) ため、stderr 側の解釈もここで固定しておく。
"""

import os
import unittest
from unittest.mock import patch

from models.translation import translation_codex_cli as codex_cli


def _result(returncode=0, stdout="", stderr="", timed_out=False):
    return codex_cli.CommandResult(
        returncode=returncode, stdout=stdout, stderr=stderr, timed_out=timed_out
    )


class TestParseLoginStatus(unittest.TestCase):
    def test_chatgpt_login_on_stderr(self) -> None:
        self.assertEqual(
            codex_cli.parse_login_status(_result(0, stderr="Logged in using ChatGPT\n")),
            codex_cli.AUTH_MODE_CHATGPT,
        )

    def test_chatgpt_login_on_stdout_is_also_accepted(self) -> None:
        """CLI が将来 stdout に移しても壊れないこと。"""
        self.assertEqual(
            codex_cli.parse_login_status(_result(0, stdout="Logged in using ChatGPT\n")),
            codex_cli.AUTH_MODE_CHATGPT,
        )

    def test_api_key_login_is_reported_as_api_key(self) -> None:
        result = _result(0, stderr="Logged in using an API key - sk-abc...xyz\n")
        self.assertEqual(codex_cli.parse_login_status(result), codex_cli.AUTH_MODE_API_KEY)

    def test_not_logged_in(self) -> None:
        self.assertEqual(
            codex_cli.parse_login_status(_result(1, stderr="Not logged in\n")),
            codex_cli.AUTH_MODE_NONE,
        )

    def test_login_timeout_is_unknown_not_connected(self) -> None:
        self.assertEqual(
            codex_cli.parse_login_status(_result(-1, timed_out=True)),
            codex_cli.AUTH_MODE_UNKNOWN,
        )

    def test_unrecognised_success_output_is_unknown_not_chatgpt(self) -> None:
        """CLI の文言が変わったら「たぶん ChatGPT」ではなく UNKNOWN にする。

        分からないときに ChatGPT と決めつけると、APIキーログインを
        素通りさせる事故に化ける。安全側 = 接続失敗に倒す。
        """
        result = _result(0, stderr="Logged in using some future mechanism\n")
        self.assertEqual(codex_cli.parse_login_status(result), codex_cli.AUTH_MODE_UNKNOWN)

    def test_nonzero_exit_without_a_known_message_is_not_logged_in(self) -> None:
        self.assertEqual(
            codex_cli.parse_login_status(_result(1, stderr="config error\n")),
            codex_cli.AUTH_MODE_NONE,
        )


class TestConnectedProperty(unittest.TestCase):
    """`CodexStatus.connected` は「ChatGPT アカウントで使える」の意味。"""

    def test_chatgpt_auth_is_connected(self) -> None:
        status = codex_cli.CodexStatus(
            installed=True, executable="codex", version="v",
            auth_mode=codex_cli.AUTH_MODE_CHATGPT,
        )
        self.assertTrue(status.connected)

    def test_api_key_auth_is_not_connected(self) -> None:
        status = codex_cli.CodexStatus(
            installed=True, executable="codex", version="v",
            auth_mode=codex_cli.AUTH_MODE_API_KEY,
        )
        self.assertFalse(status.connected)

    def test_unknown_auth_is_not_connected(self) -> None:
        status = codex_cli.CodexStatus(
            installed=True, executable="codex", version="v",
            auth_mode=codex_cli.AUTH_MODE_UNKNOWN,
        )
        self.assertFalse(status.connected)

    def test_not_installed_is_never_connected(self) -> None:
        status = codex_cli.CodexStatus(
            installed=False, auth_mode=codex_cli.AUTH_MODE_CHATGPT
        )
        self.assertFalse(status.connected)


class TestEnvironmentIsolation(unittest.TestCase):
    """項目25: ユーザーの環境に OPENAI_API_KEY があっても、この Provider が
    API 従量課金に流れないこと。"""

    def test_api_key_variables_are_stripped(self) -> None:
        base = {
            "PATH": "/usr/bin",
            "CODEX_HOME": "/home/u/.codex",
            "OPENAI_API_KEY": "sk-live-key",
            "OPENAI_BASE_URL": "https://example.invalid/v1",
            "AZURE_OPENAI_API_KEY": "azure-key",
        }
        env = codex_cli.subscription_environment(base)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("OPENAI_BASE_URL", env)
        self.assertNotIn("AZURE_OPENAI_API_KEY", env)

    def test_codex_home_is_preserved(self) -> None:
        """credential の在り処なので消してはいけない。消すとユーザーが
        公式CLIで済ませたログインが見えなくなる。"""
        env = codex_cli.subscription_environment({"CODEX_HOME": "/home/u/.codex"})
        self.assertEqual(env["CODEX_HOME"], "/home/u/.codex")

    def test_path_is_preserved(self) -> None:
        env = codex_cli.subscription_environment({"PATH": "/usr/bin"})
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_the_process_environment_itself_is_not_mutated(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-live-key"}, clear=False):
            codex_cli.subscription_environment()
            self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-live-key")

    def test_login_status_runs_without_the_api_key(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-live-key"}, clear=False), \
             patch.object(codex_cli, "_run",
                          return_value=_result(0, stderr="Logged in using ChatGPT")) as mock_run:
            codex_cli.check_login_status("codex")
        self.assertNotIn("OPENAI_API_KEY", mock_run.call_args.kwargs["env"])

    def test_exec_runs_without_the_api_key(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-live-key"}, clear=False), \
             patch.object(codex_cli, "_run", return_value=_result()) as mock_run:
            codex_cli.exec_once("codex", "prompt")
        self.assertNotIn("OPENAI_API_KEY", mock_run.call_args.kwargs["env"])


class TestProbeInstallation(unittest.TestCase):
    def test_missing_executable_reports_not_installed(self) -> None:
        with patch.object(codex_cli, "find_codex", return_value=None):
            status = codex_cli.probe_installation()
        self.assertFalse(status.installed)
        self.assertFalse(status.connected)

    def test_executable_that_cannot_report_a_version_is_not_installed(self) -> None:
        """PATH 上に名前だけ同じ別物がある / 壊れたインストール。"""
        with patch.object(codex_cli, "find_codex", return_value="codex"), \
             patch.object(codex_cli, "get_codex_version", return_value=None), \
             patch.object(codex_cli, "check_login_status") as mock_login:
            status = codex_cli.probe_installation()
        self.assertFalse(status.installed)
        # version が取れない時点で打ち切るので login status は訊かない。
        mock_login.assert_not_called()

    def test_installed_and_logged_out(self) -> None:
        with patch.object(codex_cli, "find_codex", return_value="codex"), \
             patch.object(codex_cli, "get_codex_version", return_value="codex-cli 0.155.1"), \
             patch.object(codex_cli, "check_login_status",
                          return_value=codex_cli.AUTH_MODE_NONE):
            status = codex_cli.probe_installation()
        self.assertTrue(status.installed)
        self.assertFalse(status.connected)

    def test_installed_and_logged_in_with_chatgpt(self) -> None:
        with patch.object(codex_cli, "find_codex", return_value="codex"), \
             patch.object(codex_cli, "get_codex_version", return_value="codex-cli 0.155.1"), \
             patch.object(codex_cli, "check_login_status",
                          return_value=codex_cli.AUTH_MODE_CHATGPT):
            status = codex_cli.probe_installation()
        self.assertTrue(status.connected)

    def test_installed_but_logged_in_with_an_api_key(self) -> None:
        """項目45の最重要ケース。"""
        with patch.object(codex_cli, "find_codex", return_value="codex"), \
             patch.object(codex_cli, "get_codex_version", return_value="codex-cli 0.155.1"), \
             patch.object(codex_cli, "check_login_status",
                          return_value=codex_cli.AUTH_MODE_API_KEY):
            status = codex_cli.probe_installation()
        self.assertTrue(status.installed)
        self.assertFalse(status.connected)
        self.assertEqual(status.auth_mode, codex_cli.AUTH_MODE_API_KEY)

    def test_probing_never_installs_anything(self) -> None:
        """起動時にも呼ばれるので、副作用があってはいけない (項目7)。"""
        with patch.object(codex_cli, "find_codex", return_value=None), \
             patch.object(codex_cli, "install_codex") as mock_install, \
             patch.object(codex_cli, "install_node_lts") as mock_node, \
             patch.object(codex_cli, "install_codex_package") as mock_pkg:
            codex_cli.probe_installation()
        mock_install.assert_not_called()
        mock_node.assert_not_called()
        mock_pkg.assert_not_called()


class TestLoginCommands(unittest.TestCase):
    def test_login_invokes_the_official_command(self) -> None:
        """OAuth を自前で実装せず、公式CLIに丸投げする (項目23)。"""
        with patch.object(codex_cli, "_run", return_value=_result()) as mock_run:
            codex_cli.login("codex")
        self.assertEqual(mock_run.call_args[0][0], ["codex", "login"])

    def test_login_uses_its_own_timeout(self) -> None:
        """ブラウザでの操作を待つので、翻訳用の timeout では短すぎる (項目36)。"""
        self.assertGreater(codex_cli.TIMEOUT_LOGIN_SEC, codex_cli.TIMEOUT_TRANSLATE_SEC)
        with patch.object(codex_cli, "_run", return_value=_result()) as mock_run:
            codex_cli.login("codex")
        self.assertEqual(
            mock_run.call_args.kwargs["timeout"], codex_cli.TIMEOUT_LOGIN_SEC
        )

    def test_login_cancelled_or_timed_out_is_not_connected(self) -> None:
        for result in (_result(returncode=1, stderr="cancelled"),
                       _result(returncode=-1, timed_out=True)):
            with self.subTest(result=result):
                with patch.object(codex_cli, "_run", return_value=result):
                    outcome = codex_cli.login("codex")
                self.assertFalse(outcome.ok)

    def test_logout_invokes_the_official_command(self) -> None:
        with patch.object(codex_cli, "_run", return_value=_result()) as mock_run:
            codex_cli.logout("codex")
        self.assertEqual(mock_run.call_args[0][0], ["codex", "logout"])


if __name__ == "__main__":
    unittest.main()
