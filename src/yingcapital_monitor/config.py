from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .ilink import WeixinSession


DEFAULT_TRADERS_CONFIG_PATH = Path("traders.json")


@dataclass(frozen=True)
class Settings:
    portfolio_id: str
    trader_name: str
    timezone: str
    state_path: Path
    weixin_session: WeixinSession | None = None

    @classmethod
    def all_from_env(
        cls, *, require_notifications: bool = True
    ) -> tuple["Settings", ...]:
        timezone = os.getenv("MONITOR_TIMEZONE", "America/New_York").strip()
        state_dir = Path(os.getenv("MONITOR_STATE_DIR", ".runtime"))
        config_path = Path(
            os.getenv(
                "TRADERS_CONFIG_PATH", str(DEFAULT_TRADERS_CONFIG_PATH)
            ).strip()
        )
        credentials = os.getenv("WEIXIN_SESSION", "").strip()
        session = (
            WeixinSession.from_json(credentials)
            if require_notifications and credentials else None
        )

        try:
            with config_path.open("r", encoding="utf-8") as handle:
                traders = json.load(handle)
        except FileNotFoundError as error:
            raise ValueError(f"交易员配置文件不存在：{config_path}") from error
        except json.JSONDecodeError as error:
            raise ValueError(
                f"交易员配置文件 JSON 格式错误：第 {error.lineno} 行"
            ) from error

        if not isinstance(traders, list):
            raise ValueError("交易员配置文件最外层必须是 JSON 数组")

        settings: list[Settings] = []
        portfolio_ids: set[str] = set()
        for index, trader in enumerate(traders, start=1):
            if not isinstance(trader, dict):
                raise ValueError(f"第 {index} 个交易员必须是 JSON 对象")
            extra_fields = set(trader) - {"nickname", "portfolio_id", "enabled"}
            if extra_fields:
                raise ValueError(
                    f"第 {index} 个交易员包含不允许的字段："
                    + ", ".join(sorted(extra_fields))
                )

            portfolio_id = trader.get("portfolio_id")
            trader_name = trader.get("nickname")
            enabled = trader.get("enabled")
            if not isinstance(portfolio_id, str):
                raise ValueError(
                    f"第 {index} 个交易员的 portfolio_id 必须使用引号"
                )
            if not isinstance(trader_name, str):
                raise ValueError(f"第 {index} 个交易员的 nickname 必须是文字")
            if not isinstance(enabled, bool):
                raise ValueError(
                    f"第 {index} 个交易员的 enabled 必须是 true 或 false"
                )

            portfolio_id = portfolio_id.strip()
            trader_name = trader_name.strip()
            item = cls(
                portfolio_id=portfolio_id,
                trader_name=trader_name,
                timezone=timezone,
                state_path=state_dir / f"{portfolio_id}.json",
                weixin_session=session,
            )
            item.validate(
                require_notifications=require_notifications and enabled
            )
            if portfolio_id in portfolio_ids:
                raise ValueError(f"交易员项目 ID 重复：{portfolio_id}")
            portfolio_ids.add(portfolio_id)
            if enabled:
                settings.append(item)

        return tuple(settings)

    def validate(self, *, require_notifications: bool) -> None:
        if not self.portfolio_id.isdigit():
            raise ValueError("币安项目 ID 必须是数字")
        if not self.trader_name:
            raise ValueError("交易员名称不能为空")
        if require_notifications and self.weixin_session is None:
            raise ValueError("缺少通知配置 WEIXIN_SESSION")
