"""
push_manager.py — Web Push 通知管理

负责：
- VAPID 密钥对的生成与持久化
- 推送订阅的存储与管理
- 向所有已订阅设备发送推送通知
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class PushManager:
    def __init__(self, workspace_dir: Path):
        self.keys_path = workspace_dir / "push_keys.json"
        self.subs_path = workspace_dir / "push_subscriptions.json"
        self._private_key_pem: Optional[str] = None
        self._public_key_b64:  Optional[str] = None
        self._subscriptions: list[dict] = []
        self._enabled = False
        self._init()

    def _init(self) -> None:
        try:
            from pywebpush import Vapid
        except ImportError:
            logger.warning("pywebpush 未安装，Web Push 功能不可用")
            return

        self._enabled = True
        self.keys_path.parent.mkdir(parents=True, exist_ok=True)

        if self.keys_path.exists():
            try:
                data = json.loads(self.keys_path.read_text())
                self._private_key_pem = data["private_key_pem"]
                self._public_key_b64  = data["public_key_b64"]
            except Exception:
                self._generate_keys()
        else:
            self._generate_keys()

        self._load_subscriptions()

    def _generate_keys(self) -> None:
        from pywebpush import Vapid
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PrivateFormat, NoEncryption, PublicFormat
        )
        v = Vapid()
        v.generate_keys()
        self._private_key_pem = v.private_key.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        ).decode()
        # Uncompressed EC point → urlsafe base64 (what browsers expect for applicationServerKey)
        pub_bytes = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        self._public_key_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b'=').decode()
        self.keys_path.write_text(json.dumps({
            "private_key_pem": self._private_key_pem,
            "public_key_b64":  self._public_key_b64,
        }, indent=2))
        logger.info("已生成新的 VAPID 密钥对")

    def _load_subscriptions(self) -> None:
        if self.subs_path.exists():
            try:
                self._subscriptions = json.loads(self.subs_path.read_text())
            except Exception:
                self._subscriptions = []

    def _save_subscriptions(self) -> None:
        self.subs_path.write_text(json.dumps(self._subscriptions, indent=2, ensure_ascii=False))

    @property
    def public_key_b64(self) -> Optional[str]:
        return self._public_key_b64

    # ── 订阅管理 ─────────────────────────────────────────────

    def add_subscription(self, subscription: dict) -> None:
        endpoint = subscription.get("endpoint")
        if not endpoint:
            return
        self._subscriptions = [s for s in self._subscriptions if s.get("endpoint") != endpoint]
        self._subscriptions.append(subscription)
        self._save_subscriptions()

    def remove_subscription(self, endpoint: str) -> None:
        before = len(self._subscriptions)
        self._subscriptions = [s for s in self._subscriptions if s.get("endpoint") != endpoint]
        if len(self._subscriptions) < before:
            self._save_subscriptions()

    # ── 发送通知 ─────────────────────────────────────────────

    async def send_all(self, title: str, body: str, url: str = "/m") -> None:
        if not self._enabled or not self._subscriptions:
            return
        import asyncio
        await asyncio.get_event_loop().run_in_executor(
            None, self._send_all_sync, title, body, url
        )

    def _send_all_sync(self, title: str, body: str, url: str) -> None:
        try:
            from pywebpush import webpush, WebPushException
        except ImportError:
            return

        payload = json.dumps({"title": title, "body": body, "url": url})
        expired: list[str] = []

        for sub in list(self._subscriptions):
            ep = sub.get("endpoint", "")
            try:
                webpush(
                    subscription_info=sub,
                    data=payload,
                    vapid_private_key=self._private_key_pem,
                    vapid_claims={"sub": "mailto:coderfleet@localhost"},
                )
            except WebPushException as exc:
                if exc.response is not None and exc.response.status_code in (404, 410):
                    expired.append(ep)
                else:
                    logger.warning("推送失败 [%s...]: %s", ep[:40], exc)
            except Exception as exc:
                logger.warning("推送异常 [%s...]: %s", ep[:40], exc)

        for ep in expired:
            self.remove_subscription(ep)
        if expired:
            logger.info("清理 %d 个过期推送订阅", len(expired))
