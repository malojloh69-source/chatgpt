from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file() -> None:
    """Load simple KEY=VALUE settings without third-party dependencies."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def _parse_owner_ids(raw: str) -> frozenset[int]:
    if not raw.strip():
        return frozenset()
    try:
        return frozenset(int(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError as exc:
        raise RuntimeError("OWNER_IDS должен содержать Telegram ID через запятую") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    owner_ids: frozenset[int]
    access_code: str
    database_path: Path

    @classmethod
    def from_env(cls) -> "Settings":
        _load_env_file()
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "Не задан BOT_TOKEN. Скопируйте .env.example в .env и вставьте токен бота."
            )

        return cls(
            bot_token=token,
            owner_ids=_parse_owner_ids(os.getenv("OWNER_IDS", "")),
            access_code=os.getenv("ACCESS_CODE", "MonsterLydka1488").strip().lstrip("/"),
            database_path=Path(
                os.getenv(
                    "DATABASE_PATH",
                    str(Path(__file__).resolve().parent.parent / "data" / "bot.sqlite3"),
                )
            ),
        )
