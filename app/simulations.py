"""Pure game simulations. The same events determine the animation and result."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class RandomSource(Protocol):
    def random(self) -> float: ...
    def randint(self, a: int, b: int) -> int: ...
    def choice(self, seq): ...
    def shuffle(self, seq) -> None: ...


@dataclass(frozen=True)
class TravelFrame:
    positions: tuple[int, ...]
    hazards: frozenset[int] = frozenset()


@dataclass(frozen=True)
class TravelResult:
    frames: tuple[TravelFrame, ...]
    winner: int
    distance: int
    reached_finish: bool


def travel(count: int, rng: RandomSource, *, flight: bool = False) -> TravelResult:
    if count < 1:
        raise ValueError("participants required")
    distance = 12 if flight else 10
    positions = [0] * count
    frames = [TravelFrame(tuple(positions))]
    finishers: list[int] = []
    for _ in range(30 if flight else 10):
        hazards = set()
        for index in range(count):
            if flight and rng.random() < 0.20:
                hazards.add(index)
                positions[index] = max(0, positions[index] - 1)
            else:
                positions[index] += rng.randint(1, 3)
        finishers = [i for i, position in enumerate(positions) if position >= distance]
        frames.append(
            TravelFrame(tuple(min(distance, p) for p in positions), frozenset(hazards))
        )
        if finishers:
            break
    # Simultaneous finishes share a fair tie-break; no track is moved to fake a win.
    if not finishers:
        best = max(positions)
        finishers = [i for i, position in enumerate(positions) if position == best]
    return TravelResult(
        tuple(frames), rng.choice(finishers), distance, max(positions) >= distance
    )


@dataclass(frozen=True)
class SnakeFrame:
    head: tuple[int, int]
    body: tuple[tuple[int, int], ...]
    apples: tuple[tuple[int, tuple[int, int]], ...]
    captured: int | None = None


def snake(count: int, rng: RandomSource) -> tuple[tuple[SnakeFrame, ...], int]:
    if not 1 <= count <= 8:
        raise ValueError("snake requires 1..8 apples")
    cells = [(r, c) for r in range(1, 6) for c in range(1, 6) if (r, c) != (3, 3)]
    rng.shuffle(cells)
    apples = {i: cells[i] for i in range(count)}
    head = (3, 3)
    trail = [head]
    frames = [SnakeFrame(head, (), tuple(apples.items()))]
    last = 0
    while apples:
        distance = lambda pos, origin=head: (
            abs(pos[0] - origin[0]) + abs(pos[1] - origin[1])
        )
        nearest = min(map(distance, apples.values()))
        target = rng.choice(
            [i for i, pos in apples.items() if distance(pos) == nearest]
        )
        destination = apples[target]
        while head != destination:
            row, column = head
            # A shared snake follows a route; body is a visual trail, not an obstacle.
            head = (
                (row + (1 if destination[0] > row else -1), column)
                if row != destination[0]
                else (row, column + (1 if destination[1] > column else -1))
            )
            captured = None
            if head == destination:
                captured = last = target
                apples.pop(target)
            trail.append(head)
            frames.append(
                SnakeFrame(head, tuple(trail[-5:-1]), tuple(apples.items()), captured)
            )
    return tuple(frames), last


@dataclass(frozen=True)
class MiningFrame:
    counts: tuple[int, ...]
    miner: int | None = None
    tiebreak: bool = False


def mine(count: int, rng: RandomSource) -> tuple[tuple[MiningFrame, ...], int]:
    if count < 1:
        raise ValueError("miners required")
    counts = [0] * count
    frames = [MiningFrame(tuple(counts))]
    for _ in range(12):
        miner = rng.choice(range(count))
        counts[miner] += 1
        frames.append(MiningFrame(tuple(counts), miner))
    leaders = [i for i, score in enumerate(counts) if score == max(counts)]
    winner = rng.choice(leaders)
    if len(leaders) > 1:
        # An explicitly displayed extra dig, separate from the twelve normal layers.
        counts[winner] += 1
        frames.append(MiningFrame(tuple(counts), winner, True))
    return tuple(frames), winner


@dataclass(frozen=True)
class FootballEvent:
    minute: int
    side: int
    player: int
    action: str
    defense: str
    commentary: str
    goal: bool
    score: tuple[int, int]
    attacks: tuple[int, int]


@dataclass(frozen=True)
class PenaltyEvent:
    round: int
    side: int
    goal: bool
    score: tuple[int, int]


@dataclass(frozen=True)
class FootballResult:
    events: tuple[FootballEvent, ...]
    score: tuple[int, int]
    points: tuple[int, ...]
    penalties: tuple[PenaltyEvent, ...]
    winner: int | None
    ranking: tuple[int, ...]


def football(rng: RandomSource) -> FootballResult:
    score, attacks, bonus = [0, 0], [0, 0], [0.0, 0.0]
    points = [0] * 10
    assist: list[int | None] = [None, None]
    order = []
    # Each footballer gets two possessions; team A never gets an extra attack.
    for _ in range(2):
        blue, red = list(range(5)), list(range(5, 10))
        rng.shuffle(blue)
        rng.shuffle(red)
        order.extend(player for pair in zip(blue, red) for player in pair)
    events = []
    for turn, player in enumerate(order):
        side = 0 if player < 5 else 1
        attacks[side] += 1
        action = rng.choice(("strong", "accurate", "pass"))
        defense = rng.choice(("keeper", "wall", "intercept"))
        goal = False
        if action == "pass":
            if defense == "intercept" and rng.random() < 0.55:
                bonus[side], assist[side] = 0.0, None
                commentary = "Перехват! Передача не дошла до партнёра."
            else:
                bonus[side] = min(0.30, bonus[side] + 0.15)
                assist[side] = player
                commentary = "Точный пас! Следующий удар получит бонус."
        else:
            base = {
                ("strong", "keeper"): 0.34,
                ("strong", "wall"): 0.20,
                ("strong", "intercept"): 0.42,
                ("accurate", "keeper"): 0.30,
                ("accurate", "wall"): 0.48,
                ("accurate", "intercept"): 0.34,
            }
            goal = rng.random() < min(0.85, base[action, defense] + bonus[side])
            if goal:
                score[side] += 1
                points[player] += 3
                if assist[side] is not None and assist[side] != player:
                    points[assist[side]] += 2
                commentary = (
                    "Мощный удар — ГОЛ!"
                    if action == "strong"
                    else "Точно в угол — ГОЛ!"
                )
            else:
                defender = rng.choice(range((1 - side) * 5, (1 - side) * 5 + 5))
                points[defender] += 1
                commentary = {
                    "keeper": "Сейв! Вратарь спасает команду.",
                    "wall": "Стенка блокирует удар.",
                    "intercept": "Защитник перехватывает мяч.",
                }[defense]
            bonus[side], assist[side] = 0.0, None
        events.append(
            FootballEvent(
                round((turn + 1) * 4.5),
                side,
                player,
                action,
                defense,
                commentary,
                goal,
                tuple(score),
                tuple(attacks),
            )
        )
    penalties = []
    winner = 0 if score[0] > score[1] else 1 if score[1] > score[0] else None
    if winner is None:
        penalty_score = [0, 0]
        # Five paired kicks, then sudden death. Bounded for a broken RNG.
        for round_number in range(1, 31):
            for side in (0, 1):
                goal = rng.random() < 0.72
                penalty_score[side] += int(goal)
                penalties.append(
                    PenaltyEvent(round_number, side, goal, tuple(penalty_score))
                )
            if round_number >= 5 and penalty_score[0] != penalty_score[1]:
                winner = 0 if penalty_score[0] > penalty_score[1] else 1
                break
    ranking = list(range((winner or 0) * 5, (winner or 0) * 5 + 5))
    rng.shuffle(ranking)  # Equal ratings have no fixed player-index advantage.
    ranking.sort(key=lambda index: -points[index])
    return FootballResult(
        tuple(events),
        tuple(score),
        tuple(points),
        tuple(penalties),
        winner,
        tuple(ranking),
    )
