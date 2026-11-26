from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yingcapital_monitor.config import Settings


def trader(nickname: str, portfolio_id: str, enabled: bool = True) -> dict:
    return {
        "nickname": nickname,
        "portfolio_id": portfolio_id,
        "enabled": enabled,
    }


class TraderConfigurationTests(unittest.TestCase):
    def test_add_pause_resume_rename_and_delete_preserve_state_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            config_path = directory / "traders.json"
            state_dir = directory / "state"
            alpha = trader("Alpha", "1000000000000000001")
            beta = trader("Beta", "1000000000000000002")

            def save_config(items: list[dict]) -> None:
                config_path.write_text(
                    json.dumps(items, ensure_ascii=False),
                    encoding="utf-8",
                )

            def load() -> tuple[Settings, ...]:
                with patch.dict(
                    "os.environ",
                    {
                        "TRADERS_CONFIG_PATH": str(config_path),
                        "MONITOR_STATE_DIR": str(state_dir),
                    },
                    clear=True,
                ):
                    return Settings.all_from_env(require_notifications=False)

            save_config([alpha])
            self.assertEqual([item.trader_name for item in load()], ["Alpha"])

            # 添加交易员只需追加一项。
            save_config([alpha, beta])
            beta_settings = load()[1]
            beta_state_path = beta_settings.state_path
            beta_state_path.parent.mkdir(parents=True)
            beta_state_path.write_text("existing-state", encoding="utf-8")

            # 暂停不会加载该交易员，也不会删除其状态文件。
            save_config([alpha, {**beta, "enabled": False}])
            self.assertEqual([item.trader_name for item in load()], ["Alpha"])
            self.assertEqual(
                beta_state_path.read_text(encoding="utf-8"), "existing-state"
            )

            # 恢复继续使用同一个项目 ID 状态文件。
            save_config([alpha, beta])
            resumed = load()[1]
            self.assertEqual(resumed.state_path, beta_state_path)

            # 修改显示昵称不改变项目 ID 或去重状态路径。
            save_config([alpha, {**beta, "nickname": "Beta 新昵称"}])
            renamed = load()[1]
            self.assertEqual(renamed.trader_name, "Beta 新昵称")
            self.assertEqual(renamed.state_path, beta_state_path)

            # 从名单删除后，历史状态文件仍保留。
            save_config([alpha])
            self.assertEqual([item.trader_name for item in load()], ["Alpha"])
            self.assertTrue(beta_state_path.exists())

    def test_duplicate_ids_are_rejected_even_when_one_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            config_path = Path(directory_name) / "traders.json"
            config_path.write_text(
                json.dumps(
                    [
                        trader("Alpha", "1000000000000000001"),
                        trader("Beta", "1000000000000000001", False),
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {"TRADERS_CONFIG_PATH": str(config_path)},
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "项目 ID 重复"):
                    Settings.all_from_env(require_notifications=False)

    def test_enabled_must_be_a_json_boolean(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            config_path = Path(directory_name) / "traders.json"
            config_path.write_text(
                json.dumps(
                    [
                        {
                            "nickname": "Alpha",
                            "portfolio_id": "1000000000000000001",
                            "enabled": "false",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {"TRADERS_CONFIG_PATH": str(config_path)},
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "true 或 false"):
                    Settings.all_from_env(require_notifications=False)

    def test_secret_or_unknown_fields_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            config_path = Path(directory_name) / "traders.json"
            config_path.write_text(
                json.dumps(
                    [
                        {
                            **trader("Alpha", "1000000000000000001"),
                            "token": "must-not-be-here",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {"TRADERS_CONFIG_PATH": str(config_path)},
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "不允许的字段"):
                    Settings.all_from_env(require_notifications=False)

    def test_repository_config_has_only_public_fields(self) -> None:
        root = Path(__file__).resolve().parents[1]
        traders = json.loads((root / "traders.json").read_text(encoding="utf-8"))
        self.assertGreater(len(traders), 0)
        self.assertEqual(
            len({item["portfolio_id"] for item in traders}),
            len(traders),
        )
        for item in traders:
            self.assertEqual(
                set(item), {"nickname", "portfolio_id", "enabled"}
            )
            self.assertTrue(item["enabled"])

    def test_workflow_restores_all_state_files_without_hardcoded_traders(self) -> None:
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/monitor.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("git ls-tree -r --name-only origin/monitor-state", workflow)
        self.assertIn('TRADERS_CONFIG_PATH: "traders.json"', workflow)
        self.assertNotIn("BINANCE_TRADER_", workflow)
        self.assertNotIn("for portfolio_id in", workflow)

    def test_workflow_serializes_frequent_monitor_runs(self) -> None:
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/monitor.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("group: yingcapital-position-monitor", workflow)
        self.assertIn("queue: single", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("timeout-minutes: 5", workflow)


if __name__ == "__main__":
    unittest.main()
