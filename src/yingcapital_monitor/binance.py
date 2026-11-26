from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any
from email.utils import parsedate_to_datetime


BASE_URL = "https://www.binance.com/bapi/futures/v1/friendly/future/copy-trade"
MILLISECONDS_PER_DAY = 86_400_000


class BinanceError(RuntimeError):
    pass


class BinanceCooldown(BinanceError):
    def __init__(self, status: int, retry_after: str | None):
        now = time.time()
        try:
            if retry_after and retry_after.isdecimal():
                until = now + int(retry_after)
            else:
                until = parsedate_to_datetime(retry_after or "").timestamp()
        except (ValueError, TypeError, OverflowError):
            until = now + (3600 if status == 418 else 900)
        self.until = max(now + 1, until)
        super().__init__(f"币安 HTTP {status}，已暂停本批查询")


class OperationDataUnavailable(BinanceError):
    """接口无法提供可验证的公开操作记录。"""


@dataclass(frozen=True)
class BinanceOperationsSnapshot:
    detail: dict[str, Any]
    order_rows: list[dict[str, Any]]
    total: int
    index_value: str | None


class BinanceClient:
    def __init__(
        self,
        portfolio_id: str,
        *,
        timeout: float = 25,
        max_attempts: int = 3,
    ) -> None:
        self.portfolio_id = portfolio_id
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 YingCapitalOperationMonitor/2.0",
            "clienttype": "web",
            "Origin": "https://www.binance.com",
            "Referer": (
                "https://www.binance.com/zh-CN/copy-trading/lead-details/"
                f"{portfolio_id}"
            ),
        }

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{BASE_URL}/{path}"
        data: bytes | None = None
        if method == "GET":
            query = urllib.parse.urlencode({"portfolioId": self.portfolio_id})
            url = f"{url}?{query}"
        elif payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            request = urllib.request.Request(
                url=url,
                data=data,
                headers=self.headers,
                method=method,
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    result = json.loads(response.read().decode("utf-8"))
                if (
                    not isinstance(result, dict)
                    or result.get("success") is not True
                    or result.get("code") != "000000"
                ):
                    raise BinanceError(
                        "币安接口返回失败："
                        f"code={getattr(result, 'get', lambda *_: None)('code')} "
                        f"message={getattr(result, 'get', lambda *_: None)('message')}"
                    )
                return result.get("data")
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code in {418, 429}:
                    raise BinanceCooldown(error.code, error.headers.get("Retry-After")) from error
                if error.code not in {500, 502, 503, 504}:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                last_error = error

            if attempt < self.max_attempts:
                time.sleep(2 ** (attempt - 1))

        raise BinanceError(f"币安接口请求失败：{last_error}") from last_error

    def fetch_operations(
        self,
        *,
        lookback_days: int = 7,
        page_size: int = 100,
    ) -> BinanceOperationsSnapshot:
        detail = self._request("GET", "lead-portfolio/detail")
        if not isinstance(detail, dict):
            raise BinanceError("币安项目详情格式无效")
        if str(detail.get("leadPortfolioId")) != self.portfolio_id:
            raise BinanceError("币安返回了不匹配的项目 ID")

        end_time = int(time.time() * 1000)
        history = self._request(
            "POST",
            "lead-portfolio/order-history",
            {
                "portfolioId": self.portfolio_id,
                "startTime": end_time - lookback_days * MILLISECONDS_PER_DAY,
                "endTime": end_time,
                "pageNumber": 1,
                "pageSize": page_size,
            },
        )
        if not isinstance(history, dict):
            raise OperationDataUnavailable(
                "最新操作记录 data 为空或格式无效，不能确认交易状态"
            )

        rows = history.get("list")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise OperationDataUnavailable(
                "最新操作记录 list 为空或格式无效，不能确认交易状态"
            )

        try:
            total = int(history.get("total", len(rows)))
        except (TypeError, ValueError):
            total = len(rows)
        return BinanceOperationsSnapshot(
            detail=detail,
            order_rows=rows,
            total=total,
            index_value=(
                str(history["indexValue"])
                if history.get("indexValue") is not None
                else None
            ),
        )
