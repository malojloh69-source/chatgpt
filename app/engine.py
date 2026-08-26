from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from enum import Enum, StrEnum

logger = logging.getLogger(__name__)

ContestKey = tuple[int, int | None]


class ContestType(StrEnum):
    INTERCEPT = "intercept"
    CASINO = "casino"
    GUESS = "guess"
    RACE = "race"
    CASE = "case"
    AIRPLANE = "airplane"
    PARKOUR = "parkour"
    SNAKE = "snake"
    PICKAXE = "pickaxe"
    FOOTBALL = "football"


@dataclass(frozen=True, slots=True)
class Participant:
    user_id: int
    full_name: str
    username: str | None = None


@dataclass(frozen=True, slots=True)
class DropOutcome:
    name: str
    chance: float


@dataclass(slots=True)
class ContestState:
    game_id: int
    kind: ContestType
    started_at: float
    prize: str = ""
    tracking_after_message_id: int | None = None
    intercept_seconds: float | None = None
    message_stars: int = 0
    leader: Participant | None = None
    deadline: float | None = None
    secret_number: int | None = None
    jackpot_target: int = 1
    jackpot_hits: dict[int, int] = field(default_factory=dict)
    spin_available_at: dict[int, float] = field(default_factory=dict)
    participants: dict[int, Participant] = field(default_factory=dict)
    case_name: str = ""
    case_drops: tuple[DropOutcome, ...] = ()
    team_a_name: str = ""
    team_b_name: str = ""
    football_picks: dict[int, str] = field(default_factory=dict)
    football_players: dict[int, int] = field(default_factory=dict)
    processed_message_ids: set[int] = field(default_factory=set)
    generation: int = 0


TimedWinnerHandler = Callable[
    [ContestKey, Participant, ContestState], Awaitable[None]
]
TimedFinishHandler = Callable[[ContestKey, ContestState], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class InterceptUpdate:
    accepted: bool
    first_leader: bool
    remaining_seconds: float


class SpinStatus(Enum):
    ACCEPTED = "accepted"
    NOT_ACTIVE = "not_active"
    COOLDOWN = "cooldown"


@dataclass(frozen=True, slots=True)
class SpinReservation:
    status: SpinStatus
    game_id: int | None = None
    retry_after: float = 0.0


@dataclass(frozen=True, slots=True)
class CasinoSpinUpdate:
    jackpot: bool
    hits: int
    target: int
    winner: Participant | None = None
    finished_state: ContestState | None = None


@dataclass(frozen=True, slots=True)
class RaceJoinUpdate:
    accepted: bool
    participant_count: int
    collection_ready: bool = False


@dataclass(frozen=True, slots=True)
class CaseOpenUpdate:
    outcome: DropOutcome | None
    state: ContestState


@dataclass(frozen=True, slots=True)
class ParkourAttemptUpdate:
    obstacle_kinds: tuple[str, ...]
    collision_index: int | None
    winner: Participant | None = None
    finished_state: ContestState | None = None


@dataclass(frozen=True, slots=True)
class FootballPickUpdate:
    accepted: bool
    choice: str
    changed: bool
    counts: dict[str, int]


class ContestManager:
    """Keeps one active contest per selected group."""

    def __init__(
        self,
        timed_winner_handler: TimedWinnerHandler,
        *,
        timed_finish_handler: TimedFinishHandler | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_value: Callable[[], float] | None = None,
    ) -> None:
        self._states: dict[ContestKey, ContestState] = {}
        self._timers: dict[ContestKey, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._clock = clock
        self._sleep = sleep
        self._timed_winner_handler = timed_winner_handler
        self._timed_finish_handler = timed_finish_handler
        self._random_value = random_value or secrets.SystemRandom().random
        self._next_game_id = 1

    def _new_state(
        self,
        kind: ContestType,
        *,
        prize: str = "",
        tracking_after_message_id: int | None = None,
    ) -> ContestState:
        state = ContestState(
            game_id=self._next_game_id,
            kind=kind,
            started_at=self._clock(),
            prize=prize,
            tracking_after_message_id=tracking_after_message_id,
        )
        self._next_game_id += 1
        return state

    @staticmethod
    def _copy_state(state: ContestState) -> ContestState:
        return replace(
            state,
            jackpot_hits=dict(state.jackpot_hits),
            spin_available_at=dict(state.spin_available_at),
            participants=dict(state.participants),
            case_drops=tuple(state.case_drops),
            football_picks=dict(state.football_picks),
            football_players=dict(state.football_players),
            processed_message_ids=set(state.processed_message_ids),
        )

    def _cancel_timer_locked(self, key: ContestKey) -> None:
        task = self._timers.pop(key, None)
        if task and task is not asyncio.current_task():
            task.cancel()

    async def start_intercept(
        self,
        key: ContestKey,
        seconds: float,
        *,
        prize: str = "",
        message_stars: int = 0,
        tracking_after_message_id: int | None = None,
    ) -> ContestState:
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        if message_stars < 0:
            raise ValueError("message_stars must not be negative")
        async with self._lock:
            self._cancel_timer_locked(key)
            state = self._new_state(
                ContestType.INTERCEPT,
                prize=prize,
                tracking_after_message_id=tracking_after_message_id,
            )
            state.intercept_seconds = seconds
            state.message_stars = message_stars
            self._states[key] = state
            return self._copy_state(state)

    async def start_casino(
        self,
        key: ContestKey,
        *,
        prize: str = "",
        jackpot_target: int = 1,
        tracking_after_message_id: int | None = None,
    ) -> ContestState:
        if not 1 <= jackpot_target <= 100:
            raise ValueError("jackpot_target must be between 1 and 100")
        async with self._lock:
            self._cancel_timer_locked(key)
            state = self._new_state(
                ContestType.CASINO,
                prize=prize,
                tracking_after_message_id=tracking_after_message_id,
            )
            state.jackpot_target = jackpot_target
            self._states[key] = state
            return self._copy_state(state)

    async def start_guess(
        self,
        key: ContestKey,
        secret_number: int,
        *,
        prize: str = "",
        message_stars: int = 0,
        tracking_after_message_id: int | None = None,
    ) -> ContestState:
        if not 1 <= secret_number <= 100:
            raise ValueError("secret_number must be between 1 and 100")
        if message_stars < 0:
            raise ValueError("message_stars must not be negative")
        async with self._lock:
            self._cancel_timer_locked(key)
            state = self._new_state(
                ContestType.GUESS,
                prize=prize,
                tracking_after_message_id=tracking_after_message_id,
            )
            state.secret_number = secret_number
            state.message_stars = message_stars
            self._states[key] = state
            return self._copy_state(state)

    async def start_race(
        self,
        key: ContestKey,
        seconds: float,
        *,
        prize: str = "",
        message_stars: int = 0,
        tracking_after_message_id: int | None = None,
    ) -> ContestState:
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        if message_stars < 0:
            raise ValueError("message_stars must not be negative")
        async with self._lock:
            self._cancel_timer_locked(key)
            state = self._new_state(
                ContestType.RACE,
                prize=prize,
                tracking_after_message_id=tracking_after_message_id,
            )
            state.message_stars = message_stars
            state.deadline = self._clock() + seconds
            self._states[key] = state
            self._timers[key] = asyncio.create_task(
                self._run_deadline_timer(key, state.game_id),
                name=f"race-{key[0]}-{state.game_id}",
            )
            return self._copy_state(state)

    async def start_case(
        self,
        key: ContestKey,
        case_name: str,
        drops: tuple[DropOutcome, ...],
        seconds: float,
        *,
        message_stars: int = 0,
        tracking_after_message_id: int | None = None,
    ) -> ContestState:
        if not case_name.strip() or not drops:
            raise ValueError("case name and drops are required")
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        if message_stars < 0:
            raise ValueError("message_stars must not be negative")
        if any(drop.chance < 0 or drop.chance > 100 for drop in drops):
            raise ValueError("drop chances must be between 0 and 100")
        if sum(drop.chance for drop in drops) > 100.0000001:
            raise ValueError("total drop chance must not exceed 100")
        async with self._lock:
            self._cancel_timer_locked(key)
            state = self._new_state(
                ContestType.CASE,
                tracking_after_message_id=tracking_after_message_id,
            )
            state.case_name = case_name.strip()
            state.case_drops = tuple(drops)
            state.message_stars = message_stars
            state.deadline = self._clock() + seconds
            self._states[key] = state
            self._timers[key] = asyncio.create_task(
                self._run_deadline_timer(key, state.game_id),
                name=f"case-{key[0]}-{state.game_id}",
            )
            return self._copy_state(state)

    async def start_arcade(
        self,
        key: ContestKey,
        kind: ContestType,
        seconds: float,
        *,
        prize: str = "",
        message_stars: int = 0,
        tracking_after_message_id: int | None = None,
    ) -> ContestState:
        allowed = {
            ContestType.AIRPLANE,
            ContestType.PARKOUR,
            ContestType.SNAKE,
            ContestType.PICKAXE,
        }
        if kind not in allowed:
            raise ValueError("unsupported arcade contest type")
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        if message_stars < 0:
            raise ValueError("message_stars must not be negative")
        async with self._lock:
            self._cancel_timer_locked(key)
            state = self._new_state(
                kind,
                prize=prize,
                tracking_after_message_id=tracking_after_message_id,
            )
            state.message_stars = message_stars
            state.deadline = self._clock() + seconds
            self._states[key] = state
            self._timers[key] = asyncio.create_task(
                self._run_deadline_timer(key, state.game_id),
                name=f"{kind.value}-{key[0]}-{state.game_id}",
            )
            return self._copy_state(state)

    async def start_football(
        self,
        key: ContestKey,
        team_a_name: str,
        team_b_name: str,
        seconds: float,
        *,
        message_stars: int = 0,
        tracking_after_message_id: int | None = None,
    ) -> ContestState:
        team_a = team_a_name.strip()
        team_b = team_b_name.strip()
        if not team_a or not team_b or team_a.casefold() == team_b.casefold():
            raise ValueError("team names must be non-empty and different")
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        if message_stars < 0:
            raise ValueError("message_stars must not be negative")
        async with self._lock:
            self._cancel_timer_locked(key)
            state = self._new_state(
                ContestType.FOOTBALL,
                tracking_after_message_id=tracking_after_message_id,
            )
            state.team_a_name = team_a
            state.team_b_name = team_b
            state.message_stars = message_stars
            state.deadline = self._clock() + seconds
            self._states[key] = state
            self._timers[key] = asyncio.create_task(
                self._run_deadline_timer(key, state.game_id),
                name=f"football-{key[0]}-{state.game_id}",
            )
            return self._copy_state(state)

    async def stop(self, key: ContestKey) -> ContestState | None:
        async with self._lock:
            self._cancel_timer_locked(key)
            state = self._states.pop(key, None)
            return self._copy_state(state) if state else None

    async def snapshot(self, key: ContestKey) -> ContestState | None:
        async with self._lock:
            state = self._states.get(key)
            return self._copy_state(state) if state else None

    async def submit_intercept(
        self, key: ContestKey, participant: Participant
    ) -> InterceptUpdate | None:
        async with self._lock:
            state = self._states.get(key)
            if state is None or state.kind is not ContestType.INTERCEPT:
                return None

            assert state.intercept_seconds is not None
            if state.leader and state.leader.user_id == participant.user_id:
                remaining = max(0.0, (state.deadline or self._clock()) - self._clock())
                return InterceptUpdate(False, False, remaining)

            first_leader = state.leader is None
            state.leader = participant
            state.deadline = self._clock() + state.intercept_seconds
            state.generation += 1
            generation = state.generation
            game_id = state.game_id

            self._cancel_timer_locked(key)
            self._timers[key] = asyncio.create_task(
                self._run_intercept_timer(key, game_id, generation),
                name=f"intercept-{key[0]}-{game_id}-{generation}",
            )
            return InterceptUpdate(True, first_leader, state.intercept_seconds)

    async def _run_intercept_timer(
        self, key: ContestKey, game_id: int, generation: int
    ) -> None:
        try:
            winner: Participant | None = None
            finished_state: ContestState | None = None
            while winner is None:
                async with self._lock:
                    state = self._states.get(key)
                    if (
                        state is None
                        or state.kind is not ContestType.INTERCEPT
                        or state.game_id != game_id
                        or state.generation != generation
                        or state.deadline is None
                    ):
                        return
                    remaining = state.deadline - self._clock()
                    if remaining <= 0:
                        winner = state.leader
                        finished_state = self._copy_state(state)
                        self._states.pop(key, None)
                        self._timers.pop(key, None)
                        break
                await self._sleep(remaining)

            if winner is not None and finished_state is not None:
                try:
                    await self._timed_winner_handler(key, winner, finished_state)
                except Exception:
                    logger.exception("Could not announce timed contest winner")
        except asyncio.CancelledError:
            return

    async def submit_guess(
        self, key: ContestKey, participant: Participant, number: int
    ) -> tuple[Participant, ContestState] | None:
        async with self._lock:
            state = self._states.get(key)
            if state is None or state.kind is not ContestType.GUESS:
                return None
            if number != state.secret_number:
                return None
            finished_state = self._copy_state(state)
            self._states.pop(key, None)
            return participant, finished_state

    async def submit_race(
        self, key: ContestKey, participant: Participant
    ) -> RaceJoinUpdate | None:
        async with self._lock:
            state = self._states.get(key)
            if state is None or state.kind is not ContestType.RACE:
                return None
            if participant.user_id in state.participants:
                return RaceJoinUpdate(False, len(state.participants))
            state.participants[participant.user_id] = participant
            return RaceJoinUpdate(True, len(state.participants))

    async def submit_arcade_join(
        self, key: ContestKey, participant: Participant
    ) -> RaceJoinUpdate | None:
        async with self._lock:
            state = self._states.get(key)
            if state is None or state.kind not in {
                ContestType.AIRPLANE,
                ContestType.SNAKE,
                ContestType.PICKAXE,
            }:
                return None
            if participant.user_id in state.participants:
                return RaceJoinUpdate(False, len(state.participants))
            limit = {
                ContestType.AIRPLANE: 30,
                ContestType.SNAKE: 8,
                ContestType.PICKAXE: 5,
            }[state.kind]
            if len(state.participants) >= limit:
                return RaceJoinUpdate(False, len(state.participants), True)
            state.participants[participant.user_id] = participant
            count = len(state.participants)
            ready = (
                (state.kind is ContestType.SNAKE and count == 8)
                or (state.kind is ContestType.PICKAXE and count == 5)
            )
            return RaceJoinUpdate(True, count, ready)

    async def submit_parkour(
        self,
        key: ContestKey,
        participant: Participant,
        message_id: int,
    ) -> ParkourAttemptUpdate | None:
        async with self._lock:
            state = self._states.get(key)
            if state is None or state.kind is not ContestType.PARKOUR:
                return None
            if message_id in state.processed_message_ids:
                return None
            state.processed_message_ids.add(message_id)
            state.participants[participant.user_id] = participant

            obstacle_kinds: list[str] = []
            collision_index: int | None = None
            kinds = ("wall", "trash", "animal")
            for index in range(10):
                obstacle = kinds[min(2, int(self._random_value() * len(kinds)))]
                obstacle_kinds.append(obstacle)
                if collision_index is None and self._random_value() < 0.25:
                    collision_index = index

            if collision_index is not None:
                return ParkourAttemptUpdate(tuple(obstacle_kinds), collision_index)

            finished_state = self._copy_state(state)
            self._states.pop(key, None)
            self._cancel_timer_locked(key)
            return ParkourAttemptUpdate(
                tuple(obstacle_kinds),
                None,
                winner=participant,
                finished_state=finished_state,
            )

    async def submit_football_pick(
        self,
        key: ContestKey,
        participant: Participant,
        choice: str,
    ) -> FootballPickUpdate | None:
        if choice not in {"a", "draw", "b"}:
            return None
        async with self._lock:
            state = self._states.get(key)
            if state is None or state.kind is not ContestType.FOOTBALL:
                return None
            if (
                state.tracking_after_message_id is None
                or state.deadline is None
                or state.deadline <= self._clock()
            ):
                return None
            previous = state.football_picks.get(participant.user_id)
            state.participants[participant.user_id] = participant
            state.football_picks[participant.user_id] = choice
            counts = {
                option: sum(value == option for value in state.football_picks.values())
                for option in ("a", "draw", "b")
            }
            return FootballPickUpdate(True, choice, previous not in {None, choice}, counts)

    async def submit_football_player(
        self,
        key: ContestKey,
        participant: Participant,
        player_index: int,
    ) -> bool:
        if not 0 <= player_index < 10:
            return False
        async with self._lock:
            state = self._states.get(key)
            if (
                state is None
                or state.kind is not ContestType.FOOTBALL
                or state.deadline is None
                or state.deadline <= self._clock()
            ):
                return False
            state.participants[participant.user_id] = participant
            state.football_players[participant.user_id] = player_index
            return True

    async def finish_collection_now(self, key: ContestKey) -> ContestState | None:
        async with self._lock:
            state = self._states.get(key)
            if state is None or state.kind not in {
                ContestType.SNAKE,
                ContestType.PICKAXE,
            }:
                return None
            required = 8 if state.kind is ContestType.SNAKE else 5
            if len(state.participants) < required:
                return None
            self._states.pop(key, None)
            self._cancel_timer_locked(key)
            return self._copy_state(state)

    async def open_case(
        self, key: ContestKey, message_id: int
    ) -> CaseOpenUpdate | None:
        async with self._lock:
            state = self._states.get(key)
            if state is None or state.kind is not ContestType.CASE:
                return None
            if message_id in state.processed_message_ids:
                return None
            state.processed_message_ids.add(message_id)
            roll = self._random_value() * 100
            cumulative = 0.0
            outcome: DropOutcome | None = None
            for drop in state.case_drops:
                cumulative += drop.chance
                if roll < cumulative:
                    outcome = drop
                    break
            return CaseOpenUpdate(outcome, self._copy_state(state))

    async def _run_deadline_timer(self, key: ContestKey, game_id: int) -> None:
        try:
            while True:
                async with self._lock:
                    state = self._states.get(key)
                    if (
                        state is None
                        or state.game_id != game_id
                        or state.kind
                        not in {
                            ContestType.RACE,
                            ContestType.CASE,
                            ContestType.AIRPLANE,
                            ContestType.PARKOUR,
                            ContestType.SNAKE,
                            ContestType.PICKAXE,
                            ContestType.FOOTBALL,
                        }
                        or state.deadline is None
                    ):
                        return
                    remaining = state.deadline - self._clock()
                    if remaining <= 0:
                        finished_state = self._copy_state(state)
                        self._states.pop(key, None)
                        self._timers.pop(key, None)
                        break
                await self._sleep(remaining)
            if self._timed_finish_handler is not None:
                try:
                    await self._timed_finish_handler(key, finished_state)
                except Exception:
                    logger.exception("Could not finish timed contest")
        except asyncio.CancelledError:
            return

    async def reserve_spin(
        self, key: ContestKey, user_id: int, cooldown_seconds: float
    ) -> SpinReservation:
        async with self._lock:
            state = self._states.get(key)
            if state is None or state.kind is not ContestType.CASINO:
                return SpinReservation(SpinStatus.NOT_ACTIVE)

            now = self._clock()
            available_at = state.spin_available_at.get(user_id, 0.0)
            if available_at > now:
                return SpinReservation(
                    SpinStatus.COOLDOWN,
                    game_id=state.game_id,
                    retry_after=available_at - now,
                )

            state.spin_available_at[user_id] = now + max(0.0, cooldown_seconds)
            return SpinReservation(SpinStatus.ACCEPTED, game_id=state.game_id)

    async def resolve_spin(
        self,
        key: ContestKey,
        game_id: int,
        participant: Participant,
        value: int,
    ) -> CasinoSpinUpdate | None:
        async with self._lock:
            state = self._states.get(key)
            if (
                state is None
                or state.kind is not ContestType.CASINO
                or state.game_id != game_id
            ):
                return None

            current_hits = state.jackpot_hits.get(participant.user_id, 0)
            if value != 64:
                return CasinoSpinUpdate(False, current_hits, state.jackpot_target)

            current_hits += 1
            state.jackpot_hits[participant.user_id] = current_hits
            if current_hits < state.jackpot_target:
                return CasinoSpinUpdate(True, current_hits, state.jackpot_target)

            finished_state = self._copy_state(state)
            self._states.pop(key, None)
            return CasinoSpinUpdate(
                True,
                current_hits,
                state.jackpot_target,
                winner=participant,
                finished_state=finished_state,
            )

    async def close(self) -> None:
        async with self._lock:
            tasks = list(self._timers.values())
            self._timers.clear()
            self._states.clear()
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
