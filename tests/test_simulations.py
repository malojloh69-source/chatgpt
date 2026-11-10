import random
import unittest
from collections import Counter
from itertools import pairwise

from app import simulations
from app.emojis import CATALOG, STICKER_ALTS, gallery, premium


class SimulationTests(unittest.TestCase):
    def test_travel_winner_comes_from_first_finishing_tick(self):
        for flight in (False, True):
            for seed in range(100):
                result = simulations.travel(30, random.Random(seed), flight=flight)
                self.assertTrue(result.reached_finish)
                self.assertEqual(
                    result.frames[-1].positions[result.winner], result.distance
                )
                self.assertTrue(
                    all(max(f.positions) < result.distance for f in result.frames[:-1])
                )
                for before, after in pairwise(result.frames):
                    for index, position in enumerate(after.positions):
                        if index in after.hazards:
                            self.assertEqual(
                                position, max(0, before.positions[index] - 1)
                            )
                        else:
                            self.assertIn(position - before.positions[index], (1, 2, 3))

    def test_snake_winner_is_last_captured_apple_on_real_route(self):
        for seed in range(100):
            frames, winner = simulations.snake(8, random.Random(seed))
            captured = [f.captured for f in frames if f.captured is not None]
            self.assertEqual(sorted(captured), list(range(8)))
            self.assertEqual(captured[-1], winner)
            self.assertEqual(frames[-1].apples, ())
            for before, after in pairwise(frames):
                self.assertEqual(
                    abs(before.head[0] - after.head[0])
                    + abs(before.head[1] - after.head[1]),
                    1,
                )

    def test_mining_counts_equal_visible_digs_and_tie_break_is_explicit(self):
        saw_tie = False
        for seed in range(100):
            frames, winner = simulations.mine(5, random.Random(seed))
            self.assertEqual(sum(frames[12].counts), 12)
            for before, after in pairwise(frames):
                self.assertEqual(sum(after.counts) - sum(before.counts), 1)
                self.assertEqual(
                    after.counts[after.miner] - before.counts[after.miner], 1
                )
            self.assertGreater(
                frames[-1].counts[winner],
                max(v for i, v in enumerate(frames[-1].counts) if i != winner),
            )
            if frames[-1].tiebreak:
                saw_tie = True
                top = max(frames[-2].counts)
                self.assertEqual(frames[-2].counts[winner], top)
        self.assertTrue(saw_tie)

    def test_football_equal_possessions_and_event_score_consistency(self):
        saw_penalties = False
        for seed in range(150):
            match = simulations.football(random.Random(seed))
            self.assertEqual(len(match.events), 20)
            self.assertEqual(
                Counter(e.player for e in match.events),
                Counter({i: 2 for i in range(10)}),
            )
            score = [0, 0]
            for event in match.events:
                score[event.side] += int(event.goal)
                self.assertEqual(event.score, tuple(score))
                self.assertEqual(event.side, int(event.player >= 5))
            self.assertEqual(match.score, tuple(score))
            self.assertEqual(match.events[-1].attacks, (10, 10))
            self.assertEqual(
                set(match.ranking), set(range(match.winner * 5, match.winner * 5 + 5))
            )
            self.assertEqual(
                [match.points[i] for i in match.ranking],
                sorted((match.points[i] for i in match.ranking), reverse=True),
            )
            if match.penalties:
                saw_penalties = True
                self.assertEqual(score[0], score[1])
                self.assertEqual(len(match.penalties) % 2, 0)
                self.assertGreaterEqual(len(match.penalties), 10)
                final = match.penalties[-1].score
                self.assertGreater(final[match.winner], final[1 - match.winner])
            else:
                self.assertGreater(score[match.winner], score[1 - match.winner])
        self.assertTrue(saw_penalties)

    def test_catalog_preserves_all_56_positions_and_duplicates(self):
        self.assertEqual(len(CATALOG), 56)
        self.assertEqual(CATALOG[0].emoji_id, "5357427366194849097")
        self.assertEqual(CATALOG[3].emoji_id, CATALOG[5].emoji_id)
        self.assertEqual(CATALOG[29].emoji_id, CATALOG[31].emoji_id)
        self.assertEqual(CATALOG[-1].emoji_id, "5397890704062038337")
        self.assertEqual(gallery().count("<tg-emoji"), 56)

    def test_telegram_alt_is_used_for_custom_emoji(self):
        STICKER_ALTS["test"] = "🎯"
        try:
            self.assertIn(">🎯</tg-emoji>", premium("test", "🙂"))
        finally:
            STICKER_ALTS.pop("test", None)


if __name__ == "__main__":
    unittest.main()
