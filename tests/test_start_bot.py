from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import START_BOT


class StartBotTests(unittest.TestCase):
    def test_first_launch_creates_local_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            token = "123456789:" + "A" * 35
            with (
                patch.object(START_BOT, "PROJECT_DIR", project_dir),
                patch("START_BOT.getpass.getpass", return_value=token),
                patch("builtins.print"),
            ):
                START_BOT.ensure_env_file()

            content = (project_dir / ".env").read_text(encoding="utf-8")
            self.assertIn(f"BOT_TOKEN={token}", content)
            self.assertIn("ACCESS_CODE=MonsterLydka1488", content)

    def test_existing_env_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            env_path = project_dir / ".env"
            env_path.write_text("BOT_TOKEN=keep-me\n", encoding="utf-8")
            with (
                patch.object(START_BOT, "PROJECT_DIR", project_dir),
                patch("START_BOT.getpass.getpass") as get_token,
            ):
                START_BOT.ensure_env_file()

            get_token.assert_not_called()
            self.assertEqual(env_path.read_text(encoding="utf-8"), "BOT_TOKEN=keep-me\n")


if __name__ == "__main__":
    unittest.main()
