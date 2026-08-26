from __future__ import annotations

import asyncio
import unittest

from app.engine import (
    ContestManager,
    ContestType,
    DropOutcome,
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

    async def test_race_registers_each_user_once(self) -> None:
        await self.manager.start_race(self.key, 60, message_stars=5)
        first = await self.manager.submit_race(self.key, self.alice)
        duplicate = await self.manager.submit_race(self.key, self.alice)
        second = await self.manager.submit_race(self.key, self.bob)

        self.assertTrue(first.accepted)
        self.assertFalse(duplicate.accepted)
        self.assertTrue(second.accepted)
        self.assertEqual(second.participant_count, 2)
        state = await self.manager.snapshot(self.key)
        self.assertEqual(state.message_stars, 5)
        self.assertEqual(set(state.participants), {1, 2})

    async def test_case_has_one_opening_per_message_and_uses_exact_odds(self) -> None:
        async def ignore_winner(key, participant, state):
            return None

        manager = ContestManager(ignore_winner, random_value=lambda: 0.0004)
        try:
            await manager.start_case(
                self.key,
                "Lucky",
                (DropOutcome("🧸", 0.05), DropOutcome("💎", 0.03)),
                60,
            )
            opened = await manager.open_case(self.key, 100)
            duplicate = await manager.open_case(self.key, 100)
            self.assertIsNotNone(opened)
            self.assertEqual(opened.outcome.name, "🧸")
            self.assertIsNone(duplicate)
        finally:
            await manager.close()

    async def test_race_timer_returns_all_participants(self) -> None:
        finishes = []

        async def ignore_winner(key, participant, state):
            return None

        async def collect_finish(key, state):
            finishes.append((key, state))

        manager = ContestManager(
            ignore_winner,
            timed_finish_handler=collect_finish,
        )
        try:
            await manager.start_race(self.key, 0.03, prize="Prize")
            await manager.submit_race(self.key, self.alice)
            await asyncio.sleep(0.06)
            self.assertEqual(len(finishes), 1)
            self.assertEqual(finishes[0][1].participants[1], self.alice)
            self.assertIsNone(await manager.snapshot(self.key))
        finally:
            await manager.close()

    async def test_snake_accepts_at_most_eight_and_can_start_immediately(self) -> None:
        await self.manager.start_arcade(
            self.key, ContestType.SNAKE, 60, prize="Prize"
        )
        last_update = None
        for user_id in range(1, 9):
            last_update = await self.manager.submit_arcade_join(
                self.key, Participant(user_id, f"Player {user_id}")
            )
        self.assertIsNotNone(last_update)
        self.assertTrue(last_update.collection_ready)
        self.assertEqual(last_update.participant_count, 8)

        finished = await self.manager.finish_collection_now(self.key)
        self.assertIsNotNone(finished)
        self.assertEqual(len(finished.participants), 8)
        self.assertIsNone(await self.manager.snapshot(self.key))

    async def test_pickaxe_becomes_ready_on_fifth_unique_participant(self) -> None:
        await self.manager.start_arcade(self.key, ContestType.PICKAXE, 60)
        updates = []
        for user_id in range(1, 6):
            updates.append(
                await self.manager.submit_arcade_join(
                    self.key, Participant(user_id, f"Player {user_id}")
                )
            )
        self.assertFalse(updates[3].collection_ready)
        self.assertTrue(updates[4].collection_ready)

    async def test_parkour_uses_ten_obstacles_and_exact_collision_threshold(self) -> None:
        values = iter([0.0, 0.249] + [0.0] * 9)

        async def ignore_winner(key, participant, state):
            return None

        manager = ContestManager(ignore_winner, random_value=lambda: next(values))
        try:
            await manager.start_arcade(self.key, ContestType.PARKOUR, 60)
            result = await manager.submit_parkour(self.key, self.alice, 101)
            self.assertIsNotNone(result)
            self.assertEqual(len(result.obstacle_kinds), 10)
            self.assertEqual(result.collision_index, 0)
            self.assertIsNone(result.winner)
            self.assertIsNotNone(await manager.snapshot(self.key))
        finally:
            await manager.close()

    async def test_parkour_first_full_run_wins_and_closes_game(self) -> None:
        values = iter([value for _ in range(10) for value in (0.0, 0.25)])

        async def ignore_winner(key, participant, state):
            return None

        manager = ContestManager(ignore_winner, random_value=lambda: next(values))
        try:
            await manager.start_arcade(self.key, ContestType.PARKOUR, 60)
            result = await manager.submit_parkour(self.key, self.alice, 102)
            self.assertIsNotNone(result)
            self.assertIsNone(result.collision_index)
            self.assertEqual(result.winner, self.alice)
            self.assertIsNotNone(result.finished_state)
            self.assertIsNone(await manager.snapshot(self.key))
        finally:
            await manager.close()

    async def test_football_prediction_can_be_changed_before_deadline(self) -> None:
        await self.manager.start_football(
            self.key,
            "Blue",
            "Red",
            60,
            message_stars=10,
            tracking_after_message_id=500,
        )
        first = await self.manager.submit_football_pick(self.key, self.alice, "a")
        changed = await self.manager.submit_football_pick(
            self.key, self.alice, "b"
        )
        player_selected = await self.manager.submit_football_player(
            self.key, self.alice, 7
        )
        self.assertIsNotNone(first)
        self.assertFalse(first.changed)
        self.assertIsNotNone(changed)
        self.assertTrue(changed.changed)
        self.assertTrue(player_selected)
        self.assertEqual(changed.counts, {"a": 0, "draw": 0, "b": 1})
        state = await self.manager.snapshot(self.key)
        self.assertEqual(state.football_picks[self.alice.user_id], "b")
        self.assertEqual(state.football_players[self.alice.user_id], 7)


if __name__ == "__main__":
    unittest.main()
