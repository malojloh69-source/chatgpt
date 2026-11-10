import asyncio
import unittest
from unittest.mock import AsyncMock

from app.engine import ContestManager, ContestType, DropOutcome, Participant


class EngineIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = [0.0]
        self.gate = asyncio.Event()
        self.manager = ContestManager(
            AsyncMock(), clock=lambda: self.now[0], sleep=lambda _: self.gate.wait()
        )
        self.key = (-1001, None)
        self.alice = Participant(1, "Alice")
        self.bob = Participant(2, "Bob")

    async def asyncTearDown(self):
        await self.manager.close()

    async def test_no_game_accepts_attempts_after_deadline_even_if_timer_is_delayed(
        self,
    ):
        await self.manager.start_intercept(self.key, 1)
        await self.manager.submit_intercept(self.key, self.alice, 1)
        self.now[0] = 1
        self.assertIsNone(await self.manager.submit_intercept(self.key, self.bob, 2))
        for kind in (
            ContestType.RACE,
            ContestType.CASE,
            ContestType.PARKOUR,
            ContestType.SNAKE,
        ):
            self.now[0] = 0
            if kind is ContestType.RACE:
                await self.manager.start_race(self.key, 1)
            elif kind is ContestType.CASE:
                await self.manager.start_case(
                    self.key, "Box", (DropOutcome("Gift", 100),), 1
                )
            else:
                await self.manager.start_arcade(self.key, kind, 1)
            self.now[0] = 1
            action = {
                ContestType.RACE: lambda: self.manager.submit_race(
                    self.key, self.alice, 3
                ),
                ContestType.CASE: lambda: self.manager.open_case(self.key, 3),
                ContestType.PARKOUR: lambda: self.manager.submit_parkour(
                    self.key, self.alice, 3
                ),
                ContestType.SNAKE: lambda: self.manager.submit_arcade_join(
                    self.key, self.alice, 3
                ),
            }[kind]
            self.assertIsNone(await action())

    async def test_jackpot_receipt_cannot_be_counted_twice(self):
        state = await self.manager.start_casino(
            self.key, jackpot_target=2, tracking_after_message_id=9
        )
        self.assertIsNone(
            await self.manager.resolve_spin(self.key, state.game_id, self.alice, 64, 9)
        )
        first = await self.manager.resolve_spin(
            self.key, state.game_id, self.alice, 64, 10
        )
        self.assertEqual(first.hits, 1)
        self.assertIsNone(
            await self.manager.resolve_spin(self.key, state.game_id, self.alice, 64, 10)
        )
        final = await self.manager.resolve_spin(
            self.key, state.game_id, self.alice, 64, 11
        )
        self.assertEqual(final.winner, self.alice)
        self.assertEqual(final.finished_state.attempts[1], 2)

    async def test_stale_action_does_not_enter_replacement_game(self):
        old = await self.manager.start_race(self.key, 60)
        current = await self.manager.start_race(self.key, 60)
        self.assertIsNone(
            await self.manager.submit_race(
                self.key, self.alice, 10, game_id=old.game_id
            )
        )
        result = await self.manager.submit_race(
            self.key, self.alice, 10, game_id=current.game_id
        )
        self.assertTrue(result.accepted)

    async def test_full_football_lineup_can_close_collection_early_once(self):
        state = await self.manager.start_football(self.key, "Blue", "Red", 60)
        for index in range(10):
            player = Participant(index + 1, f"Player {index}")
            await self.manager.submit_football_team(
                self.key, player, "a" if index < 5 else "b"
            )
            update = await self.manager.submit_football_player(self.key, player, index)
            self.assertEqual(update.collection_ready, index == 9)
        self.assertIsNotNone(
            await self.manager.finish_collection_now(self.key, game_id=state.game_id)
        )
        self.assertIsNone(await self.manager.finish_collection_now(self.key))

    async def test_football_receipt_preserves_actual_payment_for_refund(self):
        await self.manager.start_football(self.key, "Blue", "Red", 60, message_stars=20)
        unpaid = await self.manager.submit_football_team(
            self.key, self.alice, "a", 10, paid_stars=19
        )
        self.assertIsNone(unpaid)
        await self.manager.submit_football_team(
            self.key, self.alice, "a", 11, paid_stars=25
        )
        # Another paid message from the same player cannot overwrite the entry receipt.
        await self.manager.submit_football_team(
            self.key, self.alice, "a", 12, paid_stars=30
        )
        state = await self.manager.snapshot(self.key)
        self.assertEqual(state.football_paid_stars, {1: 25})

    async def test_close_cancels_finish_handler_after_timer_has_expired(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def finish(key, state):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        manager = ContestManager(AsyncMock(), timed_finish_handler=finish)
        await manager.start_race(self.key, 0.001)
        await asyncio.wait_for(started.wait(), 1)
        await manager.close()
        self.assertTrue(cancelled.is_set())


if __name__ == "__main__":
    unittest.main()
