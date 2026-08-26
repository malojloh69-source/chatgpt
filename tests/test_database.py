from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.database import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.sqlite3")

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def create_game(self, kind: str, **kwargs):
        defaults = dict(
            kind=kind,
            channel_id=-1001,
            discussion_chat_id=-1002,
            creator_id=10,
            prize="https://t.me/nft/Test-1",
        )
        defaults.update(kwargs)
        game = self.db.create_game(**defaults)
        self.db.set_channel_message(game.id, 55)
        bound = self.db.bind_discussion_root(
            channel_id=-1001,
            channel_message_id=55,
            discussion_chat_id=-1002,
            root_message_id=77,
        )
        self.assertIsNotNone(bound)
        return bound

    def test_activation_is_persistent(self) -> None:
        self.assertFalse(self.db.is_activated(123))
        self.db.activate_user(123)
        path = self.db.path
        self.db.close()
        self.db = Database(path)
        self.assertTrue(self.db.is_activated(123))

    def test_venues_are_updated_and_filtered(self) -> None:
        self.db.upsert_venue(-1, "channel", "One", "one", True)
        self.db.upsert_venue(-2, "supergroup", "Discussion", None, True)
        self.assertEqual([item.title for item in self.db.list_channels()], ["One"])
        self.db.upsert_venue(-1, "channel", "Renamed", "one", False)
        self.assertEqual(self.db.list_channels(), [])

    def test_game_binds_only_to_its_comment_thread(self) -> None:
        game = self.create_game("casino", target_count=1)
        self.assertEqual(
            self.db.find_active_game(-1002, 77, None).id,
            game.id,
        )
        self.assertIsNone(self.db.find_active_game(-1002, 78, None))

    def test_casino_counts_777_per_user_until_target(self) -> None:
        game = self.create_game("casino", target_count=2)
        first = self.db.record_jackpot(game.id, 1, "Alice", "alice")
        self.assertIsNotNone(first)
        self.assertEqual(first.count, 1)
        self.assertFalse(first.won)

        second = self.db.record_jackpot(game.id, 1, "Alice", "alice")
        self.assertIsNotNone(second)
        self.assertEqual(second.count, 2)
        self.assertTrue(second.won)
        self.assertEqual(second.game.winner_user_id, 1)
        self.assertIsNone(self.db.record_jackpot(game.id, 2, "Bob", "bob"))

    def test_intercept_ignores_leaders_own_text_and_resets_for_another(self) -> None:
        game = self.create_game("intercept", duration_seconds=60, message_price=5)
        first = self.db.set_intercept_leader(game.id, 1, "Alice", "alice", now=100)
        self.assertTrue(first.accepted)
        self.assertTrue(first.first_leader)
        self.assertEqual(first.game.deadline, 160)

        same = self.db.set_intercept_leader(game.id, 1, "Alice", "alice", now=120)
        self.assertFalse(same.accepted)
        self.assertEqual(same.game.deadline, 160)

        takeover = self.db.set_intercept_leader(game.id, 2, "Bob", "bob", now=125)
        self.assertTrue(takeover.accepted)
        self.assertFalse(takeover.first_leader)
        self.assertEqual(takeover.game.deadline, 185)

        self.assertIsNone(self.db.complete_intercept_if_due(game.id, 160, now=200))
        self.assertIsNone(self.db.complete_intercept_if_due(game.id, 185, now=184))
        completed = self.db.complete_intercept_if_due(game.id, 185, now=185)
        self.assertIsNotNone(completed)
        self.assertEqual(completed.winner_user_id, 2)

    def test_guess_completes_only_on_secret_number(self) -> None:
        game = self.create_game("guess", secret_number=42)
        self.assertIsNone(
            self.db.complete_guess_if_correct(game.id, 41, 1, "Alice", "alice")
        )
        winner = self.db.complete_guess_if_correct(game.id, 42, 2, "Bob", "bob")
        self.assertIsNotNone(winner)
        self.assertEqual(winner.winner_user_id, 2)
        self.assertIsNone(
            self.db.complete_guess_if_correct(game.id, 42, 1, "Alice", "alice")
        )

    def test_guess_without_secret_is_safely_ignored(self) -> None:
        game = self.create_game("guess")
        self.assertIsNone(
            self.db.complete_guess_if_correct(game.id, 42, 1, "Alice", "alice")
        )

    def test_only_creator_can_cancel_game(self) -> None:
        game = self.create_game("casino", target_count=1)
        self.assertIsNone(self.db.cancel_game(game.id, creator_id=999))
        cancelled = self.db.cancel_game(game.id, creator_id=10)
        self.assertIsNotNone(cancelled)
        self.assertEqual(cancelled.status, "cancelled")


if __name__ == "__main__":
    unittest.main()
