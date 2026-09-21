"""Codex_CLI の controller エンドポイントのテスト。

LMStudio/Ollama の疎通確認は「繋がる/繋がらない」の2値で足りるが、Codex は
未インストール / 未ログイン / APIキーでログイン を UI が出し分ける必要が
あるため、`checkTranslatorCodexConnection` だけ失敗理由を分類してから
共通実装 (`_checkTranslationEngineConnection`) に委ねる形にしてある
(項目5/24)。ここで検証するのはその分類と、インストール/ログインを
別スレッドに逃がしている部分の結果配線。

`model` (subprocess 境界) はモックする。実際に codex を起動するテストは無い。
"""

import unittest
from unittest.mock import patch

from config import config
from controller import Controller
from errors import ErrorCode
from models.translation import translation_codex_cli as codex_cli

_RUN_MAPPING = {
    "selectable_codex_model_list": "/run/selectable_codex_model_list",
    "selected_codex_model": "/run/selected_codex_model",
    "codex_status": "/run/codex_status",
    "codex_install": "/run/codex_install",
    "codex_login": "/run/codex_login",
}


class _ImmediateThread:
    """`Thread` の差し替え。`start()` でその場で target を実行する。

    本番では install/login を別スレッドに逃がしているが (項目44: UI を
    固めない)、テストで join を待つと「たまたま速いから通る」テストに
    なるので、同期実行に固定して結果配線だけを見る。
    """

    def __init__(self, target=None, name=None, **kwargs):
        self._target = target
        self.name = name
        self.daemon = False

    def start(self):
        if self._target is not None:
            self._target()


class _CodexEndpointTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_status = dict(config._SELECTABLE_TRANSLATION_ENGINE_STATUS)
        self._orig_model_list = list(config._SELECTABLE_CODEX_MODEL_LIST)
        self._orig_model = config._SELECTED_CODEX_MODEL

        self.pushed = []
        self.controller = Controller.__new__(Controller)
        self.controller.run_mapping = _RUN_MAPPING
        self.controller.run = lambda status, endpoint, payload=None: self.pushed.append(
            (status, endpoint, payload)
        )
        self.controller.updateTranslationEngineAndEngineList = lambda: None

    def tearDown(self) -> None:
        config.SELECTABLE_TRANSLATION_ENGINE_STATUS = self._orig_status
        config.SELECTABLE_CODEX_MODEL_LIST = self._orig_model_list
        # allowed=_allowed_in_populated(...) を持つので、リスト復元より後に
        # private 属性へ直接書き戻す。
        config._SELECTED_CODEX_MODEL = self._orig_model

    @staticmethod
    def _status(installed=True, auth_mode=codex_cli.AUTH_MODE_CHATGPT, version="codex-cli 0.155.1"):
        return codex_cli.CodexStatus(
            installed=installed,
            executable=r"C:\Users\someone\AppData\Roaming\npm\codex.cmd",
            version=version,
            auth_mode=auth_mode,
        )


class TestCheckConnection(_CodexEndpointTestBase):
    @patch("controller.model")
    def test_not_installed_returns_its_own_error_code(self, mock_model) -> None:
        mock_model.getTranslatorCodexStatus.return_value = self._status(installed=False)

        response = self.controller.checkTranslatorCodexConnection()

        self.assertEqual(response["status"], 400)
        self.assertEqual(response["result"]["error_code"], ErrorCode.CONNECTION_CODEX_NOT_INSTALLED)
        self.assertFalse(config.SELECTABLE_TRANSLATION_ENGINE_STATUS["Codex_CLI"])
        self.assertEqual(config.SELECTABLE_CODEX_MODEL_LIST, [])

    @patch("controller.model")
    def test_not_logged_in_returns_its_own_error_code(self, mock_model) -> None:
        mock_model.getTranslatorCodexStatus.return_value = self._status(
            auth_mode=codex_cli.AUTH_MODE_NONE
        )

        response = self.controller.checkTranslatorCodexConnection()

        self.assertEqual(response["result"]["error_code"], ErrorCode.CONNECTION_CODEX_NOT_LOGGED_IN)

    @patch("controller.model")
    def test_api_key_auth_is_rejected_with_its_own_error_code(self, mock_model) -> None:
        """ChatGPT サブスク用の Provider なので、APIキーログインを通すと
        課金先が変わってしまう (項目25)。接続成功にしない。"""
        mock_model.getTranslatorCodexStatus.return_value = self._status(
            auth_mode=codex_cli.AUTH_MODE_API_KEY
        )

        response = self.controller.checkTranslatorCodexConnection()

        self.assertEqual(response["status"], 400)
        self.assertEqual(response["result"]["error_code"], ErrorCode.CONNECTION_CODEX_API_KEY_AUTH)
        self.assertFalse(config.SELECTABLE_TRANSLATION_ENGINE_STATUS["Codex_CLI"])

    @patch("controller.model")
    def test_unknown_auth_mode_is_not_treated_as_connected(self, mock_model) -> None:
        """CLI の出力が変わって mode が読めないとき、ChatGPT と決めつけない。"""
        mock_model.getTranslatorCodexStatus.return_value = self._status(
            auth_mode=codex_cli.AUTH_MODE_UNKNOWN
        )

        response = self.controller.checkTranslatorCodexConnection()

        self.assertEqual(response["status"], 400)

    @patch("controller.model")
    def test_connected_delegates_to_the_shared_connection_handler(self, mock_model) -> None:
        """成功時はモデル一覧/選択モデルの反映を共通実装に任せる
        (Codex だけ別実装にしない)。"""
        mock_model.getTranslatorCodexStatus.return_value = self._status()
        mock_model.authenticationTranslatorCodex.return_value = True
        mock_model.getTranslatorCodexModelList.return_value = ["Automatic"]

        response = self.controller.checkTranslatorCodexConnection()

        self.assertEqual(response, {"status": 200, "result": True})
        self.assertTrue(config.SELECTABLE_TRANSLATION_ENGINE_STATUS["Codex_CLI"])
        self.assertEqual(config.SELECTABLE_CODEX_MODEL_LIST, ["Automatic"])
        self.assertEqual(config.SELECTED_CODEX_MODEL, "Automatic")
        mock_model.updateTranslatorCodexClient.assert_called_once()

    @patch("controller.model")
    def test_probe_failure_does_not_raise(self, mock_model) -> None:
        """subprocess が例外を投げても VRCT を落とさない (項目20)。"""
        mock_model.getTranslatorCodexStatus.side_effect = OSError("boom")

        response = self.controller.checkTranslatorCodexConnection()

        self.assertEqual(response["status"], 400)
        self.assertEqual(response["result"]["error_code"], ErrorCode.CONNECTION_CODEX_FAILED)


class TestStatusEndpoint(_CodexEndpointTestBase):
    @patch("controller.model")
    def test_status_payload_shape(self, mock_model) -> None:
        mock_model.getTranslatorCodexStatus.return_value = self._status()

        response = self.controller.getTranslatorCodexStatus()

        self.assertEqual(response["status"], 200)
        self.assertEqual(
            set(response["result"]),
            {"installed", "connected", "auth_mode", "version"},
        )
        self.assertTrue(response["result"]["connected"])

    @patch("controller.model")
    def test_status_does_not_leak_the_executable_path(self, mock_model) -> None:
        """パスにはユーザー名が入りうるので UI へは返さない。"""
        mock_model.getTranslatorCodexStatus.return_value = self._status()

        result = self.controller.getTranslatorCodexStatus()["result"]

        self.assertNotIn("executable", result)
        self.assertNotIn("someone", str(result))

    @patch("controller.model")
    def test_status_failure_degrades_instead_of_raising(self, mock_model) -> None:
        mock_model.getTranslatorCodexStatus.side_effect = OSError("boom")

        response = self.controller.getTranslatorCodexStatus()

        self.assertEqual(response["status"], 400)


class TestInstallEndpoint(_CodexEndpointTestBase):
    @patch("controller.Thread", _ImmediateThread)
    @patch("controller.model")
    def test_success_pushes_the_verified_status(self, mock_model) -> None:
        mock_model.installTranslatorCodexCLI.return_value = self._status(
            auth_mode=codex_cli.AUTH_MODE_NONE
        )

        response = self.controller.installTranslatorCodexCLI()

        # エンドポイント自体は即座に返る (項目44: UI を固めない)。
        self.assertEqual(response, {"status": 200, "result": True})
        endpoints = [endpoint for _, endpoint, _ in self.pushed]
        self.assertIn("/run/codex_install", endpoints)
        install_push = [p for s, e, p in self.pushed if e == "/run/codex_install"][0]
        self.assertTrue(install_push["installed"])
        # インストール直後はまだ未ログイン。UI はここで Connect ChatGPT を出す。
        self.assertFalse(install_push["connected"])

    @patch("controller.Thread", _ImmediateThread)
    @patch("controller.model")
    def test_winget_missing_maps_to_its_own_error_code(self, mock_model) -> None:
        mock_model.installTranslatorCodexCLI.side_effect = codex_cli.CodexInstallError(
            codex_cli.STAGE_WINGET_MISSING, "winget executable not found"
        )

        self.controller.installTranslatorCodexCLI()

        status, _, payload = [p for p in self.pushed if p[1] == "/run/codex_install"][0]
        self.assertEqual(status, 400)
        self.assertEqual(payload["error_code"], ErrorCode.CODEX_INSTALL_WINGET_MISSING)

    @patch("controller.Thread", _ImmediateThread)
    @patch("controller.model")
    def test_node_install_failure_maps_to_its_own_error_code(self, mock_model) -> None:
        mock_model.installTranslatorCodexCLI.side_effect = codex_cli.CodexInstallError(
            codex_cli.STAGE_NODE_INSTALL_FAILED, "rc=1 stderr=access denied"
        )

        self.controller.installTranslatorCodexCLI()

        status, _, payload = [p for p in self.pushed if p[1] == "/run/codex_install"][0]
        self.assertEqual(payload["error_code"], ErrorCode.CODEX_INSTALL_NODE_FAILED)

    @patch("controller.Thread", _ImmediateThread)
    @patch("controller.model")
    def test_installer_output_is_not_returned_to_the_ui(self, mock_model) -> None:
        """stdout/stderr/exit code は debug log 行き。UI には出さない (項目20)。"""
        mock_model.installTranslatorCodexCLI.side_effect = codex_cli.CodexInstallError(
            codex_cli.STAGE_CODEX_INSTALL_FAILED,
            "rc=1 stderr=EACCES: permission denied, /usr/lib/node_modules",
        )

        self.controller.installTranslatorCodexCLI()

        payload = [p for s, e, p in self.pushed if e == "/run/codex_install"][0]
        self.assertNotIn("EACCES", str(payload))

    @patch("controller.Thread", _ImmediateThread)
    @patch("controller.model")
    def test_unexpected_exception_does_not_escape(self, mock_model) -> None:
        """どんな失敗でも VRCT は動き続ける (項目20)。"""
        mock_model.installTranslatorCodexCLI.side_effect = RuntimeError("unexpected")

        response = self.controller.installTranslatorCodexCLI()

        self.assertEqual(response, {"status": 200, "result": True})
        payload = [p for s, e, p in self.pushed if e == "/run/codex_install"][0]
        self.assertEqual(payload["error_code"], ErrorCode.CODEX_INSTALL_FAILED)


class TestLoginEndpoint(_CodexEndpointTestBase):
    @patch("controller.Thread", _ImmediateThread)
    @patch("controller.model")
    def test_successful_login_confirms_the_connection(self, mock_model) -> None:
        mock_model.loginTranslatorCodexChatGPT.return_value = True
        mock_model.getTranslatorCodexStatus.return_value = self._status()
        mock_model.authenticationTranslatorCodex.return_value = True
        mock_model.getTranslatorCodexModelList.return_value = ["Automatic"]

        response = self.controller.loginTranslatorCodexChatGPT()

        self.assertEqual(response, {"status": 200, "result": True})
        login_pushes = [p for p in self.pushed if p[1] == "/run/codex_login"]
        self.assertEqual(login_pushes[0][0], 200)
        # ログイン成功後、そのまま接続確認まで進んでモデル一覧が埋まる。
        self.assertTrue(config.SELECTABLE_TRANSLATION_ENGINE_STATUS["Codex_CLI"])

    @patch("controller.Thread", _ImmediateThread)
    @patch("controller.model")
    def test_cancelled_login_pushes_a_login_failure(self, mock_model) -> None:
        mock_model.loginTranslatorCodexChatGPT.return_value = False
        mock_model.getTranslatorCodexStatus.return_value = self._status(
            auth_mode=codex_cli.AUTH_MODE_NONE
        )

        self.controller.loginTranslatorCodexChatGPT()

        status, _, payload = [p for p in self.pushed if p[1] == "/run/codex_login"][0]
        self.assertEqual(status, 400)
        self.assertEqual(payload["error_code"], ErrorCode.CODEX_LOGIN_FAILED)

    @patch("controller.Thread", _ImmediateThread)
    @patch("controller.model")
    def test_login_exception_is_contained(self, mock_model) -> None:
        mock_model.loginTranslatorCodexChatGPT.side_effect = OSError("boom")
        mock_model.getTranslatorCodexStatus.return_value = self._status(
            auth_mode=codex_cli.AUTH_MODE_NONE
        )

        response = self.controller.loginTranslatorCodexChatGPT()

        self.assertEqual(response, {"status": 200, "result": True})
        status, _, _payload = [p for p in self.pushed if p[1] == "/run/codex_login"][0]
        self.assertEqual(status, 400)


class TestLogoutEndpoint(_CodexEndpointTestBase):
    @patch("controller.model")
    def test_logout_clears_the_connection_but_is_not_an_error(self, mock_model) -> None:
        """ログアウトはユーザーの意図した操作なので 200 を返す。"""
        config.SELECTABLE_TRANSLATION_ENGINE_STATUS["Codex_CLI"] = True
        mock_model.getTranslatorCodexStatus.return_value = self._status(
            auth_mode=codex_cli.AUTH_MODE_NONE
        )

        response = self.controller.logoutTranslatorCodexChatGPT()

        self.assertEqual(response["status"], 200)
        self.assertFalse(config.SELECTABLE_TRANSLATION_ENGINE_STATUS["Codex_CLI"])
        self.assertEqual(config.SELECTABLE_CODEX_MODEL_LIST, [])


if __name__ == "__main__":
    unittest.main()
