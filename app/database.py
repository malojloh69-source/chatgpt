from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Venue:
    chat_id: int
    chat_type: str
    title: str
    username: str | None
    active: bool


@dataclass(frozen=True, slots=True)
class Game:
    id: int
    kind: str
    channel_id: int
    discussion_chat_id: int
    creator_id: int
    prize: str
    screenshot_file_id: str | None
    target_count: int | None
    duration_seconds: int | None
    message_price: int | None
    secret_number: int | None
    status: str
    channel_message_id: int | None
    discussion_root_message_id: int | None
    leader_user_id: int | None
    leader_name: str | None
    leader_username: str | None
    deadline: float | None
    winner_user_id: int | None
    winner_name: str | None
    winner_username: str | None
    created_at: float
    completed_at: float | None


@dataclass(frozen=True, slots=True)
class JackpotResult:
    game: Game
    count: int
    won: bool


@dataclass(frozen=True, slots=True)
class LeaderResult:
    game: Game
    accepted: bool
    first_leader: bool


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS activated_users (
                user_id INTEGER PRIMARY KEY,
                activated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS venues (
                chat_id INTEGER PRIMARY KEY,
                chat_type TEXT NOT NULL,
                title TEXT NOT NULL,
                username TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK(kind IN ('casino', 'intercept', 'guess')),
                channel_id INTEGER NOT NULL,
                discussion_chat_id INTEGER NOT NULL,
                creator_id INTEGER NOT NULL,
                prize TEXT NOT NULL,
                screenshot_file_id TEXT,
                target_count INTEGER,
                duration_seconds INTEGER,
                message_price INTEGER,
                secret_number INTEGER,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active', 'completed', 'cancelled', 'failed')),
                channel_message_id INTEGER,
                discussion_root_message_id INTEGER,
                leader_user_id INTEGER,
                leader_name TEXT,
                leader_username TEXT,
                deadline REAL,
                winner_user_id INTEGER,
                winner_name TEXT,
                winner_username TEXT,
                created_at REAL NOT NULL,
                completed_at REAL
            );

            CREATE INDEX IF NOT EXISTS games_discussion_idx
                ON games(discussion_chat_id, discussion_root_message_id, status);
            CREATE INDEX IF NOT EXISTS games_channel_message_idx
                ON games(channel_id, channel_message_id, status);

            CREATE TABLE IF NOT EXISTS casino_scores (
                game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL,
                full_name TEXT NOT NULL,
                username TEXT,
                jackpot_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(game_id, user_id)
            );
            """
        )
        self.connection.commit()

    @staticmethod
    def _game(row: sqlite3.Row | None) -> Game | None:
        return Game(**dict(row)) if row is not None else None

    def activate_user(self, user_id: int) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO activated_users(user_id, activated_at) VALUES (?, ?)",
                (user_id, time.time()),
            )

    def is_activated(self, user_id: int) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM activated_users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row is not None

    def upsert_venue(
        self,
        chat_id: int,
        chat_type: str,
        title: str,
        username: str | None,
        active: bool,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO venues(chat_id, chat_type, title, username, active, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    chat_type=excluded.chat_type,
                    title=excluded.title,
                    username=excluded.username,
                    active=excluded.active,
                    updated_at=excluded.updated_at
                """,
                (chat_id, chat_type, title, username, int(active), time.time()),
            )

    def list_channels(self) -> list[Venue]:
        rows = self.connection.execute(
            """
            SELECT chat_id, chat_type, title, username, active
            FROM venues
            WHERE chat_type = 'channel' AND active = 1
            ORDER BY title COLLATE NOCASE
            """
        ).fetchall()
        return [Venue(**{**dict(row), "active": bool(row["active"])}) for row in rows]

    def create_game(
        self,
        *,
        kind: str,
        channel_id: int,
        discussion_chat_id: int,
        creator_id: int,
        prize: str,
        screenshot_file_id: str | None = None,
        target_count: int | None = None,
        duration_seconds: int | None = None,
        message_price: int | None = None,
        secret_number: int | None = None,
    ) -> Game:
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO games(
                    kind, channel_id, discussion_chat_id, creator_id, prize,
                    screenshot_file_id, target_count, duration_seconds,
                    message_price, secret_number, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    kind,
                    channel_id,
                    discussion_chat_id,
                    creator_id,
                    prize,
                    screenshot_file_id,
                    target_count,
                    duration_seconds,
                    message_price,
                    secret_number,
                    time.time(),
                ),
            )
            game_id = int(cursor.lastrowid)
        game = self.get_game(game_id)
        assert game is not None
        return game

    def get_game(self, game_id: int) -> Game | None:
        return self._game(
            self.connection.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        )

    def set_channel_message(self, game_id: int, message_id: int) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE games SET channel_message_id = ? WHERE id = ? AND status = 'active'",
                (message_id, game_id),
            )

    def mark_failed(self, game_id: int) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE games SET status = 'failed', completed_at = ? WHERE id = ?",
                (time.time(), game_id),
            )

    def bind_discussion_root(
        self,
        *,
        channel_id: int,
        channel_message_id: int,
        discussion_chat_id: int,
        root_message_id: int,
    ) -> Game | None:
        with self.connection:
            row = self.connection.execute(
                """
                SELECT * FROM games
                WHERE channel_id = ? AND channel_message_id = ? AND status = 'active'
                ORDER BY id DESC LIMIT 1
                """,
                (channel_id, channel_message_id),
            ).fetchone()
            if row is None:
                row = self.connection.execute(
                    """
                    SELECT * FROM games
                    WHERE channel_id = ? AND channel_message_id IS NULL
                      AND status = 'active' AND created_at >= ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (channel_id, time.time() - 120),
                ).fetchone()
            if row is None:
                return None
            self.connection.execute(
                """
                UPDATE games
                SET discussion_chat_id = ?, discussion_root_message_id = ?
                WHERE id = ?
                """,
                (discussion_chat_id, root_message_id, row["id"]),
            )
        return self.get_game(int(row["id"]))

    def find_active_game(
        self,
        discussion_chat_id: int,
        thread_id: int | None,
        reply_message_id: int | None,
    ) -> Game | None:
        roots = {root for root in (thread_id, reply_message_id) if root is not None}
        if not roots:
            return None
        placeholders = ",".join("?" for _ in roots)
        row = self.connection.execute(
            f"""
            SELECT * FROM games
            WHERE discussion_chat_id = ? AND status = 'active'
              AND discussion_root_message_id IN ({placeholders})
            ORDER BY id DESC LIMIT 1
            """,
            (discussion_chat_id, *roots),
        ).fetchone()
        return self._game(row)

    def record_jackpot(
        self,
        game_id: int,
        user_id: int,
        full_name: str,
        username: str | None,
    ) -> JackpotResult | None:
        with self.connection:
            row = self.connection.execute(
                "SELECT * FROM games WHERE id = ? AND status = 'active' AND kind = 'casino'",
                (game_id,),
            ).fetchone()
            if row is None:
                return None
            target = int(row["target_count"] or 1)
            score = self.connection.execute(
                "SELECT jackpot_count FROM casino_scores WHERE game_id = ? AND user_id = ?",
                (game_id, user_id),
            ).fetchone()
            count = int(score["jackpot_count"] if score else 0) + 1
            self.connection.execute(
                """
                INSERT INTO casino_scores(game_id, user_id, full_name, username, jackpot_count)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(game_id, user_id) DO UPDATE SET
                    full_name=excluded.full_name,
                    username=excluded.username,
                    jackpot_count=excluded.jackpot_count
                """,
                (game_id, user_id, full_name, username, count),
            )
            won = count >= target
            if won:
                self.connection.execute(
                    """
                    UPDATE games SET status='completed', winner_user_id=?, winner_name=?,
                        winner_username=?, completed_at=?
                    WHERE id=? AND status='active'
                    """,
                    (user_id, full_name, username, time.time(), game_id),
                )
        game = self.get_game(game_id)
        assert game is not None
        return JackpotResult(game, count, won)

    def set_intercept_leader(
        self,
        game_id: int,
        user_id: int,
        full_name: str,
        username: str | None,
        now: float | None = None,
    ) -> LeaderResult | None:
        current_time = time.time() if now is None else now
        with self.connection:
            row = self.connection.execute(
                """
                SELECT * FROM games
                WHERE id = ? AND status = 'active' AND kind = 'intercept'
                """,
                (game_id,),
            ).fetchone()
            if row is None:
                return None
            game = self._game(row)
            assert game is not None
            if game.leader_user_id == user_id:
                return LeaderResult(game, False, False)
            first = game.leader_user_id is None
            deadline = current_time + int(game.duration_seconds or 120)
            self.connection.execute(
                """
                UPDATE games SET leader_user_id=?, leader_name=?, leader_username=?, deadline=?
                WHERE id=? AND status='active'
                """,
                (user_id, full_name, username, deadline, game_id),
            )
        updated = self.get_game(game_id)
        assert updated is not None
        return LeaderResult(updated, True, first)

    def complete_intercept_if_due(
        self, game_id: int, expected_deadline: float, now: float | None = None
    ) -> Game | None:
        current_time = time.time() if now is None else now
        with self.connection:
            row = self.connection.execute(
                """
                SELECT * FROM games
                WHERE id=? AND status='active' AND kind='intercept'
                  AND leader_user_id IS NOT NULL
                """,
                (game_id,),
            ).fetchone()
            if row is None:
                return None
            deadline = float(row["deadline"] or 0)
            if abs(deadline - expected_deadline) > 0.001 or deadline > current_time:
                return None
            self.connection.execute(
                """
                UPDATE games SET status='completed', winner_user_id=leader_user_id,
                    winner_name=leader_name, winner_username=leader_username, completed_at=?
                WHERE id=? AND status='active'
                """,
                (current_time, game_id),
            )
        return self.get_game(game_id)

    def complete_guess_if_correct(
        self,
        game_id: int,
        number: int,
        user_id: int,
        full_name: str,
        username: str | None,
    ) -> Game | None:
        with self.connection:
            row = self.connection.execute(
                """
                SELECT secret_number FROM games
                WHERE id=? AND status='active' AND kind='guess'
                """,
                (game_id,),
            ).fetchone()
            if (
                row is None
                or row["secret_number"] is None
                or int(row["secret_number"]) != number
            ):
                return None
            self.connection.execute(
                """
                UPDATE games SET status='completed', winner_user_id=?, winner_name=?,
                    winner_username=?, completed_at=?
                WHERE id=? AND status='active'
                """,
                (user_id, full_name, username, time.time(), game_id),
            )
        return self.get_game(game_id)

    def list_active_for_creator(self, creator_id: int) -> list[Game]:
        rows = self.connection.execute(
            """
            SELECT * FROM games WHERE creator_id=? AND status='active'
            ORDER BY id DESC LIMIT 30
            """,
            (creator_id,),
        ).fetchall()
        return [self._game(row) for row in rows if row is not None]  # type: ignore[misc]

    def active_intercepts_with_leader(self) -> list[Game]:
        rows = self.connection.execute(
            """
            SELECT * FROM games
            WHERE status='active' AND kind='intercept'
              AND leader_user_id IS NOT NULL AND deadline IS NOT NULL
            """
        ).fetchall()
        return [self._game(row) for row in rows if row is not None]  # type: ignore[misc]

    def cancel_game(self, game_id: int, creator_id: int | None = None) -> Game | None:
        with self.connection:
            row = self.connection.execute(
                "SELECT * FROM games WHERE id=? AND status='active'", (game_id,)
            ).fetchone()
            if row is None:
                return None
            if creator_id is not None and int(row["creator_id"]) != creator_id:
                return None
            self.connection.execute(
                "UPDATE games SET status='cancelled', completed_at=? WHERE id=?",
                (time.time(), game_id),
            )
        return self.get_game(game_id)

    def close(self) -> None:
        self.connection.close()
