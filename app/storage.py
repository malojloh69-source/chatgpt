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
