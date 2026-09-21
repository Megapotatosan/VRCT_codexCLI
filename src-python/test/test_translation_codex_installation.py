"""Codex CLI のインストール経路のテスト (項目44)。

`translation_codex_cli` は OS 境界を `_run` (subprocess) / `_which` (PATH) /
`_is_file` (ファイル存在) の3点に絞ってあるので、そこだけ差し替えれば
「winget が無い」「Node が古い」「npm install が落ちた」「入ったのに PATH が
更新されていない」といった、実機では再現させにくい分岐を全部通せる。

実際に npm install を走らせるテストは1つも無い — CI で外部ネットワークと
15分のインストールに依存させないため。
"""

import unittest
from unittest.mock import patch

from models.translation import translation_codex_cli as codex_cli


def _result(returncode=0, stdout="", stderr="", timed_out=False):
    return codex_cli.CommandResult(
        returncode=returncode, stdout=stdout, stderr=stderr, timed_out=timed_out
    )


class TestExecutableNames(unittest.TestCase):
    """Windows の npm global install は `codex.cmd` と拡張子なしの Unix shim
    `codex` を同じフォルダに置く。後者を subprocess に渡すと
    `%1 is not a valid Win32 application` になるので、順序で守る (項目18)。"""

    def test_windows_prefers_cmd_over_bare_name(self) -> None:
        with patch.object(codex_cli, "_IS_WINDOWS", True):
            names = codex_cli._executable_names("codex")
        self.assertEqual(names[0], "codex.cmd")
        self.assertLess(names.index("codex.cmd"), names.index("codex"))

    def test_posix_uses_the_bare_name_only(self) -> None:
        with patch.object(codex_cli, "_IS_WINDOWS", False):
            self.assertEqual(codex_cli._executable_names("codex"), ["codex"])

    def test_windows_picks_cmd_when_both_exist_in_the_same_directory(self) -> None:
        """同一フォルダに codex と codex.cmd が両方あるケース (項目44の最終項目)。"""
        present = {r"C:\Users\u\AppData\Roaming\npm\codex.cmd",
                   r"C:\Users\u\AppData\Roaming\npm\codex"}
        with patch.object(codex_cli, "_IS_WINDOWS", True), \
             patch.object(codex_cli, "_which", return_value=None), \
             patch.object(codex_cli, "_is_file", side_effect=lambda p: p in present):
            found = codex_cli.find_executable(
                "codex", extra_dirs=[r"C:\Users\u\AppData\Roaming\npm"]
            )
        self.assertTrue(found.endswith("codex.cmd"))


class TestNodeDetection(unittest.TestCase):
    def test_node_version_is_parsed_from_v_prefixed_output(self) -> None:
        with patch.object(codex_cli, "_run", return_value=_result(stdout="v22.11.0\n")):
            self.assertEqual(codex_cli.get_node_major_version("node"), 22)

    def test_node_below_the_minimum_is_not_usable(self) -> None:
        old = f"v{codex_cli.CODEX_MIN_NODE_MAJOR - 2}.0.0"
        with patch.object(codex_cli, "_run", return_value=_result(stdout=old)):
            self.assertFalse(codex_cli.is_node_usable("node"))

    def test_node_at_the_minimum_is_usable(self) -> None:
        exact = f"v{codex_cli.CODEX_MIN_NODE_MAJOR}.0.0"
        with patch.object(codex_cli, "_run", return_value=_result(stdout=exact)):
            self.assertTrue(codex_cli.is_node_usable("node"))

    def test_unreadable_version_is_treated_as_usable(self) -> None:
        """バージョンが読めないだけで再インストールに倒さない。

        勝手にユーザーの環境を書き換えない方針 (項目7) の側に倒す。
        """
        with patch.object(codex_cli, "_run", return_value=_result(returncode=1)):
            self.assertTrue(codex_cli.is_node_usable("node"))

    def test_missing_node_is_not_usable(self) -> None:
        self.assertFalse(codex_cli.is_node_usable(None))


class TestEnsureNodeAndNpm(unittest.TestCase):
    def test_existing_usable_node_skips_installation(self) -> None:
        """Node が既にあるなら winget を呼ばない (項目6の分岐)。"""
        with patch.object(codex_cli, "find_node", return_value="node"), \
             patch.object(codex_cli, "find_npm", return_value="npm"), \
             patch.object(codex_cli, "is_node_usable", return_value=True), \
             patch.object(codex_cli, "install_node_lts") as mock_install:
            self.assertEqual(codex_cli.ensure_node_and_npm(), "npm")
        mock_install.assert_not_called()

    def test_missing_winget_raises_a_controlled_error(self) -> None:
        """winget が無くても crash させない (項目11/20)。"""
        with patch.object(codex_cli, "_IS_WINDOWS", True), \
             patch.object(codex_cli, "find_node", return_value=None), \
             patch.object(codex_cli, "find_npm", return_value=None), \
             patch.object(codex_cli, "find_winget", return_value=None):
            with self.assertRaises(codex_cli.CodexInstallError) as ctx:
                codex_cli.ensure_node_and_npm()
        self.assertEqual(ctx.exception.stage, codex_cli.STAGE_WINGET_MISSING)

    def test_node_install_failure_raises_a_controlled_error(self) -> None:
        with patch.object(codex_cli, "_IS_WINDOWS", True), \
             patch.object(codex_cli, "find_node", return_value=None), \
             patch.object(codex_cli, "find_npm", return_value=None), \
             patch.object(codex_cli, "find_winget", return_value="winget"), \
             patch.object(codex_cli, "install_node_lts",
                          return_value=_result(returncode=1, stderr="access denied")):
            with self.assertRaises(codex_cli.CodexInstallError) as ctx:
                codex_cli.ensure_node_and_npm()
        self.assertEqual(ctx.exception.stage, codex_cli.STAGE_NODE_INSTALL_FAILED)
        # stderr は例外の detail (= debug log 行き) にだけ残る。
        self.assertIn("access denied", ctx.exception.detail)

    def test_npm_missing_after_node_install_raises_a_controlled_error(self) -> None:
        with patch.object(codex_cli, "_IS_WINDOWS", True), \
             patch.object(codex_cli, "find_node", return_value=None), \
             patch.object(codex_cli, "find_npm", return_value=None), \
             patch.object(codex_cli, "find_winget", return_value="winget"), \
             patch.object(codex_cli, "install_node_lts", return_value=_result()):
            with self.assertRaises(codex_cli.CodexInstallError) as ctx:
                codex_cli.ensure_node_and_npm()
        self.assertEqual(ctx.exception.stage, codex_cli.STAGE_NPM_MISSING)

    def test_node_installed_then_npm_found_outside_path(self) -> None:
        """インストール直後、このプロセスの PATH はまだ古い (項目19)。

        既知のインストール先を見に行くことで、VRCT の再起動を要求せずに
        npm を拾えること。
        """
        calls = {"n": 0}

        def _find_npm():
            # 1回目 (インストール前) は見つからず、2回目 (インストール後) に
            # PATH ではなく既知の場所から見つかる、という実機の挙動を再現。
            calls["n"] += 1
            return None if calls["n"] == 1 else r"C:\Program Files\nodejs\npm.cmd"

        with patch.object(codex_cli, "_IS_WINDOWS", True), \
             patch.object(codex_cli, "find_node", return_value=None), \
             patch.object(codex_cli, "find_npm", side_effect=_find_npm), \
             patch.object(codex_cli, "find_winget", return_value="winget"), \
             patch.object(codex_cli, "install_node_lts", return_value=_result()):
            npm = codex_cli.ensure_node_and_npm()
        self.assertEqual(npm, r"C:\Program Files\nodejs\npm.cmd")


class TestInstallCodex(unittest.TestCase):
    def test_already_installed_does_not_reinstall(self) -> None:
        """入っているものを入れ直さない (項目44の1ケース目)。"""
        installed = codex_cli.CodexStatus(
            installed=True, executable="codex", version="codex-cli 0.155.1",
            auth_mode=codex_cli.AUTH_MODE_CHATGPT,
        )
        with patch.object(codex_cli, "probe_installation", return_value=installed), \
             patch.object(codex_cli, "ensure_node_and_npm") as mock_node, \
             patch.object(codex_cli, "install_codex_package") as mock_pkg:
            status = codex_cli.install_codex()
        self.assertTrue(status.installed)
        mock_node.assert_not_called()
        mock_pkg.assert_not_called()

    def test_npm_install_failure_raises_a_controlled_error(self) -> None:
        """npm install が落ちても VRCT は動き続ける (項目20)。"""
        with patch.object(codex_cli, "probe_installation",
                          return_value=codex_cli.CodexStatus(installed=False)), \
             patch.object(codex_cli, "ensure_node_and_npm", return_value="npm"), \
             patch.object(codex_cli, "install_codex_package",
                          return_value=_result(returncode=1, stderr="EACCES")):
            with self.assertRaises(codex_cli.CodexInstallError) as ctx:
                codex_cli.install_codex()
        self.assertEqual(ctx.exception.stage, codex_cli.STAGE_CODEX_INSTALL_FAILED)

    def test_install_timeout_raises_a_controlled_error(self) -> None:
        with patch.object(codex_cli, "probe_installation",
                          return_value=codex_cli.CodexStatus(installed=False)), \
             patch.object(codex_cli, "ensure_node_and_npm", return_value="npm"), \
             patch.object(codex_cli, "install_codex_package",
                          return_value=_result(returncode=-1, timed_out=True)):
            with self.assertRaises(codex_cli.CodexInstallError) as ctx:
                codex_cli.install_codex()
        self.assertEqual(ctx.exception.stage, codex_cli.STAGE_CODEX_INSTALL_FAILED)
        self.assertIn("timed_out=True", ctx.exception.detail)

    def test_npm_success_is_not_trusted_without_verifying_the_executable(self) -> None:
        """npm の exit code 0 を成功判定に使わない (項目16)。"""
        probes = [
            codex_cli.CodexStatus(installed=False),   # インストール前
            codex_cli.CodexStatus(installed=False),   # インストール後の検証
        ]
        with patch.object(codex_cli, "probe_installation", side_effect=probes), \
             patch.object(codex_cli, "ensure_node_and_npm", return_value="npm"), \
             patch.object(codex_cli, "install_codex_package", return_value=_result()):
            with self.assertRaises(codex_cli.CodexInstallError) as ctx:
                codex_cli.install_codex()
        self.assertEqual(
            ctx.exception.stage, codex_cli.STAGE_CODEX_NOT_FOUND_AFTER_INSTALL
        )

    def test_successful_install_reports_the_verified_status(self) -> None:
        verified = codex_cli.CodexStatus(
            installed=True, executable=r"C:\npm\codex.cmd",
            version="codex-cli 0.155.1", auth_mode=codex_cli.AUTH_MODE_NONE,
        )
        probes = [codex_cli.CodexStatus(installed=False), verified]
        progress = codex_cli.InstallProgress()
        with patch.object(codex_cli, "probe_installation", side_effect=probes), \
             patch.object(codex_cli, "ensure_node_and_npm", return_value="npm"), \
             patch.object(codex_cli, "install_codex_package", return_value=_result()):
            status = codex_cli.install_codex(progress)
        self.assertTrue(status.installed)
        # インストール直後はまだログインしていないので connected は False。
        # UI はここで「Connect ChatGPT」を出す (項目22: install と login を分ける)。
        self.assertFalse(status.connected)
        self.assertEqual(progress.stage, "done")


class TestInstallCommands(unittest.TestCase):
    """コマンドは必ず argv のリストで組み立てる (shell 文字列連結をしない)。"""

    def test_node_install_uses_the_official_winget_id_as_an_argv_list(self) -> None:
        with patch.object(codex_cli, "_run", return_value=_result()) as mock_run:
            codex_cli.install_node_lts("winget")
        argv = mock_run.call_args[0][0]
        self.assertIsInstance(argv, list)
        self.assertIn(codex_cli.NODE_LTS_WINGET_ID, argv)
        self.assertIn("--silent", argv)
        self.assertIn("--disable-interactivity", argv)

    def test_codex_install_uses_the_official_npm_package(self) -> None:
        """公式の配布経路以外は使わない (項目21)。"""
        with patch.object(codex_cli, "_run", return_value=_result()) as mock_run:
            codex_cli.install_codex_package("npm")
        argv = mock_run.call_args[0][0]
        self.assertIsInstance(argv, list)
        self.assertIn("--global", argv)
        self.assertIn(f"{codex_cli.CODEX_NPM_PACKAGE}@latest", argv)

    def test_install_timeout_is_separate_from_the_translation_timeout(self) -> None:
        """インストールを翻訳用の timeout で殺さない (項目36)。"""
        self.assertGreater(codex_cli.TIMEOUT_INSTALL_SEC, codex_cli.TIMEOUT_TRANSLATE_SEC)
        self.assertGreaterEqual(codex_cli.TIMEOUT_INSTALL_SEC, 900)
        with patch.object(codex_cli, "_run", return_value=_result()) as mock_run:
            codex_cli.install_codex_package("npm")
        self.assertEqual(
            mock_run.call_args.kwargs["timeout"], codex_cli.TIMEOUT_INSTALL_SEC
        )


if __name__ == "__main__":
    unittest.main()
