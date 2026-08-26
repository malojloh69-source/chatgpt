"""Единственный файл запуска Monster Contest Bot.

Windows: py main.py
Также файл можно открыть двойным щелчком.
"""

from __future__ import annotations

import getpass
import importlib.metadata
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / ".venv"
VENV_PYTHON = (
    VENV_DIR / "Scripts" / "python.exe"
    if os.name == "nt"
    else VENV_DIR / "bin" / "python"
)
AIROGRAM_VERSION = "3.30.0"
TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")


def wait_before_close() -> None:
    try:
        input("\nНажмите Enter, чтобы закрыть окно...")
    except (EOFError, KeyboardInterrupt):
        pass


def ensure_settings() -> None:
    """Запросить токен и создать локальные настройки при первом запуске."""
    if os.getenv("BOT_TOKEN", "").strip():
        print("[1/4] Токен получен из переменных окружения хостинга.")
        return

    env_path = PROJECT_DIR / ".env"
    if env_path.is_file():
        return

    if not sys.stdin.isatty():
        raise RuntimeError(
            "Для запуска на хостинге добавьте секрет BOT_TOKEN в Environment Variables"
        )

    print("[1/4] Первая настройка бота")
    print("Получите новый токен у @BotFather и вставьте его ниже.")
    for _ in range(3):
        token = getpass.getpass("Токен Telegram-бота: ").strip()
        if TOKEN_RE.fullmatch(token):
            break
        print("Токен выглядит неверно. Скопируйте его из @BotFather целиком.")
    else:
        raise RuntimeError("Не удалось получить корректный токен")

    env_path.write_text(
        "BOT_TOKEN=" + token + "\n"
        "OWNER_IDS=\n"
        "ACCESS_CODE=/MonsterLydka1488\n"
        "DATA_FILE=monster_bot.sqlite3\n"
        "INTERCEPT_SECONDS=120\n"
        "CASINO_COOLDOWN_SECONDS=3\n"
        "PRIZE_CALL=@Monster_Tags, выдай приз победителю!\n",
        encoding="utf-8",
    )
    print("Настройки сохранены локально. Файл .env нельзя публиковать.\n")


def create_virtual_environment() -> None:
    if VENV_PYTHON.exists():
        return
    print("[2/4] Создаю виртуальное окружение...")
    subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])


def relaunch_inside_environment() -> int:
    if Path(sys.executable).resolve() == VENV_PYTHON.resolve():
        return -1
    return subprocess.call(
        [str(VENV_PYTHON), str(Path(__file__).resolve())],
        cwd=PROJECT_DIR,
    )


def install_dependencies() -> None:
    try:
        installed_version = importlib.metadata.version("aiogram")
    except importlib.metadata.PackageNotFoundError:
        installed_version = None
    if installed_version == AIROGRAM_VERSION:
        return

    print("[3/4] Устанавливаю aiogram. Это требуется только при первом запуске...")
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            f"aiogram=={AIROGRAM_VERSION}",
        ],
        cwd=PROJECT_DIR,
    )


def main() -> int:
    os.chdir(PROJECT_DIR)
    if sys.version_info < (3, 11):
        raise RuntimeError("Нужен Python 3.11 или новее")

    hosted = bool(os.getenv("BOT_TOKEN", "").strip())
    ensure_settings()
    if not hosted:
        create_virtual_environment()
        child_exit_code = relaunch_inside_environment()
        if child_exit_code >= 0:
            return child_exit_code

    install_dependencies()
    print("[4/4] Запускаю Monster Contest Bot...")
    print("Чтобы остановить бота, нажмите Ctrl+C.\n")

    from app.bot import run

    run()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except Exception:
        print("\nНе удалось запустить бота:\n", file=sys.stderr)
        traceback.print_exc()
        wait_before_close()
        raise SystemExit(1)
