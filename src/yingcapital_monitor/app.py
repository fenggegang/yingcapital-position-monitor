from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .binance import BinanceClient, BinanceError, OperationDataUnavailable
from .config import Settings
from .models import ACTION_LABELS, TradeEvent, parse_orders
from .notify import WeixinNotifier
from .state import PendingNotification, load_state, save_state


LOGGER = logging.getLogger("yingcapital_monitor")
MAX_SEEN_FINGERPRINTS = 2_000


def _now(timezone_name: str) -> str:
    try:
        selected_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        LOGGER.warning("系统缺少时区 %s，时间回退为 UTC", timezone_name)
        selected_timezone = timezone.utc
    return datetime.now(selected_timezone).isoformat(timespec="seconds")


def _deliver_pending(
    state,
    settings: Settings,
    notifier: WeixinNotifier,
) -> list[str]:
    errors: list[str] = []
    for item in list(state.pending):
        event_id = item.event.event_id
        if not item.delivered["weixin"]:
            try:
                notifier.send_weixin(item.event)
                item.delivered["weixin"] = True
                save_state(settings.state_path, state)
                LOGGER.info(
                    "[%s] 微信已发送：action=%s symbol=%s event=%s",
                    settings.trader_name,
                    ACTION_LABELS[item.event.action],
                    item.event.symbol,
                    event_id[:12],
                )
            except Exception as error:
                errors.append(f"微信 {event_id}: {type(error).__name__}")
                LOGGER.error(
                    "[%s] 微信通知失败，将在下次运行重试："
                    "event=%s（详细错误已隐藏）",
                    settings.trader_name,
                    event_id[:12],
                )

    state.pending = [
        item for item in state.pending if not all(item.delivered.values())
    ]
    save_state(settings.state_path, state)
    return errors


def _merge_seen(old: list[str], current: list[str]) -> list[str]:
    merged: list[str] = []
    known: set[str] = set()
    for fingerprint in old + current:
        if fingerprint not in known:
            known.add(fingerprint)
            merged.append(fingerprint)
    return merged[-MAX_SEEN_FINGERPRINTS:]


def run_monitor(settings: Settings) -> int:
    state = load_state(settings.state_path, settings.portfolio_id)
    notifier = WeixinNotifier(settings)
    delivery_errors = _deliver_pending(state, settings, notifier)

    try:
        snapshot = BinanceClient(settings.portfolio_id).fetch_operations()
    except OperationDataUnavailable as error:
        LOGGER.error(
            "[%s] 最新操作记录当前不可用；保留独立状态，不生成提醒：%s",
            settings.trader_name,
            error,
        )
        return 1
    except BinanceError as error:
        LOGGER.error(
            "[%s] 读取最新操作记录失败；保留独立状态，不生成提醒：%s",
            settings.trader_name,
            error,
        )
        return 1

    actual_name = str(snapshot.detail.get("nickname", "")).strip()
    if actual_name and actual_name != settings.trader_name:
        LOGGER.warning(
            "[%s] 配置显示昵称与币安昵称不同：实际=%s；"
            "项目 ID 已验证，继续使用配置昵称监控",
            settings.trader_name,
            actual_name,
        )

    orders = parse_orders(snapshot.order_rows)
    now = _now(settings.timezone)
    fingerprints = [order.fingerprint for order in orders]
    latest_order = max(
        orders,
        key=lambda order: (order.operation_time, order.order_time),
        default=None,
    )

    if not state.initialized:
        state.initialized = True
        state.initialized_at = now
        state.updated_at = now
        state.last_order_time = latest_order.operation_time if latest_order else None
        state.latest_order_fingerprint = (
            latest_order.fingerprint if latest_order else None
        )
        state.api_index_value = snapshot.index_value
        state.seen_order_fingerprints = _merge_seen([], fingerprints)
        save_state(settings.state_path, state)
        LOGGER.info(
            "[%s] 首次有效运行：已将最新 %d 条操作记录保存为独立基准；"
            "positionShow=%s；不发送旧交易提醒",
            settings.trader_name,
            len(orders),
            snapshot.detail.get("positionShow"),
        )
        return 1 if delivery_errors else 0

    seen = set(state.seen_order_fingerprints)
    new_orders = [order for order in orders if order.fingerprint not in seen]
    new_orders.sort(key=lambda order: (order.operation_time, order.order_time))

    if (
        new_orders
        and len(snapshot.order_rows) >= 100
        and not any(fingerprint in seen for fingerprint in fingerprints)
    ):
        LOGGER.warning(
            "[%s] 最新 100 条记录与旧基准没有交集，期间可能超过接口单页容量；"
            "仍将当前可见记录逐条处理",
            settings.trader_name,
        )

    existing_pending = {item.event.event_id for item in state.pending}
    for order in new_orders:
        event = order.to_event()
        if event.event_id not in existing_pending:
            state.pending.append(PendingNotification(event=event))
            existing_pending.add(event.event_id)

    state.seen_order_fingerprints = _merge_seen(
        state.seen_order_fingerprints, fingerprints
    )
    state.updated_at = now
    state.api_index_value = snapshot.index_value
    if latest_order is not None:
        previous_latest_time = state.last_order_time or 0
        if latest_order.operation_time >= previous_latest_time:
            state.last_order_time = latest_order.operation_time
            state.latest_order_fingerprint = latest_order.fingerprint
    save_state(settings.state_path, state)

    if not new_orders:
        LOGGER.info(
            "[%s] 最新操作记录无新增：读取=%d，接口总数=%d，"
            "positionShow=%s；不发送消息",
            settings.trader_name,
            len(orders),
            snapshot.total,
            snapshot.detail.get("positionShow"),
        )
        return 1 if delivery_errors else 0

    LOGGER.info(
        "[%s] 检测到 %d 条新增真实操作记录，准备发送微信",
        settings.trader_name,
        len(new_orders),
    )
    new_errors = _deliver_pending(state, settings, notifier)
    return 1 if delivery_errors or new_errors else 0


def run_monitors(settings_list: tuple[Settings, ...]) -> int:
    failures = 0
    for settings in settings_list:
        try:
            failures += 1 if run_monitor(settings) else 0
        except Exception as error:
            failures += 1
            LOGGER.error(
                "[%s] 监控执行失败：%s（不影响其他交易员）",
                settings.trader_name,
                type(error).__name__,
            )
    return 1 if failures else 0


def check_api(settings: Settings) -> int:
    try:
        snapshot = BinanceClient(settings.portfolio_id).fetch_operations()
    except OperationDataUnavailable as error:
        LOGGER.error("[%s] 最新操作记录接口不可用：%s", settings.trader_name, error)
        return 2
    except BinanceError as error:
        LOGGER.error("[%s] 币安接口验证失败：%s", settings.trader_name, error)
        return 1

    orders = parse_orders(snapshot.order_rows)
    LOGGER.info(
        "[%s] 接口验证成功：nickname=%s，portfolioType=%s，"
        "positionShow=%s，接口总数=%d，本页有效成交=%d",
        settings.trader_name,
        snapshot.detail.get("nickname"),
        snapshot.detail.get("portfolioType"),
        snapshot.detail.get("positionShow"),
        snapshot.total,
        len(orders),
    )
    if orders:
        latest = max(orders, key=lambda order: order.operation_time)
        LOGGER.info(
            "[%s] 最新真实记录：action=%s symbol=%s qty=%s price=%s "
            "operationTime=%d orderTime=%d orderUpdateTime=%d",
            settings.trader_name,
            ACTION_LABELS[latest.action or ""],
            latest.symbol,
            latest.executed_qty,
            latest.avg_price,
            latest.operation_time,
            latest.order_time,
            latest.order_update_time,
        )
    else:
        LOGGER.info("[%s] 当前没有公开操作记录；空列表是有效结果", settings.trader_name)
    return 0


def check_apis(settings_list: tuple[Settings, ...]) -> int:
    results = [check_api(settings) for settings in settings_list]
    return 0 if all(result == 0 for result in results) else 1


def send_test(settings: Settings) -> int:
    now_milliseconds = int(datetime.now(timezone.utc).timestamp() * 1000)
    event = TradeEvent(
        order_fingerprint="weixin-connectivity-test",
        action="OPEN_LONG",
        symbol="TESTUSDT",
        base_asset="TEST",
        quote_asset="USDT",
        side="BUY",
        position_side="LONG",
        order_type="MARKET",
        executed_qty=Decimal("1"),
        avg_price=Decimal("100"),
        total_pnl=Decimal("0"),
        order_time=now_milliseconds,
        order_update_time=now_milliseconds,
    )
    try:
        WeixinNotifier(settings).send_weixin(event)
        LOGGER.info("微信连通性测试通知已发送")
        return 0
    except Exception:
        LOGGER.error("微信连通性测试失败（详细错误已隐藏）")
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="币安交易员公开操作记录监控")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check-api",
        action="store_true",
        help="验证所有已启用交易员的最新操作记录接口，不发送通知",
    )
    group.add_argument(
        "--test-notification",
        action="store_true",
        help="仅发送一条微信连通性测试通知",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args(argv)
    try:
        settings_list = Settings.all_from_env(
            require_notifications=not args.check_api
        )
        if args.check_api:
            return check_apis(settings_list)
        if args.test_notification:
            return send_test(settings_list[0])
        return run_monitors(settings_list)
    except Exception as error:
        LOGGER.error("程序执行失败：%s（详细错误已隐藏）", type(error).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
