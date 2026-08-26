from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.storage import BotStorage, UserTargets


class BotStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "bot.sqlite3"
        self.storage = BotStorage(self.path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_authorization_persists_between_instances(self) -> None:
        self.assertFalse(self.storage.is_authorized(42))
        self.storage.authorize(42)
        reopened = BotStorage(self.path)
        self.assertTrue(reopened.is_authorized(42))

    def test_chats_and_user_targets_are_stored(self) -> None:
        self.storage.upsert_chat(-1001, "Game chat", "supergroup", "game")
        self.storage.set_group(42, -1001)

        self.assertEqual(
            self.storage.get_targets(42),
            UserTargets(group_id=-1001),
        )
        groups = self.storage.list_chats(("group", "supergroup"))
        self.assertEqual([chat.chat_id for chat in groups], [-1001])

    def test_upsert_refreshes_chat_title(self) -> None:
        self.storage.upsert_chat(-1001, "Old", "supergroup")
        self.storage.upsert_chat(-1001, "New", "supergroup")
        self.assertEqual(self.storage.get_chat(-1001).title, "New")

    def test_drops_and_cases_are_saved_for_reuse(self) -> None:
        bear = self.storage.save_drop(42, "🧸", 0.05)
        heart = self.storage.save_drop(42, "💝", 0.04)
        saved_case = self.storage.save_case(
            42,
            "Lucky",
            10,
            600,
            [bear.drop_id, heart.drop_id],
            screenshot_kind="photo",
            screenshot_file_id="telegram-file-id",
        )

        reopened = BotStorage(self.path)
        loaded = reopened.get_case(42, saved_case.case_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "Lucky")
        self.assertEqual(loaded.stars, 10)
        self.assertEqual([drop.name for drop in loaded.drops], ["🧸", "💝"])
        self.assertEqual(len(reopened.list_drops(42)), 2)
        self.assertEqual(len(reopened.list_cases(42)), 1)

    def test_activity_setting_is_silent_and_persistent(self) -> None:
        self.assertFalse(self.storage.is_activity_enabled(-1001))
        self.storage.set_activity_enabled(-1001, True, 42)
        self.assertTrue(BotStorage(self.path).is_activity_enabled(-1001))
        self.storage.set_activity_enabled(-1001, False, 42)
        self.assertFalse(self.storage.is_activity_enabled(-1001))


if __name__ == "__main__":
    unittest.main()
