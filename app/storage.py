from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class KnownChat:
    chat_id: int
    title: str
    kind: str
    username: str | None = None


@dataclass(frozen=True, slots=True)
class UserTargets:
    group_id: int | None = None


@dataclass(frozen=True, slots=True)
class SavedDrop:
    drop_id: int
    owner_id: int
    name: str
    chance: float


@dataclass(frozen=True, slots=True)
class SavedCase:
    case_id: int
    owner_id: int
    name: str
    stars: int
    duration_seconds: int
    screenshot_kind: str | None
    screenshot_file_id: str | None
    drops: tuple[SavedDrop, ...]


class BotStorage:
    """Small persistent registry for access and selected Telegram chats."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._create_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _create_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS authorized_users (
                    user_id INTEGER PRIMARY KEY,
                    authorized_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS known_chats (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    username TEXT,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_targets (
                    user_id INTEGER PRIMARY KEY,
                    group_id INTEGER
                );
                CREATE TABLE IF NOT EXISTS saved_drops (
                    drop_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    chance REAL NOT NULL CHECK(chance >= 0 AND chance <= 100),
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS saved_cases (
                    case_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    stars INTEGER NOT NULL CHECK(stars >= 0),
                    duration_seconds INTEGER NOT NULL CHECK(duration_seconds > 0),
                    screenshot_kind TEXT,
                    screenshot_file_id TEXT,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS saved_case_drops (
                    case_id INTEGER NOT NULL,
                    drop_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY(case_id, position),
                    FOREIGN KEY(case_id) REFERENCES saved_cases(case_id) ON DELETE CASCADE,
                    FOREIGN KEY(drop_id) REFERENCES saved_drops(drop_id)
                );
                CREATE TABLE IF NOT EXISTS activity_gifts (
                    chat_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    updated_by INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )

    def authorize(self, user_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO authorized_users(user_id, authorized_at)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET authorized_at=excluded.authorized_at
                """,
                (user_id, int(time.time())),
            )

    def is_authorized(self, user_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM authorized_users WHERE user_id=?", (user_id,)
            ).fetchone()
        return row is not None

    def upsert_chat(
        self,
        chat_id: int,
        title: str,
        kind: str,
        username: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO known_chats(chat_id, title, kind, username, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title=excluded.title,
                    kind=excluded.kind,
                    username=excluded.username,
                    updated_at=excluded.updated_at
                """,
                (chat_id, title, kind, username, int(time.time())),
            )

    def list_chats(self, kinds: tuple[str, ...]) -> list[KnownChat]:
        placeholders = ",".join("?" for _ in kinds)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT chat_id, title, kind, username
                FROM known_chats
                WHERE kind IN ({placeholders})
                ORDER BY updated_at DESC, title COLLATE NOCASE
                LIMIT 50
                """,
                kinds,
            ).fetchall()
        return [
            KnownChat(
                chat_id=row["chat_id"],
                title=row["title"],
                kind=row["kind"],
                username=row["username"],
            )
            for row in rows
        ]

    def get_chat(self, chat_id: int | None) -> KnownChat | None:
        if chat_id is None:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT chat_id, title, kind, username
                FROM known_chats WHERE chat_id=?
                """,
                (chat_id,),
            ).fetchone()
        if row is None:
            return None
        return KnownChat(
            chat_id=row["chat_id"],
            title=row["title"],
            kind=row["kind"],
            username=row["username"],
        )

    def get_targets(self, user_id: int) -> UserTargets:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT group_id FROM user_targets WHERE user_id=?",
                (user_id,),
            ).fetchone()
        if row is None:
            return UserTargets()
        return UserTargets(group_id=row["group_id"])

    def set_group(self, user_id: int, chat_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO user_targets(user_id, group_id)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET group_id=excluded.group_id
                """,
                (user_id, chat_id),
            )

    def save_drop(self, owner_id: int, name: str, chance: float) -> SavedDrop:
        value = name.strip()
        if not value:
            raise ValueError("drop name must not be empty")
        if not 0 <= chance <= 100:
            raise ValueError("chance must be between 0 and 100")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO saved_drops(owner_id, name, chance, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (owner_id, value, chance, int(time.time())),
            )
            drop_id = int(cursor.lastrowid)
        return SavedDrop(drop_id, owner_id, value, chance)

    def get_drop(self, owner_id: int, drop_id: int) -> SavedDrop | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT drop_id, owner_id, name, chance
                FROM saved_drops
                WHERE owner_id=? AND drop_id=?
                """,
                (owner_id, drop_id),
            ).fetchone()
        return self._drop_from_row(row) if row is not None else None

    def list_drops(self, owner_id: int, limit: int = 50) -> list[SavedDrop]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT drop_id, owner_id, name, chance
                FROM saved_drops
                WHERE owner_id=?
                ORDER BY drop_id DESC
                LIMIT ?
                """,
                (owner_id, limit),
            ).fetchall()
        return [self._drop_from_row(row) for row in rows]

    @staticmethod
    def _drop_from_row(row: sqlite3.Row) -> SavedDrop:
        return SavedDrop(
            drop_id=row["drop_id"],
            owner_id=row["owner_id"],
            name=row["name"],
            chance=float(row["chance"]),
        )

    def save_case(
        self,
        owner_id: int,
        name: str,
        stars: int,
        duration_seconds: int,
        drop_ids: list[int],
        *,
        screenshot_kind: str | None = None,
        screenshot_file_id: str | None = None,
    ) -> SavedCase:
        value = name.strip()
        if not value or not drop_ids:
            raise ValueError("case name and drops are required")
        if stars < 0 or duration_seconds <= 0:
            raise ValueError("invalid case price or duration")
        with self._connect() as connection:
            placeholders = ",".join("?" for _ in drop_ids)
            owned = connection.execute(
                f"""
                SELECT drop_id FROM saved_drops
                WHERE owner_id=? AND drop_id IN ({placeholders})
                """,
                (owner_id, *drop_ids),
            ).fetchall()
            if {int(row["drop_id"]) for row in owned} != set(drop_ids):
                raise ValueError("all drops must belong to the case owner")
            cursor = connection.execute(
                """
                INSERT INTO saved_cases(
                    owner_id, name, stars, duration_seconds,
                    screenshot_kind, screenshot_file_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    owner_id,
                    value,
                    stars,
                    duration_seconds,
                    screenshot_kind,
                    screenshot_file_id,
                    int(time.time()),
                ),
            )
            case_id = int(cursor.lastrowid)
            connection.executemany(
                """
                INSERT INTO saved_case_drops(case_id, drop_id, position)
                VALUES (?, ?, ?)
                """,
                [(case_id, drop_id, position) for position, drop_id in enumerate(drop_ids)],
            )
        saved = self.get_case(owner_id, case_id)
        if saved is None:  # pragma: no cover - protected by the transaction above
            raise RuntimeError("saved case disappeared")
        return saved

    def get_case(self, owner_id: int, case_id: int) -> SavedCase | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT case_id, owner_id, name, stars, duration_seconds,
                       screenshot_kind, screenshot_file_id
                FROM saved_cases
                WHERE owner_id=? AND case_id=?
                """,
                (owner_id, case_id),
            ).fetchone()
            if row is None:
                return None
            drop_rows = connection.execute(
                """
                SELECT d.drop_id, d.owner_id, d.name, d.chance
                FROM saved_case_drops cd
                JOIN saved_drops d ON d.drop_id=cd.drop_id
                WHERE cd.case_id=?
                ORDER BY cd.position
                """,
                (case_id,),
            ).fetchall()
        return SavedCase(
            case_id=row["case_id"],
            owner_id=row["owner_id"],
            name=row["name"],
            stars=row["stars"],
            duration_seconds=row["duration_seconds"],
            screenshot_kind=row["screenshot_kind"],
            screenshot_file_id=row["screenshot_file_id"],
            drops=tuple(self._drop_from_row(drop_row) for drop_row in drop_rows),
        )

    def list_cases(self, owner_id: int, limit: int = 25) -> list[SavedCase]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT case_id FROM saved_cases
                WHERE owner_id=?
                ORDER BY case_id DESC
                LIMIT ?
                """,
                (owner_id, limit),
            ).fetchall()
        cases: list[SavedCase] = []
        for row in rows:
            saved = self.get_case(owner_id, int(row["case_id"]))
            if saved is not None:
                cases.append(saved)
        return cases

    def set_activity_enabled(self, chat_id: int, enabled: bool, user_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO activity_gifts(chat_id, enabled, updated_by, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    enabled=excluded.enabled,
                    updated_by=excluded.updated_by,
                    updated_at=excluded.updated_at
                """,
                (chat_id, int(enabled), user_id, int(time.time())),
            )

    def is_activity_enabled(self, chat_id: int | None) -> bool:
        if chat_id is None:
            return False
        with self._connect() as connection:
            row = connection.execute(
                "SELECT enabled FROM activity_gifts WHERE chat_id=?", (chat_id,)
            ).fetchone()
        return bool(row["enabled"]) if row is not None else False
