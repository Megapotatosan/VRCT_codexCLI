"""`CodexClient` の翻訳ランタイムのテスト (項目46)。

`codex exec` の起動は `translation_codex_cli.exec_once` の1点に閉じているので、
そこを差し替えれば成功/空出力/壊れた出力/timeout/CLIクラッシュ/連続リクエストを
全部モックで通せる。

項目46の "App-server disconnect" は、第一版が persistent app-server を
採らず `codex exec` の one-shot に統一しているため該当ケースが存在しない
(項目31 Option A)。代わりに、それに相当する「一度失敗したら別経路へ落ちる」
経路として構造化出力→プレーンテキストのフォールバックを検証する。
"""

import unittest
from unittest.mock import patch

from models.translation import translation_codex_cli as codex_cli
from models.translation.translation_codex import AUTOMATIC_MODEL, CodexClient
from models.translation.translation_languages import loadTranslationLanguages


def setUpModule() -> None:
    """`translation_lang` は import 時点では空で、通常は config の初期化が
    読み込む。ここは config を経由しないので自分で読み込む — これをしないと
    `CodexClient()` の `translation_lang["Codex_CLI"]` が KeyError になる。
    """
    loadTranslationLanguages(path=".")


def _result(returncode=0, stdout="", stderr="", timed_out=False):
    return codex_cli.CommandResult(
        returncode=returncode, stdout=stdout, stderr=stderr, timed_out=timed_out
    )


def _connectedClient():
    """ChatGPT ログイン済みまで進んだ状態のクライアント。"""
    client = CodexClient()
    client._status = codex_cli.CodexStatus(
        installed=True,
        executable="codex",
        version="codex-cli 0.155.1",
        auth_mode=codex_cli.AUTH_MODE_CHATGPT,
    )
    client.setModel(AUTOMATIC_MODEL)
    return client


class TestModelSelection(unittest.TestCase):
    def test_connected_client_offers_automatic_only(self) -> None:
        """第一版はモデルセレクタを作らない (項目40)。実在しないモデル名を
        ハードコードするより、CLI の設定に従う1択のほうが正しい。"""
        self.assertEqual(_connectedClient().getModelList(), [AUTOMATIC_MODEL])

    def test_disconnected_client_offers_nothing(self) -> None:
        """モデル一覧が空 = 接続失敗、という既存の共通契約に合わせる。"""
        self.assertEqual(CodexClient().getModelList(), [])

    def test_unknown_model_is_rejected(self) -> None:
        self.assertFalse(_connectedClient().setModel("gpt-does-not-exist"))

    def test_automatic_model_is_not_passed_to_the_cli(self) -> None:
        """Automatic のときは --model を渡さず、ユーザーの config.toml を効かせる。"""
        client = _connectedClient()
        with patch.object(codex_cli, "exec_once", return_value=("hi", _result())) as mock_exec:
            client.translate("やあ", "Japanese", "English")
        self.assertIsNone(mock_exec.call_args.kwargs["model"])


class TestTranslate(unittest.TestCase):
    def test_success_returns_the_translation(self) -> None:
        client = _connectedClient()
        with patch.object(codex_cli, "exec_once",
                          return_value=("Are you joining us later?", _result())):
            result = client.translate("あとで一緒に来る？", "Japanese", "English")
        self.assertEqual(result, "Are you joining us later?")

    def test_empty_output_returns_an_empty_string_not_a_failure(self) -> None:
        """CLI は正常終了したがモデルが何も返さなかったケース。

        これは「失敗」ではなく「空の翻訳」。他 Provider と同じ扱いにする。
        """
        client = _connectedClient()
        with patch.object(codex_cli, "exec_once", return_value=("", _result())):
            self.assertEqual(client.translate("...", "Japanese", "English"), "")

    def test_cli_crash_raises_so_the_facade_reports_a_backend_failure(self) -> None:
        """空文字で返すと「成功して空だった」と区別がつかなくなる。"""
        client = _connectedClient()
        with patch.object(codex_cli, "exec_once",
                          return_value=("", _result(returncode=101, stderr="panicked"))):
            with self.assertRaises(codex_cli.CodexTranslationError) as ctx:
                client.translate("hello", "English", "Japanese")
        self.assertEqual(ctx.exception.outcome, "cli_error")

    def test_timeout_raises_with_a_timeout_outcome(self) -> None:
        client = _connectedClient()
        with patch.object(codex_cli, "exec_once",
                          return_value=("", _result(returncode=-1, timed_out=True))):
            with self.assertRaises(codex_cli.CodexTranslationError) as ctx:
                client.translate("hello", "English", "Japanese")
        self.assertEqual(ctx.exception.outcome, "timeout")

    def test_translating_while_disconnected_raises_without_spawning_a_process(self) -> None:
        client = CodexClient()
        with patch.object(codex_cli, "exec_once") as mock_exec:
            with self.assertRaises(codex_cli.CodexTranslationError):
                client.translate("hello", "English", "Japanese")
        mock_exec.assert_not_called()

    def test_api_key_auth_cannot_translate(self) -> None:
        """APIキーログインのまま翻訳させない (項目25)。"""
        client = CodexClient()
        client._status = codex_cli.CodexStatus(
            installed=True, executable="codex", version="v",
            auth_mode=codex_cli.AUTH_MODE_API_KEY,
        )
        with patch.object(codex_cli, "exec_once") as mock_exec:
            with self.assertRaises(codex_cli.CodexTranslationError):
                client.translate("hello", "English", "Japanese")
        mock_exec.assert_not_called()

    def test_multiple_requests_reuse_one_client(self) -> None:
        """mic/speaker/chat から連続で来ても壊れないこと (項目35)。"""
        client = _connectedClient()
        outputs = [("one", _result()), ("two", _result()), ("three", _result())]
        with patch.object(codex_cli, "exec_once", side_effect=outputs):
            results = [
                client.translate("1", "Japanese", "English"),
                client.translate("2", "Japanese", "English"),
                client.translate("3", "Japanese", "English"),
            ]
        self.assertEqual(results, ["one", "two", "three"])

    def test_concurrency_is_bounded(self) -> None:
        """同時に走る codex プロセスに上限があること。無制限に spawn すると
        VRCT が数十プロセスを立ち上げてしまう。"""
        client = _connectedClient()
        self.assertGreaterEqual(client._exec_semaphore._initial_value, 1)
        self.assertLessEqual(client._exec_semaphore._initial_value, 4)

    def test_close_is_safe_without_a_persistent_process(self) -> None:
        client = _connectedClient()
        client.close()
        self.assertFalse(client._warmed_up)


class TestStructuredOutputFallback(unittest.TestCase):
    """構造化出力 (項目29) は「使えるなら使う、駄目なら黙って落ちる」。

    `--output-schema` を解さない Codex CLI に当たったときに翻訳が丸ごと
    失敗しないこと、かつ同じインスタンスで毎回2回叩かないこと。
    """

    def test_falls_back_to_plain_text_once_and_then_stays_plain(self) -> None:
        client = _connectedClient()
        self.assertTrue(client.use_structured_output)

        calls = []

        def _exec(_path, _prompt, model=None, use_structured_output=True, **kwargs):
            calls.append(use_structured_output)
            if use_structured_output:
                return "", _result(returncode=2, stderr="unexpected argument")
            return "Are you coming over later?", _result()

        with patch.object(codex_cli, "exec_once", side_effect=_exec):
            first = client.translate("你一陣會唔會過嚟？", "Chinese Traditional", "English")
        self.assertEqual(first, "Are you coming over later?")
        self.assertEqual(calls, [True, False])
        self.assertFalse(client.use_structured_output)

        # 2回目以降は最初からプレーンテキストで1回だけ。
        calls.clear()
        with patch.object(codex_cli, "exec_once", side_effect=_exec):
            client.translate("again", "English", "Japanese")
        self.assertEqual(calls, [False])

    def test_a_genuine_failure_still_raises_after_the_fallback(self) -> None:
        client = _connectedClient()
        with patch.object(codex_cli, "exec_once",
                          return_value=("", _result(returncode=1, stderr="network down"))):
            with self.assertRaises(codex_cli.CodexTranslationError):
                client.translate("hello", "English", "Japanese")


class TestOutputParsing(unittest.TestCase):
    """`--output-last-message` の中身を翻訳文に落とす部分 (項目29)。"""

    def test_structured_json_is_unwrapped(self) -> None:
        self.assertEqual(
            codex_cli._extract_translation('{"translation": "Are you joining us later?"}'),
            "Are you joining us later?",
        )

    def test_plain_text_passes_through(self) -> None:
        self.assertEqual(
            codex_cli._extract_translation("  Are you coming over later?  "),
            "Are you coming over later?",
        )

    def test_malformed_json_falls_back_to_the_raw_text(self) -> None:
        """壊れた JSON でも例外にせず、読めたものを返す。"""
        self.assertEqual(
            codex_cli._extract_translation('{"translation": "oops'),
            '{"translation": "oops',
        )

    def test_json_without_a_translation_field_falls_back_to_the_raw_text(self) -> None:
        self.assertEqual(
            codex_cli._extract_translation('{"other": "x"}'), '{"other": "x"}'
        )

    def test_empty_output_is_empty(self) -> None:
        self.assertEqual(codex_cli._extract_translation(""), "")
        self.assertEqual(codex_cli._extract_translation("   \n"), "")

    def test_slang_is_preserved_verbatim(self) -> None:
        """項目47のスラング群。パース側で勝手に加工しないこと。"""
        for token in ("lol", "brb", "gg", "AFK", "w", "草", "www", "やば", "笑"):
            with self.subTest(token=token):
                self.assertEqual(codex_cli._extract_translation(token), token)


class TestExecArguments(unittest.TestCase):
    """`codex exec` に渡すフラグの意図を固定する。"""

    def _argvFor(self, **kwargs):
        with patch.object(codex_cli, "_run", return_value=_result()) as mock_run:
            codex_cli.exec_once("codex", "prompt", **kwargs)
        return mock_run.call_args[0][0]

    def test_runs_outside_a_git_repository(self) -> None:
        self.assertIn("--skip-git-repo-check", self._argvFor())

    def test_does_not_persist_session_files(self) -> None:
        """翻訳した会話をディスクに残さない。"""
        self.assertIn("--ephemeral", self._argvFor())

    def test_runs_read_only(self) -> None:
        """翻訳にファイル書き込みもコマンド実行も要らない。"""
        argv = self._argvFor()
        self.assertIn("--sandbox", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")

    def test_works_in_a_scratch_directory_not_the_user_files(self) -> None:
        argv = self._argvFor()
        self.assertIn("--cd", argv)
        self.assertIn("vrct_codex_", argv[argv.index("--cd") + 1])

    def test_reads_the_final_message_from_a_file(self) -> None:
        self.assertIn("--output-last-message", self._argvFor())

    def test_structured_output_adds_a_schema_and_plain_does_not(self) -> None:
        self.assertIn("--output-schema", self._argvFor(use_structured_output=True))
        self.assertNotIn("--output-schema", self._argvFor(use_structured_output=False))

    def test_an_explicit_model_is_forwarded(self) -> None:
        argv = self._argvFor(model="gpt-5.6")
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-5.6")

    def test_translation_timeout_is_its_own_budget(self) -> None:
        """翻訳・ログイン・インストールの timeout を混ぜない (項目36)。"""
        self.assertLessEqual(codex_cli.TIMEOUT_TRANSLATE_SEC, 30)
        self.assertLess(codex_cli.TIMEOUT_TRANSLATE_SEC, codex_cli.TIMEOUT_LOGIN_SEC)


class TestPromptConstruction(unittest.TestCase):
    def test_prompt_tells_the_agent_to_translate_not_to_reply(self) -> None:
        """Codex は素だと質問に答えてしまう。明示の抑止が要る (項目28)。"""
        prompt = _connectedClient()._buildSystemPrompt("Japanese", "English").lower()
        self.assertIn("return only the translation", prompt)
        self.assertIn("do not answer the message", prompt)
        self.assertIn("do not explain", prompt)

    def test_prompt_carries_the_language_pair(self) -> None:
        prompt = _connectedClient()._buildSystemPrompt("Japanese", "English")
        self.assertIn("Japanese", prompt)
        self.assertIn("English", prompt)

    def test_history_uses_vrct_history_rather_than_a_codex_session(self) -> None:
        """項目30: Codex 側の会話を memory に使わず、VRCT の履歴を毎回渡す。"""
        client = _connectedClient()
        client.setContextHistory([
            {"source": "chat", "text": "we are going to the event", "timestamp": "2026-09-21T10:00:00"},
        ])
        self.assertIn("we are going to the event",
                      client._buildSystemPrompt("Japanese", "English"))

    def test_history_from_other_sources_is_filtered_out(self) -> None:
        client = _connectedClient()
        client.setContextHistory([
            {"source": "unknown_source", "text": "should not appear"},
        ])
        self.assertNotIn("should not appear",
                         client._buildSystemPrompt("Japanese", "English"))


class TestLatencyLogging(unittest.TestCase):
    """項目34: 計測はするが、会話本文は診断ログに出さない。"""

    def test_private_text_is_not_logged(self) -> None:
        client = _connectedClient()
        secret = "private conversation content"
        logged = []
        with patch("models.translation.translation_codex.printLog",
                   side_effect=lambda *a, **k: logged.append(" ".join(map(str, a)))), \
             patch.object(codex_cli, "exec_once", return_value=("translated", _result())):
            client.translate(secret, "English", "Japanese")
        blob = "\n".join(logged)
        self.assertNotIn(secret, blob)
        self.assertNotIn("translated", blob)

    def test_shape_is_logged(self) -> None:
        client = _connectedClient()
        logged = []
        with patch("models.translation.translation_codex.printLog",
                   side_effect=lambda *a, **k: logged.append(" ".join(map(str, a)))), \
             patch.object(codex_cli, "exec_once", return_value=("translated", _result())):
            client.translate("hello", "English", "Japanese")
        blob = "\n".join(logged)
        for field in ("provider=codex", "route=", "state=", "chars=", "elapsed_ms=", "outcome=ok"):
            self.assertIn(field, blob)

    def test_first_request_is_cold_and_the_second_is_warm(self) -> None:
        client = _connectedClient()
        logged = []
        with patch("models.translation.translation_codex.printLog",
                   side_effect=lambda *a, **k: logged.append(" ".join(map(str, a)))), \
             patch.object(codex_cli, "exec_once", return_value=("t", _result())):
            client.translate("a", "English", "Japanese")
            client.translate("b", "English", "Japanese")
        self.assertIn("state=cold", logged[0])
        self.assertIn("state=warm", logged[1])


if __name__ == "__main__":
    unittest.main()
