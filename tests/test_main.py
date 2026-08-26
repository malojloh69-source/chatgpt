from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class MainLauncherTests(unittest.TestCase):
    def test_first_launch_creates_env_with_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            token = "123456789:" + "A" * 35
            with (
                patch.object(main, "PROJECT_DIR", project_dir),
                patch("main.getpass.getpass", return_value=token),
                patch("builtins.print"),
            ):
                main.ensure_settings()

            settings = (project_dir / ".env").read_text(encoding="utf-8")
            self.assertIn(f"BOT_TOKEN={token}", settings)
            self.assertIn("ACCESS_CODE=/MonsterLydka1488", settings)

    def test_existing_env_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            env_path = project_dir / ".env"
            env_path.write_text("BOT_TOKEN=keep\n", encoding="utf-8")
            with (
                patch.object(main, "PROJECT_DIR", project_dir),
                patch("main.getpass.getpass") as get_token,
            ):
                main.ensure_settings()

            get_token.assert_not_called()
            self.assertEqual(env_path.read_text(encoding="utf-8"), "BOT_TOKEN=keep\n")

    def test_dependency_is_installed_without_requirements_file(self) -> None:
        with (
            patch(
                "main.importlib.metadata.version",
                side_effect=main.importlib.metadata.PackageNotFoundError,
            ),
            patch("main.subprocess.check_call") as install,
            patch("builtins.print"),
        ):
            main.install_dependencies()

        command = install.call_args.args[0]
        self.assertIn("aiogram==3.30.0", command)
        self.assertNotIn("-r", command)


if __name__ == "__main__":
    unittest.main()
