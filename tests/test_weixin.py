import base64
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_state import event

from yingcapital_monitor import bind_weixin
from yingcapital_monitor.app import _deliver_pending, main
from yingcapital_monitor.config import Settings
from yingcapital_monitor.ilink import DEFAULT_BASE, WeixinSession
from yingcapital_monitor.notify import WeixinNotifier
from yingcapital_monitor.state import MonitorState, PendingNotification, load_state

SESSION = {
    "bot_token": "fake-bot-token",
    "context_token": "fake-context",
    "base_url": DEFAULT_BASE,
    "user_id": "private-test-recipient",
}


class WeixinTests(unittest.TestCase):
    def test_cli_sends_existing_alert_to_bound_chat(self):
        with (
            patch.dict(os.environ, {"WEIXIN_SESSION": json.dumps(SESSION)}, clear=True),
            patch("urllib.request.build_opener") as opener,
        ):
            call = opener.return_value.open
            call.return_value = io.BytesIO(b'{"ret":0}')
            self.assertEqual(main(["--test-notification"]), 0)
            req = call.call_args.args[0]
            self.assertEqual(req.full_url, DEFAULT_BASE + "/ilink/bot/sendmessage")
            self.assertEqual(req.get_header("Authorization"), "Bearer fake-bot-token")
            self.assertEqual(req.get_header("Authorizationtype"), "ilink_bot_token")
            self.assertTrue(base64.b64decode(req.get_header("X-wechat-uin")).isdigit())
            msg = json.loads(req.data)["msg"]
            self.assertEqual(msg["to_user_id"], SESSION["user_id"])
            self.assertEqual(msg["context_token"], SESSION["context_token"])
            self.assertEqual((msg["message_type"], msg["message_state"]), (2, 2))
            self.assertIn("TESTUSDT", msg["item_list"][0]["text_item"]["text"])

    def test_rejected_or_malformed_response_leaves_old_pending_for_retry(self):
        session = WeixinSession.from_json(json.dumps(SESSION))
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("urllib.request.build_opener") as opener,
        ):
            path = Path(tmp) / "123.json"
            settings = Settings("123", "test", "UTC", path, session)
            notifier = WeixinNotifier(settings)
            old = {"event": event().to_dict(), "delivered": {"telegram": False}}
            state = MonitorState.from_dict(
                {
                    "version": 3,
                    "portfolio_id": "123",
                    "initialized": True,
                    "pending": [old],
                },
                "123",
            )
            for body in (b'{"ret":-2}', b'{"ret":0,"errcode":-14}', b"", b"not json"):
                opener.return_value.open.return_value = io.BytesIO(body)
                self.assertTrue(_deliver_pending(state, settings, notifier))
                state = load_state(path, "123")
                self.assertEqual(state.pending[0].delivered, {"weixin": False})
                for value in SESSION.values():
                    self.assertNotIn(value, path.read_text())
            opener.return_value.open.return_value = io.BytesIO(b'{"ret":0}')
            self.assertEqual(_deliver_pending(state, settings, notifier), [])
            self.assertEqual(load_state(path, "123").pending, [])
            old["delivered"]["telegram"] = True
            state.pending = [PendingNotification.from_dict(old)]
            opener.reset_mock()
            self.assertEqual(_deliver_pending(state, settings, notifier), [])
            opener.assert_not_called()

    def test_binding_checks_sender_and_challenge_before_updating_private_file(self):
        def incoming(sender, phrase, token):
            return {
                "from_user_id": sender,
                "context_token": token,
                "item_list": [{"type": 1, "text_item": {"text": phrase}}],
            }

        replies = [
            {"qrcode": "id", "qrcode_img_content": "local-display-url"},
            {"status": "scaned_but_redirect", "redirect_host": "region.example"},
            {"status": "need_verifycode"},
            {"status": "confirmed", "bot_token": "test", "ilink_user_id": "owner"},
            {
                "get_updates_buf": "next",
                "msgs": [
                    incoming("other", "绑定 abc", "other-token"),
                    incoming("owner", "old text", "stale-token"),
                ],
            },
            {
                "get_updates_buf": "final",
                "msgs": [incoming("owner", "绑定 abc", "right-token")],
            },
        ]
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(
                bind_weixin, "SESSION_FILE", Path(tmp) / "weixin.json"
            ) as path,
            patch.object(bind_weixin, "api_request", side_effect=replies) as api,
            patch("secrets.token_hex", return_value="abc"),
            patch("getpass.getpass", return_value="1234"),
            patch("shutil.which", return_value=None),
            patch("time.sleep"),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(bind_weixin.main(), 0)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["context_token"], "right-token")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("right-token", output.getvalue())
            self.assertIn("verify_code=1234", api.call_args_list[3].args[1])
            self.assertEqual(api.call_args.args[2], {"get_updates_buf": "next"})
