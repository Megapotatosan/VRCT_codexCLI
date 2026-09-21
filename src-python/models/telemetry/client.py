"""
Aptabase SDK ラッパー（非同期版）
"""
import logging
from typing import Optional, Dict, Any

# Aptabase SDK のインポート
try:
    from aptabase import Aptabase
except ImportError:
    Aptabase = None

try:
    from build_channel import BUILD_CHANNEL
except ImportError:
    import os
    import sys

    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
    from build_channel import BUILD_CHANNEL


class AptabaseWrapper:
    # このフォーク (VRCT_codexCLI) ではテレメトリを送らない。
    #
    # 上流はここに stable/beta それぞれの Aptabase APP_KEY を持っていたが、
    # それは **上流の作者の分析プロジェクト** であり、フォークを配布したまま
    # 残すと、こちらのユーザーの利用状況が、本人が同意していない第三者の
    # アカウントへ、しかも上流のバージョンと見分けがつかない形で流れ込む。
    # 上流の分析データを汚染することにもなる。
    #
    # フォーク自身の Aptabase プロジェクトを用意したくなったら、ここに
    # {"stable": "...", "beta": "..."} を入れて APP_KEY を引き直せば、
    # TelemetryCore 側は無変更で有効になる。
    APP_KEYS: dict = {}
    APP_KEY = APP_KEYS.get(BUILD_CHANNEL)

    def __init__(self):
        self.client = None
        # Suppress noisy logs from the Aptabase SDK (only CRITICAL allowed)
        logging.getLogger("aptabase").setLevel(logging.CRITICAL)
    
    async def start(self, app_version: str = "1.0.0"):
        """Aptabase クライアント開始"""
        if Aptabase is None:
            raise ImportError("aptabase library not installed")
        try:
            self.client = Aptabase(
                app_key=self.APP_KEY,
                app_version=app_version,
                is_debug=False,
                max_batch_size=25,
                flush_interval=10.0,
                timeout=30.0
            )
            await self.client.start()
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Aptabase: {e}")
    
    async def track(self, event_name: str, properties: Optional[Dict[str, Any]] = None):
        """イベント送信（非同期）"""
        if self.client is None:
            return

        # properties が None なら空辞書
        if properties is None:
            properties = {}

        try:
            await self.client.track(event_name, properties)
        except Exception:
            # テレメトリ送信失敗は黙殺（本体処理を止めない）
            pass
    
    async def stop(self):
        """クライアント停止（フラッシュ含む）"""
        if self.client is not None:
            try:
                await self.client.stop()
            except Exception:
                pass
            self.client = None
