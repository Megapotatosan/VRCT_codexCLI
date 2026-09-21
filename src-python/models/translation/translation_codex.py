"""Codex / ChatGPT 翻訳 Provider (`Codex_CLI`)。

他の LLM Provider (`translation_openai.py` 等) と同じ形のクライアントだが、
HTTP の代わりにローカルの公式 Codex CLI を叩き、認証は ChatGPT アカウントの
公式ログインに完全に委ねる。OpenAI_API とは authentication mechanism が
違うため、同じエンジンには入れず独立した Provider にしてある (項目3)。

VRCT 側から見た位置づけは LMStudio/Ollama と同じ「疎通確認型」
(`CONNECTION_PROVIDER_REGISTRY`) — 認証キーを持たず、接続できるかどうかを
確認してモデル一覧を得る。違いは接続先がローカルサーバーではなく
サブプロセスである点だけなので、新しい Provider 層は作らずに既存の抽象化に
そのまま載せている (項目43: minimum necessary architecture change)。

認証キーを持たないので `getAuthKey()`/`setAuthKey()` は **実装しない**
(項目27)。互換のために空実装を置くと「VRCT が ChatGPT の credential を
持っている」ように見えてしまい、実態と食い違う。

ランタイムは `codex exec` の one-shot (項目31 Option A)。persistent
app-server を採らなかった理由は `_ROUTE_ONE_SHOT` のコメントを参照。
"""

from __future__ import annotations

import time
from datetime import datetime
from threading import BoundedSemaphore
from typing import Optional

try:
    from .translation_languages import translation_lang
    from .translation_utils import loadTranslatePromptConfig
    from . import translation_codex_cli as codex_cli
except Exception:
    import sys
    from os import path as os_path
    sys.path.append(os_path.dirname(os_path.abspath(__file__)))
    from translation_languages import translation_lang, loadTranslationLanguages
    from translation_utils import loadTranslatePromptConfig
    import translation_codex_cli as codex_cli
    translation_lang = loadTranslationLanguages(path=".", force=True)

try:
    from utils import errorLogging, printLog
except Exception:  # スタンドアロン実行 (`python translation_codex.py`) 用
    def errorLogging():  # type: ignore
        import traceback
        traceback.print_exc()

    def printLog(*args, **kwargs):  # type: ignore
        print(*args)


# 第一版はモデルセレクタを作らない (項目40)。Codex が利用可能なモデルを
# 機械可読に列挙する安定したコマンドが無いため、実在しないモデル名を
# ハードコードするより「CLI の設定に従う」を1択で出すほうが正しい。
# この値が選ばれている間、`codex exec` に `--model` を渡さない =
# ユーザーの `~/.codex/config.toml` の `model` がそのまま効く。
AUTOMATIC_MODEL = "Automatic"

# 同時に走らせる `codex exec` の上限 (項目35)。VRCT は mic/speaker/chat を
# 並行に処理しうるので、無制限に spawn すると数十プロセスが立つ。
# 2 にしているのは「mic と speaker が同時に喋っても詰まらない」最小値。
_MAX_CONCURRENT_EXEC = 2

# 計測ラベル (項目34)。persistent app-server を足す日が来たら
# route="persistent" が増える。
_ROUTE_ONE_SHOT = "one_shot"


class CodexClient:
    """Codex CLI を翻訳バックエンドとして使うクライアント。

    LMStudio/Ollama クライアントと同じメソッド名を持たせてあるので、
    `Translator` 側の分岐と `Controller._checkTranslationEngineConnection`
    はそのまま使える。Codex 固有なのは install/login まわりだけ。
    """

    def __init__(self, root_path: Optional[str] = None) -> None:
        self.model: Optional[str] = None
        self.root_path = root_path

        prompt_config = loadTranslatePromptConfig(root_path, "translation_codex.yml")
        self.supported_languages = list(translation_lang["Codex_CLI"]["source"].keys())
        self.prompt_template = prompt_config["system_prompt"]
        self.history_cfg = prompt_config.get("history", {
            "use_history": False,
            "sources": [],
            "max_messages": 0,
            "max_chars": 0,
            "header_template": "",
            "item_template": "[{source}] {role}: {text}",
        })
        # 構造化出力 (項目29)。CLI 側が `--output-schema` を解さない場合に
        # 翻訳が丸ごと失敗しないよう、1回失敗したらこのインスタンスでは
        # 以降プレーンテキストに落とす (下記 translate() 参照)。
        self.use_structured_output = bool(prompt_config.get("use_structured_output", True))

        self._context_history: list = []
        self._status = codex_cli.CodexStatus()
        self._exec_semaphore = BoundedSemaphore(_MAX_CONCURRENT_EXEC)
        self._warmed_up = False

    # -- 状態 ---------------------------------------------------------------

    def probeInstallation(self) -> codex_cli.CodexStatus:
        """Codex の導入状況と ChatGPT ログイン状態を調べる。

        副作用なし。起動時にも UI の再描画時にも安全に呼べる (項目7)。
        """
        self._status = codex_cli.probe_installation(self._status.executable)
        return self._status

    def getStatus(self) -> codex_cli.CodexStatus:
        return self._status

    def isInstalled(self) -> bool:
        return self._status.installed

    def checkConnection(self) -> bool:
        """`CONNECTION_PROVIDER_REGISTRY` から呼ばれる疎通確認。

        「ChatGPT アカウントでログイン済み」でだけ True。APIキーログインは
        False にして、UI に「Connect ChatGPT」を出させる (項目24/25)。
        """
        status = self.probeInstallation()
        return status.connected

    # -- インストール / ログイン -------------------------------------------

    def install(self) -> codex_cli.CodexStatus:
        """ユーザーの明示操作でのみ呼ばれるインストール (項目6/7)。

        Raises:
            codex_cli.CodexInstallError: 段階つきの失敗。
        """
        progress = codex_cli.InstallProgress()
        self._status = codex_cli.install_codex(progress)
        return self._status

    def login(self) -> bool:
        """公式 `codex login` を実行し、完了後に状態を取り直す (項目22/23)。

        戻り値は「ChatGPT アカウントで繋がったか」。ログイン自体は成功しても
        APIキーログインだった場合は False になる。
        """
        codex_path = self._status.executable or codex_cli.find_codex()
        if codex_path is None:
            return False
        codex_cli.login(codex_path)
        return self.checkConnection()

    def logout(self) -> bool:
        codex_path = self._status.executable or codex_cli.find_codex()
        if codex_path is None:
            return False
        result = codex_cli.logout(codex_path)
        self.probeInstallation()
        return result.ok

    # -- モデル -------------------------------------------------------------

    def getModelList(self) -> list:
        """選べるモデル (項目40)。

        接続できていないときに空を返すのは、`_checkTranslationEngineConnection`
        が「モデル一覧が空 = 接続失敗」として扱う既存契約に合わせるため。
        """
        return [AUTOMATIC_MODEL] if self._status.connected else []

    def getModel(self) -> Optional[str]:
        return self.model

    def setModel(self, model: str) -> bool:
        if model in self.getModelList():
            self.model = model
            return True
        return False

    def updateClient(self) -> None:
        """他 Provider と同じタイミングで呼ばれる「クライアント再構築」。

        Codex には常駐クライアントが無いので、ここでやるのは状態の取り直し
        だけ。`translate()` のたびに `codex login status` を打たずに済むよう、
        接続状態はこのタイミングでキャッシュしている。
        """
        self.probeInstallation()
        self._warmed_up = False

    def setContextHistory(self, history_items: list) -> None:
        """直近の会話履歴を受け取る (項目30)。

        Codex 側の会話セッションを memory として使わず、VRCT が持っている
        履歴を毎回プロンプトに載せる。`--ephemeral` でセッションを残さないのも
        同じ理由で、翻訳結果が前回の会話状態に依存しないようにしている。
        """
        self._context_history = history_items or []

    # -- 翻訳 ---------------------------------------------------------------

    def _buildSystemPrompt(self, input_lang: str, output_lang: str) -> str:
        """他 Provider と同じ組み立て方 (YAML テンプレ + 履歴) を踏襲する。"""
        system_prompt = self.prompt_template.format(
            supported_languages=self.supported_languages,
            input_lang=input_lang,
            output_lang=output_lang,
        )

        if not self.history_cfg.get("use_history"):
            return system_prompt

        allowed_sources = set(self.history_cfg.get("sources", []))
        max_messages = int(self.history_cfg.get("max_messages", 0))
        max_chars = int(self.history_cfg.get("max_chars", 0))
        item_tmpl = self.history_cfg.get("item_template", "[{source}] {role}: {text}")
        header_tmpl = self.history_cfg.get("header_template", "{history}")

        filtered = [h for h in self._context_history if h.get("source") in allowed_sources]
        recent = filtered[-max_messages:] if max_messages > 0 else filtered
        formatted_items = []
        for h in recent:
            timestamp_str = ""
            if "timestamp" in h:
                try:
                    timestamp_str = datetime.fromisoformat(h["timestamp"]).strftime("%H:%M")
                except Exception:
                    timestamp_str = ""
            formatted_items.append(
                item_tmpl.format(
                    timestamp=timestamp_str,
                    source=h.get("source", ""),
                    text=h.get("text", ""),
                )
            )
        history_blob = "\n".join(formatted_items).strip()
        if max_chars and len(history_blob) > max_chars:
            history_blob = history_blob[-max_chars:]
        history_header = header_tmpl.format(max_messages=max_messages, history=history_blob)
        return f"{system_prompt}\n\n{history_header}" if history_header else system_prompt

    def _logLatency(self, chars: int, elapsed_ms: int, outcome: str, cold: bool) -> None:
        """項目34の計測ログ。翻訳対象の本文は絶対に出さない。"""
        printLog(
            "Codex translation",
            f"provider=codex route={_ROUTE_ONE_SHOT} state={'cold' if cold else 'warm'} "
            f"chars={chars} elapsed_ms={elapsed_ms} outcome={outcome}",
        )

    def translate(self, text: str, input_lang: str, output_lang: str) -> str:
        """1文を翻訳する。

        Returns:
            翻訳文。モデルが本当に空を返した場合のみ空文字 (項目46 "Empty output")。

        Raises:
            codex_cli.CodexTranslationError: CLI の非0終了 / timeout / 未接続。
                `Translator.translate()` がこれを握って `False` (backend
                failure) に変換する。空文字で返すと「成功して空だった」と
                区別がつかず、上位が失敗を検知できなくなる。
        """
        codex_path = self._status.executable
        if codex_path is None or not self._status.connected:
            raise codex_cli.CodexTranslationError(
                "not_connected",
                f"installed={self._status.installed} auth_mode={self._status.auth_mode}",
            )

        prompt = f"{self._buildSystemPrompt(input_lang, output_lang)}\n\n---\n{text}"
        cold = not self._warmed_up
        started = time.monotonic()

        # 同時実行数を絞る (項目35)。acquire で待たせることで、
        # プロセスが際限なく積み上がるのを防ぐ。
        with self._exec_semaphore:
            translation, result = codex_cli.exec_once(
                codex_path,
                prompt,
                model=None if self.model in (None, AUTOMATIC_MODEL) else self.model,
                use_structured_output=self.use_structured_output,
            )

            # `--output-schema` を解さない CLI では usage エラーで即死する。
            # その場合だけ1度プレーンテキストで再試行し、以後はこの
            # インスタンスで構造化出力を諦める (項目29: 複雑にしすぎない)。
            if not result.ok and self.use_structured_output and not translation:
                self.use_structured_output = False
                translation, result = codex_cli.exec_once(
                    codex_path,
                    prompt,
                    model=None if self.model in (None, AUTOMATIC_MODEL) else self.model,
                    use_structured_output=False,
                )

        elapsed_ms = int((time.monotonic() - started) * 1000)
        self._warmed_up = True

        if result.timed_out:
            self._logLatency(len(text), elapsed_ms, "timeout", cold)
            raise codex_cli.CodexTranslationError("timeout", f"timeout after {elapsed_ms}ms")
        if not result.ok:
            self._logLatency(len(text), elapsed_ms, "cli_error", cold)
            # stderr は debug log にだけ残す。UI には出さない (項目20)。
            printLog("Codex translation failed", f"rc={result.returncode} stderr={result.stderr.strip()[:2000]}")
            raise codex_cli.CodexTranslationError("cli_error", f"rc={result.returncode}")
        if not translation:
            # CLI は正常終了したがモデルが何も返さなかった。これは失敗では
            # なく「空の翻訳」として上位に渡す (他 Provider と同じ扱い)。
            self._logLatency(len(text), elapsed_ms, "empty_output", cold)
            return ""

        self._logLatency(len(text), elapsed_ms, "ok", cold)
        return translation

    def close(self) -> None:
        """常駐プロセスを持たないので解放するものは無いが、Provider 切り替え時に
        呼ばれる契約に合わせて用意しておく (app-server route を足す日に効く)。"""
        self._warmed_up = False


if __name__ == "__main__":
    client = CodexClient(root_path=".")
    status = client.probeInstallation()
    print("installed:", status.installed, "version:", status.version, "auth:", status.auth_mode)
    if not status.installed:
        print("Codex CLI is not installed. Run the VRCT installer flow or `npm i -g @openai/codex`.")
    elif not status.connected:
        print("Not connected to a ChatGPT account. Run `codex login`.")
    else:
        client.setModel(AUTOMATIC_MODEL)
        client.updateClient()
        print(client.translate("こんにちは世界", "Japanese", "English"))
