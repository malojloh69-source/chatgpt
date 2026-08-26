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
    AIRPLANE_IDS,
    ArcadeSetup,
    CaseSetup,
    CasinoSetup,
    ContestBot,
    FootballSetup,
    FOOTBALLERS,
    GuessSetup,
    InterceptSetup,
    football_pick_keyboard,
    game_key,
)
from app.config import Settings
from app.engine import ContestState, ContestType, Participant
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

    async def test_airplane_setup_uses_standard_form_and_starts_game(self) -> None:
        self._prepare_targets()
        await self.state.set_state(ArcadeSetup.screenshot)
        await self.state.set_data(
            {
                "arcade_kind": ContestType.AIRPLANE.value,
                "prize": "NFT prize",
                "stars": 10,
                "duration": 60,
            }
        )

        message = FakeMessage()
        await self.contest_bot._finish_arcade_setup(message, self.state)

        active = await self.contest_bot.manager.snapshot(game_key(-1001))
        self.assertIsNotNone(active)
        self.assertEqual(active.kind, ContestType.AIRPLANE)
        self.assertEqual(active.message_stars, 10)
        self.assertEqual(active.tracking_after_message_id, 1)

    async def test_football_setup_starts_automatic_prediction_round(self) -> None:
        self._prepare_targets()
        await self.state.set_state(FootballSetup.screenshot)
        await self.state.set_data(
            {
                "team_a": "Blue Stars",
                "team_b": "Red Stars",
                "stars": 20,
                "duration": 60,
            }
        )

        message = FakeMessage()
        await self.contest_bot._finish_football_setup(message, self.state)

        active = await self.contest_bot.manager.snapshot(game_key(-1001))
        self.assertIsNotNone(active)
        self.assertEqual(active.kind, ContestType.FOOTBALL)
        self.assertEqual(active.team_a_name, "Blue Stars")
        self.assertEqual(active.team_b_name, "Red Stars")
        self.assertEqual(active.message_stars, 20)
        sent_markup = self.bot.send_message.await_args.kwargs["reply_markup"]
        callbacks = [
            button.callback_data
            for row in sent_markup.inline_keyboard
            for button in row
        ]
        self.assertIn("football:pick:draw", callbacks)
        self.assertEqual(
            len([value for value in callbacks if value.startswith("football:player:")]),
            10,
        )

    def test_football_keyboard_has_both_teams_draw_and_ten_players(self) -> None:
        keyboard = football_pick_keyboard("Blue", "Red")
        callbacks = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertEqual(
            callbacks[:3],
            ["football:pick:a", "football:pick:draw", "football:pick:b"],
        )
        self.assertEqual(len(callbacks), 13)

    def test_airplane_animation_uses_premium_plane_and_twenty_percent_birds(self) -> None:
        alice = Participant(1, "Alice", "alice")
        bob = Participant(2, "Bob", "bob")
        self.contest_bot._random = SimpleNamespace(
            random=lambda: 0.10,
            randint=lambda start, end: 1,
            choice=lambda values: values[0],
        )

        frames = self.contest_bot._airplane_frames([alice, bob], alice)

        self.assertEqual(len(frames), 11)
        self.assertIn(AIRPLANE_IDS[0], frames[-1])
        self.assertIn(AIRPLANE_IDS[1], frames[1])
        self.assertIn("Первый самолёт", frames[-1])

    def test_football_post_contains_all_premium_players(self) -> None:
        text = self.contest_bot._football_start_text("Blue", "Red", 20, 60)
        for _, emoji_id, _ in FOOTBALLERS:
            self.assertIn(emoji_id, text)
        self.assertIn("1,5×", text)
        self.assertIn("10 атак", text)

    async def test_football_win_announces_one_and_a_half_times_payout(self) -> None:
        participant = Participant(100, "Player", "player")
        state = ContestState(
            game_id=1,
            kind=ContestType.FOOTBALL,
            started_at=0,
            message_stars=20,
            team_a_name="Blue",
            team_b_name="Red",
            participants={participant.user_id: participant},
            football_picks={participant.user_id: "a"},
            football_players={participant.user_id: 0},
        )
        self.contest_bot._safe_public = AsyncMock()

        await self.contest_bot._announce_football_result(
            game_key(-1001), state, [2, 1]
        )

        text = self.contest_bot._safe_public.await_args.args[1]
        self.assertIn("30 Stars", text)
        self.assertIn("@player", text)
        self.assertIn(FOOTBALLERS[0][1], text)

    async def test_football_draw_burns_every_prediction(self) -> None:
        state = ContestState(
            game_id=1,
            kind=ContestType.FOOTBALL,
            started_at=0,
            message_stars=21,
            team_a_name="Blue",
            team_b_name="Red",
        )
        self.contest_bot._safe_public = AsyncMock()

        await self.contest_bot._announce_football_result(
            game_key(-1001), state, [3, 3]
        )

        text = self.contest_bot._safe_public.await_args.args[1]
        self.assertIn("сгорают", text)
        self.assertNotIn("31,5 Stars", text)


if __name__ == "__main__":
    unittest.main()
