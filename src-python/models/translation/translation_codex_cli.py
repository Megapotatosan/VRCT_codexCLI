"""公式 Codex CLI (`@openai/codex`) との低レベルな境界。

`translation_codex.py` (`CodexClient`) が VRCT の翻訳エンジンとしての
ふるまいを担うのに対し、こちらは「OS 上の codex/node/npm/winget を探す・
入れる・ログイン状態を訊く・1回実行する」だけを担当する。分けてあるのは、
この層がほぼ全て subprocess とファイルシステム探索であり、テストでは
`_run`/`_which` の2点だけをモックすれば installation / authentication の
全分岐を検証できるため (test_translation_codex_cli.py 参照)。

他の翻訳エンジンと決定的に違うのは authentication の持ち主である:
OpenAI_API は VRCT が API キーを預かるが、Codex_CLI の credential は
公式 CLI が `CODEX_HOME` (既定 `~/.codex`) か OS のキーチェーンに保存し、
VRCT は一切触らない。VRCT がやるのは `codex login` を起動して
「ChatGPT アカウントでログイン済みか」を `codex login status` で訊くことだけ。

`codex login status` の出力 (v0.155 時点、いずれも **stderr**):

    exit 0  "Logged in using ChatGPT"                -> ChatGPT アカウント
    exit 0  "Logged in using an API key - sk-...xyz" -> APIキー (この Provider では不可)
    exit 1  "Not logged in"                          -> 未ログイン

APIキーログインを弾くのは課金先が変わってしまうため (下記 `subscription_environment`
と合わせて、項目25「Environment Isolation」の実装)。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from os import path as os_path
from typing import Optional, Sequence

# 公式の配布経路。第三者ミラーやprebuilt binaryは使わない (項目21)。
CODEX_NPM_PACKAGE = "@openai/codex"

# npm に公開されている `engines` フィールドの値 (v0.155.1 時点で ">=16")。
# ここをハードコードされた「20」等にしないのは、古い値を焼き付けると
# 実際には動く環境で不要な Node.js 再インストールを強いるため (項目9)。
# 実行時には `node --version` の major をこの値と比較するだけにして、
# 要求が上がったときはこの定数1つの更新で済むようにしてある。
CODEX_MIN_NODE_MAJOR = 16

# winget が入れる Node.js。LTS を指すエイリアスなので、将来 LTS が上がっても
# ここは変えなくてよい (項目10)。
NODE_LTS_WINGET_ID = "OpenJS.NodeJS.LTS"

# timeout は用途ごとに完全に分ける (項目36)。translation timeout を
# インストールに流用すると15分のインストールが30秒で殺される。
TIMEOUT_PROBE_SEC = 20          # `codex --version` / `codex login status`
TIMEOUT_TRANSLATE_SEC = 30      # `codex exec` 1回
TIMEOUT_LOGIN_SEC = 600         # ブラウザでの公式ログイン完了待ち
TIMEOUT_INSTALL_SEC = 900       # Node.js + Codex のインストール (項目14)

# `codex exec` に渡さない = 子プロセスの環境から取り除く変数 (項目25)。
# これを消さないと、ユーザーのPCに OPENAI_API_KEY が生えているだけで
# 「ChatGPT サブスクで翻訳しているつもりが API 従量課金だった」が起きる。
# `codex login status` 自体は enable_codex_api_key_env=false で動くため
# 影響を受けないが、`codex exec` は環境変数を拾うので必須。
_API_ENV_VARS_TO_STRIP = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_ORGANIZATION",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT",
    "OPENAI_PROJECT_ID",
    "CODEX_API_KEY",
    "AZURE_OPENAI_API_KEY",
)

_IS_WINDOWS = sys.platform == "win32"

# `codex login status` の stderr を分類する。文字列一致に寄りかからずに
# 済ませたいところだが、CLI 側に機械可読な出力が無いため現状はこれが唯一の
# 手段。CLI の文言が変わった場合は「ログイン済みだが mode 不明」として
# 扱い (AUTH_MODE_UNKNOWN)、接続失敗と同じ扱いにする — 「たぶん ChatGPT
# だろう」で API 課金に落ちるより安全側に倒す。
_RE_LOGGED_IN_CHATGPT = re.compile(r"logged in using chatgpt", re.IGNORECASE)
_RE_LOGGED_IN_API_KEY = re.compile(r"logged in using an api key", re.IGNORECASE)
_RE_NOT_LOGGED_IN = re.compile(r"not logged in", re.IGNORECASE)

AUTH_MODE_NONE = "none"
AUTH_MODE_CHATGPT = "chatgpt"
AUTH_MODE_API_KEY = "api_key"
AUTH_MODE_UNKNOWN = "unknown"

# インストール処理がどの段階で失敗したかを UI ではなく debug log に残すための
# ラベル (項目20)。UI にはこの stage に対応する短い文言だけを出す。
STAGE_WINGET_MISSING = "winget_missing"
STAGE_NODE_INSTALL_FAILED = "node_install_failed"
STAGE_NPM_MISSING = "npm_missing"
STAGE_CODEX_INSTALL_FAILED = "codex_install_failed"
STAGE_CODEX_NOT_FOUND_AFTER_INSTALL = "codex_not_found_after_install"
STAGE_UNSUPPORTED_PLATFORM = "unsupported_platform"


class CodexInstallError(Exception):
    """インストール経路の失敗。`stage` で UI 文言を選び、`detail` は debug log 行き。

    VRCT 本体を落とさないため、呼び出し側 (`CodexClient.install()`) は
    必ずこれを捕まえて構造化された結果に変換する (項目20)。
    """

    def __init__(self, stage: str, detail: str = "") -> None:
        super().__init__(f"{stage}: {detail}" if detail else stage)
        self.stage = stage
        self.detail = detail


class CodexTranslationError(Exception):
    """翻訳実行そのものの失敗 (CLI が非0終了 / timeout / 未接続)。

    `Translator.translate()` は例外を握って `False` (= backend failure) に
    変換する契約なので、空文字を返してしまうと「翻訳に成功して空だった」と
    区別がつかなくなる。モデルが本当に空を返したケース (項目46の
    "Empty output") だけを空文字で表し、それ以外はこの例外で表す。
    """

    def __init__(self, outcome: str, detail: str = "") -> None:
        super().__init__(f"{outcome}: {detail}" if detail else outcome)
        self.outcome = outcome
        self.detail = detail


@dataclass(frozen=True)
class CommandResult:
    """`_run()` の戻り値。例外ではなく値で失敗を表現する。"""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


@dataclass(frozen=True)
class CodexStatus:
    """UI の状態A〜D (項目5) をそのまま表現できる粒度の状態。"""

    installed: bool = False
    executable: Optional[str] = None
    version: Optional[str] = None
    auth_mode: str = AUTH_MODE_NONE

    @property
    def connected(self) -> bool:
        """「ChatGPT アカウントで繋がっている」か。

        APIキーログインは意図的に False。この Provider の存在意義が
        「APIキーを要求しないこと」なので、APIキーで動いてしまったら
        ユーザーの期待 (と課金先) が壊れる (項目24/25)。
        """
        return self.installed and self.auth_mode == AUTH_MODE_CHATGPT


@dataclass
class InstallProgress:
    """インストールの進捗。割合は出せないので「今どの段階か」だけを返す
    (項目15: 嘘のパーセンテージを作らない)。"""

    stage: str = ""
    messages: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# subprocess / PATH の薄いラッパ (テストではこの2つだけをモックする)
# ---------------------------------------------------------------------------

def _no_window_kwargs() -> dict:
    """GUI アプリからの subprocess でコンソール窓を一瞬も出さない。

    VRCT は GUI なので、翻訳のたびに黒い窓が明滅すると実用にならない。
    """
    if not _IS_WINDOWS:
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


def _run(
    argv: Sequence[str],
    timeout: float,
    env: Optional[dict] = None,
    cwd: Optional[str] = None,
    input_text: Optional[str] = None,
) -> CommandResult:
    """argv は必ずリストで渡す。shell=True で文字列連結しない (項目10/13)。

    `input_text` は子プロセスの stdin に流す。翻訳対象の本文のように
    「内容を一切信用できない文字列」は argv ではなくこちらに載せること —
    Windows で解決される codex 実行ファイルは `codex.cmd` (バッチ) であり、
    バッチへの引数は CreateProcess から cmd.exe の引用規則を通るため、
    `&` `|` `^` `%` `"` を含む文章が化けたり、最悪コマンドとして解釈されうる。
    stdin に載せればこの経路を丸ごと回避できる。

    どんな失敗 (実行ファイル無し/権限/タイムアウト) でも例外を投げずに
    `CommandResult` を返す — 呼び出し側の分岐を1本化するため。
    """
    try:
        completed = subprocess.run(
            list(argv),
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            cwd=cwd,
            **_no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as e:
        return CommandResult(
            returncode=-1,
            stdout=(e.stdout or "") if isinstance(e.stdout, str) else "",
            stderr=(e.stderr or "") if isinstance(e.stderr, str) else "",
            timed_out=True,
        )
    except (OSError, ValueError) as e:
        return CommandResult(returncode=-1, stdout="", stderr=str(e))
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def _which(name: str) -> Optional[str]:
    """`shutil.which` の薄いラッパ。テストで1点だけ差し替えられるようにする。"""
    from shutil import which as _shutil_which
    return _shutil_which(name)


def _is_file(path: str) -> bool:
    return os_path.isfile(path)


# ---------------------------------------------------------------------------
# 実行ファイル探索 (項目17/18/19)
# ---------------------------------------------------------------------------

def subscription_environment(base_env: Optional[dict] = None) -> dict:
    """ChatGPT ログインでのみ動くように API 系の環境変数を落とした環境を作る。

    `CODEX_HOME` は残す — credential の在り処であり、ここを消すと
    ユーザーが公式CLIで済ませたログインが見えなくなる。
    """
    env = dict(os.environ if base_env is None else base_env)
    for key in _API_ENV_VARS_TO_STRIP:
        env.pop(key, None)
    return env


def _windows_codex_candidates() -> list:
    """Windows の npm global install が codex を置きうる場所。

    PATH が更新されていない状況 (項目19: VRCT 起動後にインストールした)
    でも見つけられるよう、既知の場所を直接あたる。
    """
    candidates = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(os_path.join(appdata, "npm"))
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        program_files = os.environ.get(var)
        if program_files:
            candidates.append(os_path.join(program_files, "nodejs"))
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        # nvm-windows / volta などが使う場所
        candidates.append(os_path.join(localappdata, "npm"))
    return candidates


def _posix_codex_candidates() -> list:
    home = os_path.expanduser("~")
    return [
        "/usr/local/bin",
        "/usr/bin",
        "/opt/homebrew/bin",
        os_path.join(home, ".npm-global", "bin"),
        os_path.join(home, ".local", "bin"),
    ]


def _executable_names(stem: str) -> list:
    """Windows では `.cmd` を最優先する (項目18)。

    npm の global install は `codex.cmd` (Windows用ラッパ) と、拡張子なしの
    Unix向け shim `codex` を **同じフォルダに両方** 置く。後者を
    `subprocess.run` に渡すと `%1 is not a valid Win32 application` になるため、
    拡張子なしは最後の手段として残しつつ順序で守る。
    """
    if _IS_WINDOWS:
        return [f"{stem}.cmd", f"{stem}.exe", f"{stem}.bat", stem]
    return [stem]


def find_executable(stem: str, extra_dirs: Optional[Sequence[str]] = None) -> Optional[str]:
    """`stem` (codex/node/npm) の実行ファイルを探す。

    探索順は項目17のとおり: PATH -> 既知のインストール先。
    「前回見つかったパス」のキャッシュは呼び出し側 (`CodexClient`) が持つ。
    """
    for name in _executable_names(stem):
        found = _which(name)
        if found:
            return found

    search_dirs = list(extra_dirs or [])
    search_dirs.extend(_windows_codex_candidates() if _IS_WINDOWS else _posix_codex_candidates())
    for directory in search_dirs:
        for name in _executable_names(stem):
            candidate = os_path.join(directory, name)
            if _is_file(candidate):
                return candidate
    return None


def find_codex(preferred: Optional[str] = None) -> Optional[str]:
    """codex 実行ファイル。`preferred` は前回解決できたパス (項目17の4番目)。"""
    if preferred and _is_file(preferred):
        return preferred
    return find_executable("codex")


def find_node() -> Optional[str]:
    return find_executable("node")


def find_npm() -> Optional[str]:
    """npm を探す。

    Node.js を入れた直後は、既存プロセスの PATH に新しい Node が載っていない
    ことがある (項目12/19)。`_which` だけに頼らず `find_executable` 経由で
    `C:\\Program Files\\nodejs\\npm.cmd` 等も見に行く。
    """
    return find_executable("npm")


def find_winget() -> Optional[str]:
    return find_executable("winget")


def get_node_major_version(node_path: str) -> Optional[int]:
    """`node --version` (`v22.11.0`) の major を返す。取れなければ None。"""
    result = _run([node_path, "--version"], timeout=TIMEOUT_PROBE_SEC)
    if not result.ok:
        return None
    match = re.search(r"v?(\d+)\.", (result.stdout or result.stderr).strip())
    return int(match.group(1)) if match else None


def is_node_usable(node_path: Optional[str]) -> bool:
    """Codex を入れられる Node かどうか。バージョンが読めない場合は「使える」と
    見なす — 読めないだけで動く可能性があり、ここで再インストールに倒すと
    項目7「勝手に環境を変えない」に反するため。"""
    if node_path is None:
        return False
    major = get_node_major_version(node_path)
    if major is None:
        return True
    return major >= CODEX_MIN_NODE_MAJOR


# ---------------------------------------------------------------------------
# 状態の取得 (項目5/16/24)
# ---------------------------------------------------------------------------

def get_codex_version(codex_path: str) -> Optional[str]:
    """`codex --version`。npm の exit code 0 を信じず、ここで実体を検証する (項目16)。"""
    result = _run([codex_path, "--version"], timeout=TIMEOUT_PROBE_SEC, env=subscription_environment())
    if not result.ok:
        return None
    text = (result.stdout or result.stderr).strip()
    return text.splitlines()[0].strip() if text else None


def parse_login_status(result: CommandResult) -> str:
    """`codex login status` の結果を AUTH_MODE_* に落とす。

    出力は stdout ではなく **stderr** に出る (codex-rs は eprintln!)。
    両方を見るのは、将来 stdout に移っても壊れないようにするため。
    """
    if result.timed_out:
        return AUTH_MODE_UNKNOWN
    text = f"{result.stdout}\n{result.stderr}"
    if _RE_LOGGED_IN_CHATGPT.search(text):
        return AUTH_MODE_CHATGPT
    if _RE_LOGGED_IN_API_KEY.search(text):
        return AUTH_MODE_API_KEY
    if _RE_NOT_LOGGED_IN.search(text):
        return AUTH_MODE_NONE
    if result.returncode != 0:
        # 「Not logged in」以外の非0終了 (設定エラー等) は未ログイン扱い。
        return AUTH_MODE_NONE
    # exit 0 なのに文言が読めない = CLI の出力が変わった可能性。
    # ChatGPT と断定せず UNKNOWN にして接続失敗側に倒す。
    return AUTH_MODE_UNKNOWN


def check_login_status(codex_path: str) -> str:
    result = _run(
        [codex_path, "login", "status"],
        timeout=TIMEOUT_PROBE_SEC,
        env=subscription_environment(),
    )
    return parse_login_status(result)


def probe_installation(preferred_path: Optional[str] = None) -> CodexStatus:
    """UI の状態A〜D を決めるのに必要な情報を1回で集める。

    ここでは **絶対にインストールをしない** (項目7)。VRCT 起動時にも呼ばれる
    ため、副作用があるとユーザーの同意なく環境を書き換えることになる。
    """
    codex_path = find_codex(preferred_path)
    if codex_path is None:
        return CodexStatus(installed=False)

    version = get_codex_version(codex_path)
    if version is None:
        # 実行ファイルはあるが動かない (壊れたインストール、PATH上の別物)。
        return CodexStatus(installed=False, executable=codex_path)

    return CodexStatus(
        installed=True,
        executable=codex_path,
        version=version,
        auth_mode=check_login_status(codex_path),
    )


# ---------------------------------------------------------------------------
# インストール (項目6/7/10/11/12/13/16)
# ---------------------------------------------------------------------------

def install_node_lts(winget_path: str) -> CommandResult:
    """winget で Node.js LTS を入れる (項目10)。

    NOTE: `OpenJS.NodeJS.LTS` はマシン全体にインストールする MSI なので
    昇格 (UAC) が要る。`--disable-interactivity` を付けているため、昇格が
    必要でユーザーが同意していない環境では winget 側が非0で終了する。
    これは想定内の失敗として `STAGE_NODE_INSTALL_FAILED` に落とし、UI では
    「管理者権限が要る」旨を案内する (項目20: crash させない)。
    """
    return _run(
        [
            winget_path, "install",
            "--id", NODE_LTS_WINGET_ID,
            "--exact",
            "--source", "winget",
            "--silent",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--disable-interactivity",
        ],
        timeout=TIMEOUT_INSTALL_SEC,
    )


def install_codex_package(npm_path: str) -> CommandResult:
    """公式 npm パッケージを global に入れる (項目13/21)。"""
    return _run(
        [
            npm_path, "install",
            "--global", f"{CODEX_NPM_PACKAGE}@latest",
            "--no-audit",
            "--no-fund",
        ],
        timeout=TIMEOUT_INSTALL_SEC,
        env=subscription_environment(),
    )


def ensure_node_and_npm(progress: Optional[InstallProgress] = None) -> str:
    """npm のパスを返す。必要なら Node.js LTS を入れてから探し直す。

    Raises:
        CodexInstallError: winget が無い / Node インストール失敗 / 入れたのに
            npm が見つからない。
    """
    node_path = find_node()
    npm_path = find_npm()

    if npm_path is not None and is_node_usable(node_path):
        return npm_path

    if not _IS_WINDOWS:
        # 自動インストールは Windows を第一版の対象とする (Known Limitations)。
        # 他OSでは既存の Node を使う。無ければ手で入れてもらうしかない。
        raise CodexInstallError(
            STAGE_UNSUPPORTED_PLATFORM,
            "automatic Node.js installation is only implemented for Windows",
        )

    if progress is not None:
        progress.stage = "installing_node"

    winget_path = find_winget()
    if winget_path is None:
        raise CodexInstallError(STAGE_WINGET_MISSING, "winget executable not found")

    node_result = install_node_lts(winget_path)
    if not node_result.ok:
        raise CodexInstallError(
            STAGE_NODE_INSTALL_FAILED,
            f"rc={node_result.returncode} timed_out={node_result.timed_out} "
            f"stdout={node_result.stdout.strip()[:2000]} stderr={node_result.stderr.strip()[:2000]}",
        )

    # インストール直後、この VRCT プロセスの PATH は古いまま (項目19)。
    # `find_npm` は既知の場所も見るので、再起動を要求せずに拾える。
    npm_path = find_npm()
    if npm_path is None:
        raise CodexInstallError(
            STAGE_NPM_MISSING,
            "npm not found after installing Node.js LTS",
        )
    return npm_path


def install_codex(progress: Optional[InstallProgress] = None) -> CodexStatus:
    """ユーザーが「Install Codex CLI」を押したときの一連の流れ (項目6)。

    起動時には絶対に呼ばない (項目7)。呼び出し側が明示の同意を取ってから
    呼ぶこと。

    Raises:
        CodexInstallError: 各段階の失敗。stage で UI 文言を選ぶ。
    """
    if progress is None:
        progress = InstallProgress()

    progress.stage = "checking"
    existing = probe_installation()
    if existing.installed:
        # すでに入っているものを入れ直さない (項目44の1ケース目)。
        progress.stage = "already_installed"
        return existing

    npm_path = ensure_node_and_npm(progress)

    progress.stage = "installing_codex"
    codex_result = install_codex_package(npm_path)
    if not codex_result.ok:
        raise CodexInstallError(
            STAGE_CODEX_INSTALL_FAILED,
            f"rc={codex_result.returncode} timed_out={codex_result.timed_out} "
            f"stdout={codex_result.stdout.strip()[:2000]} stderr={codex_result.stderr.strip()[:2000]}",
        )

    # npm の exit code 0 では成功と見なさない (項目16)。実体を探し直し、
    # `codex --version` が通ることまで確認する。
    progress.stage = "verifying"
    status = probe_installation()
    if not status.installed:
        raise CodexInstallError(
            STAGE_CODEX_NOT_FOUND_AFTER_INSTALL,
            "npm reported success but the codex executable could not be verified",
        )

    progress.stage = "done"
    return status


# ---------------------------------------------------------------------------
# ログイン (項目22/23/24)
# ---------------------------------------------------------------------------

def login(codex_path: str, timeout: float = TIMEOUT_LOGIN_SEC) -> CommandResult:
    """公式の `codex login` を起動する。

    VRCT はブラウザを開かせて終わるのを待つだけで、OAuth を自前で実装したり
    token を横取りしたりはしない (項目4/23)。credential の保存先は CLI 任せ。
    """
    return _run([codex_path, "login"], timeout=timeout, env=subscription_environment())


def logout(codex_path: str) -> CommandResult:
    """公式の `codex logout`。UI の Disconnect はこれがある場合のみ出す (項目5)。"""
    return _run([codex_path, "logout"], timeout=TIMEOUT_PROBE_SEC, env=subscription_environment())


# ---------------------------------------------------------------------------
# 翻訳1回の実行 (項目31 Option A)
# ---------------------------------------------------------------------------

# 構造化出力 (項目29) 用の JSON Schema。`codex exec --output-schema` に渡す。
TRANSLATION_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"translation": {"type": "string"}},
    "required": ["translation"],
    "additionalProperties": False,
}


def _extract_translation(raw: str) -> str:
    """`--output-last-message` に書かれた最終メッセージを翻訳文に落とす。

    構造化出力が効いていれば `{"translation": "..."}`、効かなければ素のテキスト。
    どちらでも同じ結果になるようにして、schema 非対応の CLI でも壊れないようにする。
    """
    text = (raw or "").strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except ValueError:
            return text
        if isinstance(payload, dict) and isinstance(payload.get("translation"), str):
            return payload["translation"].strip()
    return text


def exec_once(
    codex_path: str,
    prompt: str,
    model: Optional[str] = None,
    timeout: float = TIMEOUT_TRANSLATE_SEC,
    use_structured_output: bool = True,
) -> tuple:
    """`codex exec` を1回まわして翻訳文を返す。

    Returns:
        (translation, CommandResult) — 失敗時 translation は "".

    渡しているフラグの理由:
        --skip-git-repo-check : 作業ディレクトリは git 管理外の一時領域
        --ephemeral           : セッションファイルをディスクに残さない (プライバシー)
        --sandbox read-only   : 翻訳にファイル書き込みもコマンド実行も要らない
        --cd <temp dir>       : ユーザーのファイルを作業対象にしない
        --output-last-message : イベントJSONLを解析せず最終メッセージだけ受け取る

    prompt は argv ではなく **stdin** で渡す (末尾の `-` がその指定)。
    翻訳対象は VRChat の発話や受信メッセージ、つまり内容を制御できない
    文字列であり、Windows では実行ファイルが `codex.cmd` (バッチ) に
    解決されるため、argv に載せると cmd.exe の引用規則を通ってしまう
    (項目13「未処理の使用者入力を shell 経由で渡さない」)。
    """
    with tempfile.TemporaryDirectory(prefix="vrct_codex_") as workdir:
        output_path = os_path.join(workdir, "last_message.txt")
        argv = [
            codex_path, "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox", "read-only",
            "--cd", workdir,
            "--output-last-message", output_path,
        ]

        schema_path = None
        if use_structured_output:
            schema_path = os_path.join(workdir, "translation_schema.json")
            with open(schema_path, "w", encoding="utf-8") as f:
                json.dump(TRANSLATION_OUTPUT_SCHEMA, f)
            argv += ["--output-schema", schema_path]

        if model:
            argv += ["--model", model]
        # `-` = 指示を stdin から読む。prompt 本文は argv に載せない。
        argv.append("-")

        result = _run(
            argv,
            timeout=timeout,
            env=subscription_environment(),
            cwd=workdir,
            input_text=prompt,
        )

        raw = ""
        if _is_file(output_path):
            try:
                with open(output_path, "r", encoding="utf-8", errors="replace") as f:
                    raw = f.read()
            except OSError:
                raw = ""

        return _extract_translation(raw), result
