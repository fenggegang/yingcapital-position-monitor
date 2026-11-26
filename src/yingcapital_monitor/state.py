from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import TradeEvent


STATE_VERSION = 4


@dataclass
class PendingNotification:
    event: TradeEvent
    delivered: dict[str, bool] = field(
        default_factory=lambda: {"weixin": False}
    )

    def to_dict(self) -> dict[str, Any]:
        return {"event": self.event.to_dict(), "delivered": self.delivered}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PendingNotification":
        delivered = raw.get("delivered", {})
        previously_sent = delivered.get("weixin", delivered.get("telegram", False))
        return cls(
            event=TradeEvent.from_dict(raw["event"]),
            delivered={"weixin": bool(previously_sent)},
        )


@dataclass
class MonitorState:
    portfolio_id: str
    initialized: bool = False
    seen_order_fingerprints: list[str] = field(default_factory=list)
    pending: list[PendingNotification] = field(default_factory=list)
    initialized_at: str | None = None
    updated_at: str | None = None
    last_order_time: int | None = None
    latest_order_fingerprint: str | None = None
    api_index_value: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "portfolio_id": self.portfolio_id,
            "initialized": self.initialized,
            "initialized_at": self.initialized_at,
            "updated_at": self.updated_at,
            "last_order_time": self.last_order_time,
            "latest_order_fingerprint": self.latest_order_fingerprint,
            "api_index_value": self.api_index_value,
            "seen_order_fingerprints": self.seen_order_fingerprints,
            "pending": [item.to_dict() for item in self.pending],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], portfolio_id: str) -> "MonitorState":
        if str(raw.get("portfolio_id")) != portfolio_id:
            raise ValueError("状态文件的项目 ID 与当前配置不一致")

        # v1 是旧的“当前仓位”基准，不能用于操作记录去重。迁移时丢弃旧
        # 仓位和旧待发事件，让下一次有效接口读取建立全新的操作记录基准。
        version = raw.get("version")
        if version == 1:
            return cls(portfolio_id=portfolio_id)
        if version not in {2, 3, STATE_VERSION}:
            raise ValueError("不支持的状态文件版本")

        fingerprints = raw.get("seen_order_fingerprints", [])
        if not isinstance(fingerprints, list):
            raise ValueError("状态文件的操作指纹列表无效")
        return cls(
            portfolio_id=portfolio_id,
            initialized=bool(raw.get("initialized", False)),
            initialized_at=raw.get("initialized_at"),
            updated_at=raw.get("updated_at"),
            last_order_time=raw.get("last_order_time"),
            latest_order_fingerprint=raw.get("latest_order_fingerprint"),
            api_index_value=raw.get("api_index_value"),
            seen_order_fingerprints=[str(item) for item in fingerprints],
            pending=[
                PendingNotification.from_dict(item)
                for item in raw.get("pending", [])
            ],
        )


def load_state(path: Path, portfolio_id: str) -> MonitorState:
    if not path.exists():
        return MonitorState(portfolio_id=portfolio_id)
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("状态文件格式无效")
    return MonitorState.from_dict(raw, portfolio_id)


def save_state(path: Path, state: MonitorState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
