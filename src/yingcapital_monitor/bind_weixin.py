"""Bind or refresh the operator's private Weixin session outside CI."""

from __future__ import annotations

import getpass
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
from pathlib import Path
from urllib.parse import urlencode

from .ilink import DEFAULT_BASE, api_origin, api_request

SESSION_FILE = Path.home() / ".config" / "yingcapital-monitor" / "weixin.json"


def save_private(path: Path, session: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(session, handle)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def bind() -> dict:
    qr = api_request(
        DEFAULT_BASE, "get_bot_qrcode?bot_type=3", {"local_token_list": []}
    )
    link = qr["qrcode_img_content"]
    print("请用微信扫码确认绑定。绑定链接请勿分享。", flush=True)
    if shutil.which("qrencode"):
        subprocess.run(
            ["qrencode", "-t", "ANSIUTF8"], input=link, text=True, check=True
        )
    else:
        try:
            import qrcode  # Optional, used only to display the login QR in a terminal.
        except ImportError:
            print(link, flush=True)
        else:
            qr_image = qrcode.QRCode()
            qr_image.add_data(link)
            qr_image.print_ascii(invert=True)
    base = DEFAULT_BASE
    query = {"qrcode": qr["qrcode"]}
    deadline = time.monotonic() + 480
    while time.monotonic() < deadline:
        try:
            reply = api_request(base, "get_qrcode_status?" + urlencode(query))
        except (TimeoutError, urllib.error.URLError) as error:
            if isinstance(error, urllib.error.HTTPError):
                raise
            time.sleep(1)
            continue
        status = reply.get("status")
        if status == "confirmed":
            if not reply.get("bot_token") or not reply.get("ilink_user_id"):
                raise ValueError("微信未返回绑定账号或收件人")
            return {
                "base_url": api_origin(reply.get("baseurl") or base),
                "bot_token": reply["bot_token"],
                "user_id": reply["ilink_user_id"],
            }
        if status == "scaned_but_redirect":
            base = api_origin("https://" + reply["redirect_host"])
        elif status in ("need_verifycode", "confirmed_but_need_verifycode"):
            query["verify_code"] = getpass.getpass("输入微信显示的 PIN：").strip()
        elif status == "scaned":
            query.pop("verify_code", None)
        elif status != "wait":
            raise RuntimeError("绑定未完成，请重新运行绑定命令")
        time.sleep(1)
    raise TimeoutError("扫码超时，请重新绑定")


def refresh(session: dict) -> dict:
    challenge = "绑定 " + secrets.token_hex(3)
    print(f"请用扫码的微信向机器人发送：{challenge}", flush=True)
    cursor = session.get("get_updates_buf", "")
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            result = api_request(
                session["base_url"],
                "getupdates",
                {"get_updates_buf": cursor},
                token=session["bot_token"],
                timeout=40,
            )
        except TimeoutError:
            continue
        cursor = result.get("get_updates_buf") or cursor
        for msg in result.get("msgs", []):
            if msg.get("from_user_id") != session["user_id"] or not msg.get(
                "context_token"
            ):
                continue
            if any(
                item.get("text_item", {}).get("text", "").strip() == challenge
                for item in msg.get("item_list", [])
            ):
                return {
                    **session,
                    "context_token": msg["context_token"],
                    "get_updates_buf": cursor,
                }
    raise TimeoutError("未收到绑定消息，请再运行一次")


def main() -> int:
    try:
        if SESSION_FILE.exists():
            session = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        else:
            session = bind()
            save_private(SESSION_FILE, session)
        save_private(SESSION_FILE, refresh(session))
        print(f"微信会话已保存到 {SESSION_FILE}。")
        return 0
    except (OSError, ValueError, TypeError, RuntimeError, KeyError) as error:
        # A network exception can contain the login URL. Never print it.
        print(f"微信绑定失败（{type(error).__name__}），请重试。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
