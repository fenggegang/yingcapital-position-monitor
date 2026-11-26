import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yingcapital_monitor.app import _deliver_pending
from yingcapital_monitor.config import Settings, Subscription
from yingcapital_monitor.ilink import WeixinSession
from yingcapital_monitor.notify import WeixinNotifier
from yingcapital_monitor.state import MonitorState, PendingNotification, load_state
from test_state import event

SECOND = "3a5fd9ed-917b-48aa-8697-ab2bdcfac1eb"


class SubscriptionTests(unittest.TestCase):
    def test_partial_success_reload_and_unsubscribe(self):
        with tempfile.TemporaryDirectory() as directory:
            session = WeixinSession("https://example.test", "private-token", "private-user", "private-context")
            settings = Settings("123", "demo", "UTC", Path(directory) / "123.json",
                                subscriptions=(Subscription("weixin", session, None),
                                               Subscription(SECOND, session, frozenset(["123"]))))
            notifier = WeixinNotifier(settings)
            item = PendingNotification(event(), dict.fromkeys(notifier.recipients, False))
            state = MonitorState("123", initialized=True, pending=[item])
            with patch.object(notifier, "send_weixin", side_effect=[None, RuntimeError()]) as send:
                self.assertTrue(_deliver_pending(state, settings, notifier))
                self.assertEqual(send.call_count, 2)
            restored = load_state(settings.state_path, "123")
            self.assertEqual(restored.pending[0].delivered, {"weixin": True, SECOND: False})
            with patch.object(notifier, "send_weixin") as send:
                self.assertFalse(_deliver_pending(restored, settings, notifier))
                self.assertEqual(send.call_args.args[1], SECOND)
                self.assertEqual(send.call_count, 1)
            state.pending = [item]
            notifier.recipients.pop(SECOND)
            with patch.object(notifier, "send_weixin") as send:
                self.assertFalse(_deliver_pending(state, settings, notifier))
                send.assert_not_called()
            self.assertEqual(json.loads(settings.state_path.read_text())["pending"], [])
            self.assertNotIn("private-", settings.state_path.read_text())

    def test_disabled_and_explicit_selection(self):
        from io import BytesIO
        session = dict(base_url="https://example.test", user_id="u", bot_token="b", context_token="c")
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = BytesIO(json.dumps({"users":[
                dict(id="weixin",enabled=False,traders=None,session=session),
                dict(id=SECOND,enabled=True,traders=["123"],session=session)
            ]}).encode())
            subs = Subscription.load("https://example.test/api/runtime", "fake-key")
        for portfolio, expected in [("123",[SECOND]),("555",[])]:
            settings = Settings(portfolio,"demo","UTC",Path("unused"),subscriptions=subs)
            self.assertEqual(list(settings.recipients()), expected)
