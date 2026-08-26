from __future__ import annotations

import asyncio
import unittest

from app.engine import (
    ContestManager,
    ContestType,
    Participant,
    SpinStatus,
)


class ContestManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.winners: list[tuple[tuple[int, int | None], Participant]] = []

        async def collect_winner(key, participant, state):
            self.winners.append((key, participant))

        self.manager = ContestManager(collect_winner)
        self.key = (-100123, None)
        self.alice = Participant(1, "Alice", "alice")
        self.bob = Participant(2, "Bob", "bob")

    async def asyncTearDown(self) -> None:
        await self.manager.close()

    async def test_intercept_resets_for_another_user(self) -> None:
        await self.manager.start_intercept(self.key, 0.08)
        first = await self.manager.submit_intercept(self.key, self.alice)
        self.assertTrue(first and first.accepted and first.first_leader)

        await asyncio.sleep(0.04)
        takeover = await self.manager.submit_intercept(self.key, self.bob)
        self.assertTrue(takeover and takeover.accepted)
        await asyncio.sleep(0.05)
        self.assertEqual(self.winners, [])

        await asyncio.sleep(0.05)
        self.assertEqual(self.winners, [(self.key, self.bob)])

    async def test_leaders_own_message_does_not_extend_timer(self) -> None:
        await self.manager.start_intercept(self.key, 0.06)
        await self.manager.submit_intercept(self.key, self.alice)
        await asyncio.sleep(0.035)
        update = await self.manager.submit_intercept(self.key, self.alice)
        self.assertIsNotNone(update)
        self.assertFalse(update.accepted)
        await asyncio.sleep(0.04)
        self.assertEqual(self.winners, [(self.key, self.alice)])

    async def test_guess_has_only_one_winner(self) -> None:
        await self.manager.start_guess(self.key, 42)
        self.assertIsNone(await self.manager.submit_guess(self.key, self.alice, 41))
        result = await self.manager.submit_guess(self.key, self.bob, 42)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.bob)
        self.assertIsNone(await self.manager.submit_guess(self.key, self.alice, 42))
        self.assertIsNone(await self.manager.snapshot(self.key))

    async def test_casino_requires_value_64_and_applies_cooldown(self) -> None:
        await self.manager.start_casino(self.key)
        reservation = await self.manager.reserve_spin(self.key, self.alice.user_id, 10)
        self.assertEqual(reservation.status, SpinStatus.ACCEPTED)
        miss = await self.manager.resolve_spin(
            self.key, reservation.game_id or 0, self.alice, 63
        )
        self.assertIsNotNone(miss)
        self.assertFalse(miss.jackpot)

        cooldown = await self.manager.reserve_spin(self.key, self.alice.user_id, 10)
        self.assertEqual(cooldown.status, SpinStatus.COOLDOWN)

        bob_spin = await self.manager.reserve_spin(self.key, self.bob.user_id, 10)
        win = await self.manager.resolve_spin(
            self.key, bob_spin.game_id or 0, self.bob, 64
        )
        self.assertIsNotNone(win)
        self.assertEqual(win.winner, self.bob)

    async def test_casino_counts_required_777_per_player(self) -> None:
        await self.manager.start_casino(self.key, jackpot_target=2)
        first = await self.manager.reserve_spin(self.key, self.alice.user_id, 0)
        progress = await self.manager.resolve_spin(
            self.key, first.game_id or 0, self.alice, 64
        )
        self.assertIsNotNone(progress)
        self.assertEqual((progress.hits, progress.target), (1, 2))
        self.assertIsNone(progress.winner)

        bob = await self.manager.reserve_spin(self.key, self.bob.user_id, 0)
        bob_progress = await self.manager.resolve_spin(
            self.key, bob.game_id or 0, self.bob, 64
        )
        self.assertIsNotNone(bob_progress)
        self.assertEqual(bob_progress.hits, 1)

        second = await self.manager.reserve_spin(self.key, self.alice.user_id, 0)
        winner = await self.manager.resolve_spin(
            self.key, second.game_id or 0, self.alice, 64
        )
        self.assertIsNotNone(winner)
        self.assertEqual(winner.winner, self.alice)
        self.assertIsNone(await self.manager.snapshot(self.key))

    async def test_new_contest_replaces_old_one_and_cancels_timer(self) -> None:
        await self.manager.start_intercept(self.key, 0.03)
        await self.manager.submit_intercept(self.key, self.alice)
        await self.manager.start_casino(self.key)
        await asyncio.sleep(0.05)
        self.assertEqual(self.winners, [])
        state = await self.manager.snapshot(self.key)
        self.assertIsNotNone(state)
        self.assertEqual(state.kind, ContestType.CASINO)


if __name__ == "__main__":
    unittest.main()
