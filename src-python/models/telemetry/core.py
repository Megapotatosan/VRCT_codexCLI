"""
テレメトリコアロジック
- Aptabase クライアントの起動/停止
- イベント送信
"""

from .client import AptabaseWrapper
from .state import TelemetryState


class TelemetryCore:
    def __init__(self, state: TelemetryState):
        self.state = state
        self.client = None
        # 送信先の APP_KEY が無い場合はクライアントを作らない。このフォークは
        # 既定でそうなっており (client.AptabaseWrapper 参照)、以降の
        # start/stop/send_event は全て self.client is None で no-op になる。
        if not AptabaseWrapper.APP_KEY:
            return
        try:
            self.client = AptabaseWrapper()
        except Exception:
            self.client = None

    async def start(self, app_version: str = "1.0.0"):
        if self.client is None:
            return
        try:
            await self.client.start(app_version=app_version)
        except Exception:
            self.client = None

    async def stop(self):
        if self.client is not None:
            try:
                await self.client.stop()
            except Exception:
                pass

    async def send_event(self, event_name: str, payload: dict = None):
        if self.client is None:
            return
        properties = payload or {}
        await self.client.track(event_name, properties)
