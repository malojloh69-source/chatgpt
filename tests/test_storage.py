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
        self.storage.upsert_chat(-1002, "News", "channel", "news")
        self.storage.set_group(42, -1001)
        self.storage.set_channel(42, -1002)

        self.assertEqual(
            self.storage.get_targets(42),
            UserTargets(group_id=-1001, channel_id=-1002),
        )
        groups = self.storage.list_chats(("group", "supergroup"))
        channels = self.storage.list_chats(("channel",))
        self.assertEqual([chat.chat_id for chat in groups], [-1001])
        self.assertEqual([chat.chat_id for chat in channels], [-1002])

    def test_upsert_refreshes_chat_title(self) -> None:
        self.storage.upsert_chat(-1001, "Old", "supergroup")
        self.storage.upsert_chat(-1001, "New", "supergroup")
        self.assertEqual(self.storage.get_chat(-1001).title, "New")


if __name__ == "__main__":
    unittest.main()
