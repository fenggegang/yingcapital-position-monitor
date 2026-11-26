from __future__ import annotations

from uuid import uuid4
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import Settings
from .ilink import api_request
from .models import ACTION_LABELS, TradeEvent, decimal_text


def _format_order_time(milliseconds: int, timezone_name: str) -> str:
    try:
        selected_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        selected_timezone = timezone.utc
    value = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
    return value.astimezone(selected_timezone).isoformat(timespec="seconds")


def render_event(event: TradeEvent, settings: Settings) -> str:
    action = ACTION_LABELS[event.action]
    if event.action in {"BUY_DIRECTION", "SELL_DIRECTION"}:
        operation_text = f"{action}（开平待确认）"
    elif event.action == "DIRECTION_UNCONFIRMED":
        side_label = "买入" if event.side == "BUY" else "卖出"
        possibilities = "开多或平空" if event.side == "BUY" else "开空或平多"
        operation_text = (
            f"{action}（{side_label}；公开记录无法确认{possibilities}）"
        )
    else:
        direction = "多" if event.action.endswith("LONG") else "空"
        operation_text = f"{action}（{direction}）"
    quote_asset = event.quote_asset or "USDT"
    notional_value = event.executed_qty * event.avg_price
    return chr(10).join(
        [
            f"【{settings.trader_name}】{action}提醒",
            f"交易员：{settings.trader_name}",
            f"交易对：{event.symbol}",
            f"操作方向：{operation_text}",
            f"成交价格：{decimal_text(event.avg_price)} {quote_asset}",
            f"成交数量：{decimal_text(event.executed_qty)}",
            f"成交价值：{decimal_text(notional_value)} {quote_asset}",
            f"订单类型：{event.order_type}",
            f"已实现盈亏：{decimal_text(event.total_pnl)} {quote_asset}",
            f"操作时间：{_format_order_time(event.operation_time, settings.timezone)}",
            f"项目 ID：{settings.portfolio_id}",
            "数据源：币安网页公开最新操作记录（只读）",
        ]
    )


class WeixinNotifier:
    def __init__(self, settings: Settings, *, timeout: float = 20) -> None:
        self.settings = settings
        self.timeout = timeout
        self.recipients = settings.recipients()

    def send_weixin(self, event: TradeEvent, recipient: str = "weixin") -> None:
        session = self.recipients[recipient]
        if session is None:
            raise ValueError("缺少通知配置 WEIXIN_SESSION")
        message = {
            "from_user_id": "",
            "to_user_id": session.user_id,
            "client_id": str(uuid4()),
            "message_type": 2,
            "message_state": 2,
            "context_token": session.context_token,
            "item_list": [
                {"type": 1, "text_item": {"text": render_event(event, self.settings)}}
            ],
        }
        api_request(session.base_url, "sendmessage", {"msg": message},
                    token=session.bot_token, timeout=self.timeout)
