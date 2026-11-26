"""HTTP JSON transport shared by Weixin binding and trade notifications."""

from __future__ import annotations

import base64
import json
import secrets
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_BASE = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "3.0.0"
CLIENT_VERSION = str(3 << 16)


def api_origin(value: str) -> str:
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/")
    ):
        raise ValueError("微信 API 地址必须是 HTTPS 服务地址")
    return value.rstrip("/")


@dataclass(frozen=True, repr=False)
class WeixinSession:
    base_url: str
    bot_token: str
    user_id: str
    context_token: str

    @classmethod
    def from_json(cls, value: str) -> WeixinSession:
        data = json.loads(value)
        keys = ("base_url", "bot_token", "user_id", "context_token")
        if not isinstance(data, dict) or not all(
            isinstance(data.get(key), str) and data[key].strip() for key in keys
        ):
            raise ValueError("WEIXIN_SESSION 缺少有效的地址、账号凭据或会话")
        return cls(
            api_origin(data["base_url"]),
            data["bot_token"],
            data["user_id"],
            data["context_token"],
        )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # iLink communicates IDC changes in the JSON login response.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_request(
    base: str,
    endpoint: str,
    body: dict | None = None,
    *,
    token: str = "",
    timeout: float = 35,
) -> dict:
    headers = {"iLink-App-Id": "bot", "iLink-App-ClientVersion": CLIENT_VERSION}
    data = None
    if body is not None:
        uin = str(secrets.randbits(32)).encode("ascii")
        headers.update(
            {
                "Content-Type": "application/json",
                "AuthorizationType": "ilink_bot_token",
                "X-WECHAT-UIN": base64.b64encode(uin).decode("ascii"),
            }
        )
        if token:
            headers["Authorization"] = f"Bearer {token}"
            body = {
                **body,
                "base_info": {
                    "channel_version": CHANNEL_VERSION,
                    "bot_agent": "YingCapitalMonitor/4.0",
                },
            }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{api_origin(base)}/ilink/bot/{endpoint}", headers=headers, data=data
    )
    with urllib.request.build_opener(_NoRedirect()).open(
        req, timeout=timeout
    ) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise TypeError("微信响应格式无效")
    for key in ("ret", "errcode"):
        if key in result and (type(result[key]) is not int or result[key] != 0):
            code = result[key] if type(result[key]) is int else "invalid"
            raise RuntimeError(f"微信 iLink {key}={code}")
    return result
