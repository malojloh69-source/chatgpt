from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot import (
    CaseSetup,
    CasinoSetup,
    ContestBot,
    GuessSetup,
    InterceptSetup,
    game_key,
)
from app.config import Settings
from app.storage import BotStorage


class FakeMessage:
    def __init__(self, text: str | None = None, user_id: int = 42) -> None:
        # Keep type as a plain string: this matches the representation that
        # exposed the original identity-comparison bug.
        self.chat = SimpleNamespace(type="private")
        self.from_user = SimpleNamespace(id=user_id)
        self.text = text
        self.answers: list[tuple[str, object | None]] = []

    async def answer(self, text: str, reply_markup=None):
        self.answers.append((text, reply_markup))


class FakeGroupMessage:
    def __init__(
        self,
        *,
        value: int | None = None,
        user_id: int = 100,
        is_bot: bool = False,
        sender_chat=None,
        is_automatic_forward: bool = False,
        text: str | None = None,
        message_id: int = 10,
    ) -> None:
        self.chat = SimpleNamespace(
            id=-1001,
            type="supergroup",
            title="Game chat",
            username="game_chat",
        )
        self.from_user = SimpleNamespace(
            id=user_id,
            is_bot=is_bot,
            full_name="Player",
            username="player",
        )
        self.sender_chat = sender_chat
        self.is_automatic_forward = is_automatic_forward
        self.text = text
        self.message_id = message_id
        self.dice = (
            SimpleNamespace(emoji="🎰", value=value) if value is not None else None
        )
        self.replies: list[str] = []

    async def reply(self, text: str):
        self.replies.append(text)


class PrivatePanelFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        settings = Settings(
            "123456789:" + "A" * 35, frozenset()
        )
        self.bot = Bot(settings.bot_token)
        self.storage = BotStorage(Path(self.temp_dir.name) / "bot.sqlite3")
        self.contest_bot = ContestBot(self.bot, settings, self.storage)
        self.memory = MemoryStorage()
        self.state = FSMContext(
            self.memory,
            StorageKey(bot_id=self.bot.id, chat_id=42, user_id=42),
        )

    async def asyncTearDown(self) -> None:
        await self.contest_bot.manager.close()
        await self.memory.close()
        await self.bot.session.close()
        self.temp_dir.cleanup()

    async def test_promo_then_start_responds(self) -> None:
        promo = FakeMessage("/MonsterLydka1488")
        await self.contest_bot.cmd_access(promo, self.state)
        self.assertTrue(self.storage.is_authorized(42))
        self.assertTrue(promo.answers)

        start = FakeMessage("/start")
        await self.state.set_state(CasinoSetup.prize)
        await self.contest_bot.cmd_start(start, self.state)
        self.assertTrue(start.answers)
        self.assertIn("Monster Contest Bot", start.answers[-1][0])
        self.assertIsNone(await self.state.get_state())

    async def test_start_does_not_reveal_access_code(self) -> None:
        start = FakeMessage("/start", user_id=77)
        await self.contest_bot.cmd_start(start, self.state)
        self.assertTrue(start.answers)
        self.assertNotIn("MonsterLydka1488", start.answers[-1][0])

    async def test_casino_text_fields_advance(self) -> None:
        self.storage.authorize(42)
        await self.state.set_state(CasinoSetup.prize)

        prize = FakeMessage("https://t.me/nft/ViceCream-431517")
        await self.contest_bot.receive_casino_prize(prize, self.state)
        self.assertEqual(await self.state.get_state(), CasinoSetup.jackpot_target.state)
        self.assertTrue(prize.answers)

        target = FakeMessage("3")
        await self.contest_bot.receive_casino_target(target, self.state)
        self.assertEqual(await self.state.get_state(), CasinoSetup.screenshot.state)
        self.assertTrue(target.answers)

    async def test_intercept_text_fields_advance(self) -> None:
        self.storage.authorize(42)
        await self.state.set_state(InterceptSetup.prize)

        prize = FakeMessage("NFT prize")
        await self.contest_bot.receive_intercept_prize(prize, self.state)
        self.assertEqual(await self.state.get_state(), InterceptSetup.duration.state)

        duration = FakeMessage("2м")
        await self.contest_bot.receive_intercept_duration(duration, self.state)
        self.assertEqual(await self.state.get_state(), InterceptSetup.stars.state)

        stars = FakeMessage("10")
        await self.contest_bot.receive_intercept_stars(stars, self.state)
        self.assertEqual(await self.state.get_state(), InterceptSetup.screenshot.state)

    def _prepare_targets(self) -> None:
        self.storage.authorize(42)
        self.storage.upsert_chat(-1001, "Game chat", "supergroup")
        self.storage.set_group(42, -1001)
        self.bot.get_chat_member = AsyncMock(
            return_value=SimpleNamespace(status=ChatMemberStatus.ADMINISTRATOR)
        )
        self.bot.send_message = AsyncMock(
            return_value=SimpleNamespace(message_id=1)
        )

    async def test_completed_casino_form_starts_game(self) -> None:
        self._prepare_targets()
        await self.state.set_state(CasinoSetup.screenshot)
        await self.state.set_data({"prize": "NFT prize", "jackpot_target": 3})

        message = FakeMessage()
        await self.contest_bot._finish_casino_setup(message, self.state)

        active = await self.contest_bot.manager.snapshot(game_key(-1001))
        self.assertIsNotNone(active)
        self.assertEqual(active.jackpot_target, 3)
        self.assertEqual(active.prize, "NFT prize")
        self.assertEqual(active.tracking_after_message_id, 1)
        self.assertIsNone(await self.state.get_state())
        self.assertEqual(self.bot.send_message.await_count, 1)

    async def test_rapid_native_777_is_not_lost_to_cooldown(self) -> None:
        await self.contest_bot.manager.start_casino(
            game_key(-1001),
            prize="Prize",
            jackpot_target=1,
        )
        miss = FakeGroupMessage(value=1)
        jackpot = FakeGroupMessage(value=64)
        self.contest_bot._announce_winner = AsyncMock()

        await self.contest_bot.on_message(miss)
        with patch("app.bot.asyncio.sleep", new=AsyncMock()):
            await self.contest_bot.on_message(jackpot)

        self.assertTrue(jackpot.replies)
        self.assertIn("777! Победа засчитана", jackpot.replies[-1])
        self.contest_bot._announce_winner.assert_awaited_once()
        self.assertIsNone(
            await self.contest_bot.manager.snapshot(game_key(-1001))
        )

    async def test_intercept_ignores_bot_and_automatic_forwarded_posts(self) -> None:
        await self.contest_bot.manager.start_intercept(game_key(-1001), 120)

        automatic = FakeGroupMessage(
            text="Forwarded post",
            sender_chat=SimpleNamespace(id=-1002),
            is_automatic_forward=True,
        )
        own = FakeGroupMessage(text="Bot reply", user_id=self.bot.id, is_bot=True)
        await self.contest_bot.on_message(automatic)
        await self.contest_bot.on_message(own)

        untouched = await self.contest_bot.manager.snapshot(game_key(-1001))
        self.assertIsNone(untouched.leader)

        human = FakeGroupMessage(text="Real player message")
        await self.contest_bot.on_message(human)
        active = await self.contest_bot.manager.snapshot(game_key(-1001))
        self.assertEqual(active.leader.user_id, 100)

    async def test_tracking_starts_only_after_bots_group_message(self) -> None:
        await self.contest_bot.manager.start_intercept(
            game_key(-1001), 120, tracking_after_message_id=50
        )

        old_message = FakeGroupMessage(text="Too early", message_id=49)
        boundary_message = FakeGroupMessage(text="Boundary", message_id=50)
        new_message = FakeGroupMessage(text="Count me", message_id=51)
        await self.contest_bot.on_message(old_message)
        await self.contest_bot.on_message(boundary_message)
        untouched = await self.contest_bot.manager.snapshot(game_key(-1001))
        self.assertIsNone(untouched.leader)

        await self.contest_bot.on_message(new_message)
        active = await self.contest_bot.manager.snapshot(game_key(-1001))
        self.assertEqual(active.leader.user_id, 100)

    async def test_completed_intercept_form_starts_game(self) -> None:
        self._prepare_targets()
        await self.state.set_state(InterceptSetup.screenshot)
        await self.state.set_data(
            {"prize": "NFT prize", "duration": 120, "stars": 10}
        )

        message = FakeMessage()
        await self.contest_bot._finish_intercept_setup(message, self.state)

        active = await self.contest_bot.manager.snapshot(game_key(-1001))
        self.assertIsNotNone(active)
        self.assertEqual(active.intercept_seconds, 120)
        self.assertEqual(active.message_stars, 10)
        self.assertEqual(active.tracking_after_message_id, 1)
        self.assertIsNone(await self.state.get_state())
        self.assertEqual(self.bot.send_message.await_count, 1)

    async def test_guess_form_and_display_price_only(self) -> None:
        self._prepare_targets()
        await self.state.set_state(GuessSetup.stars)
        await self.state.set_data(
            {"prize": "NFT prize", "secret_number": 42, "stars": 0}
        )
        setup_message = FakeMessage()
        await self.contest_bot._finish_guess_setup(setup_message, self.state)

        active = await self.contest_bot.manager.snapshot(game_key(-1001))
        self.assertEqual(active.secret_number, 42)
        await self.contest_bot.manager.stop(game_key(-1001))
        await self.contest_bot.manager.start_guess(
            game_key(-1001), 42, prize="NFT prize", message_stars=10
        )
        self.contest_bot._announce_winner = AsyncMock()

        ordinary = FakeGroupMessage(text="42", message_id=20)
        await self.contest_bot.on_message(ordinary)

        self.assertTrue(ordinary.replies)
        self.contest_bot._announce_winner.assert_awaited_once()
        self.assertIsNone(await self.contest_bot.manager.snapshot(game_key(-1001)))

    async def test_race_counts_an_ordinary_user_only_once(self) -> None:
        await self.contest_bot.manager.start_race(
            game_key(-1001), 60, message_stars=5
        )
        ordinary = FakeGroupMessage(text="go", message_id=31)
        duplicate = FakeGroupMessage(text="again", message_id=32)
        await self.contest_bot.on_message(ordinary)
        await self.contest_bot.on_message(duplicate)

        active = await self.contest_bot.manager.snapshot(game_key(-1001))
        self.assertEqual(len(active.participants), 1)
        self.assertEqual(len(ordinary.replies), 1)
        self.assertEqual(duplicate.replies, [])

    async def test_case_is_saved_and_started(self) -> None:
        self._prepare_targets()
        saved_drop = self.storage.save_drop(42, "🧸", 0.05)
        await self.state.set_state(CaseSetup.screenshot)
        await self.state.set_data(
            {
                "case_name": "Lucky",
                "stars": 0,
                "duration": 60,
                "drop_ids": [saved_drop.drop_id],
            }
        )

        message = FakeMessage()
        await self.contest_bot._finish_case_setup(message, self.state)

        active = await self.contest_bot.manager.snapshot(game_key(-1001))
        self.assertEqual(active.kind.value, "case")
        self.assertEqual(active.case_name, "Lucky")
        self.assertEqual(len(self.storage.list_cases(42)), 1)

    async def test_activity_post_discloses_real_chances(self) -> None:
        text = self.contest_bot._activity_start_text()
        self.assertIn("0.05%", text)
        self.assertIn("0.04%", text)
        self.assertIn("0.03%", text)
        self.assertNotIn("0.1%", text)


if __name__ == "__main__":
    unittest.main()
