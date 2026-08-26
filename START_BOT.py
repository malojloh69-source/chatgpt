"""Простой запуск Monster Contest Bot через Python.

Команда для Windows: py START_BOT.py
Также файл можно открыть двойным щелчком.
"""

from __future__ import annotations

import importlib.util
import getpass
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / ".venv"
TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
VENV_PYTHON = (
    VENV_DIR / "Scripts" / "python.exe"
    if os.name == "nt"
    else VENV_DIR / "bin" / "python"
)


def wait_before_close() -> None:
    try:
        input("\nНажмите Enter, чтобы закрыть окно...")
    except (EOFError, KeyboardInterrupt):
        pass


def ensure_env_file() -> None:
    """Create .env interactively on the first launch."""
    env_path = PROJECT_DIR / ".env"
    if env_path.is_file():
        return

    print("[0/3] Первая настройка бота.")
    print("Новый токен можно получить у @BotFather командой /newbot или /token.")
    for _ in range(3):
        token = getpass.getpass("Вставьте токен Telegram-бота: ").strip()
        if TOKEN_RE.fullmatch(token):
            break
        print("Токен выглядит неверно. Скопируйте его целиком из @BotFather.")
    else:
        raise RuntimeError("Не удалось получить корректный BOT_TOKEN")

    env_path.write_text(
        "BOT_TOKEN=" + token + "\n"
        "OWNER_IDS=\n"
        "ACCESS_CODE=MonsterLydka1488\n"
        "DATABASE_PATH=data/bot.sqlite3\n",
        encoding="utf-8",
    )
    print("Настройки сохранены в локальный файл .env. Не публикуйте этот файл.\n")


def create_virtual_environment() -> None:
    if VENV_PYTHON.exists():
        return
    print("[1/3] Создаю окружение Python...")
    subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])


def relaunch_inside_environment() -> int:
    current_python = Path(sys.executable).resolve()
    target_python = VENV_PYTHON.resolve()
    if current_python == target_python:
        return -1
    return subprocess.call(
        [str(VENV_PYTHON), str(Path(__file__).resolve())],
        cwd=PROJECT_DIR,
    )


def install_dependencies() -> None:
    missing = ["aiogram"] if importlib.util.find_spec("aiogram") is None else []
    if not missing:
        return
    print("[2/3] Устанавливаю библиотеки. Это требуется только при первом запуске...")
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            str(PROJECT_DIR / "requirements.txt"),
        ],
        cwd=PROJECT_DIR,
    )


def main() -> int:
    os.chdir(PROJECT_DIR)
    if sys.version_info < (3, 11):
        raise RuntimeError("Нужен Python 3.11 или новее")
    ensure_env_file()

    create_virtual_environment()
    child_exit_code = relaunch_inside_environment()
    if child_exit_code >= 0:
        return child_exit_code

    install_dependencies()
    print("[3/3] Запускаю Monster Contest Bot...")
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
