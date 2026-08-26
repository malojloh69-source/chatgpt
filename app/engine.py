from __future__ import annotations

import asyncio
import logging
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


@dataclass(frozen=True, slots=True)
class Participant:
    user_id: int
    full_name: str
    username: str | None = None


@dataclass(slots=True)
class ContestState:
    game_id: int
    kind: ContestType
    started_at: float
    prize: str = ""
    channel_id: int | None = None
    intercept_seconds: float | None = None
    message_stars: int = 0
    leader: Participant | None = None
    deadline: float | None = None
    secret_number: int | None = None
    jackpot_target: int = 1
    jackpot_hits: dict[int, int] = field(default_factory=dict)
    spin_available_at: dict[int, float] = field(default_factory=dict)
    generation: int = 0


TimedWinnerHandler = Callable[
    [ContestKey, Participant, ContestState], Awaitable[None]
]


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


class ContestManager:
    """Keeps one active contest per selected discussion group."""

    def __init__(
        self,
        timed_winner_handler: TimedWinnerHandler,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._states: dict[ContestKey, ContestState] = {}
        self._timers: dict[ContestKey, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._clock = clock
        self._sleep = sleep
        self._timed_winner_handler = timed_winner_handler
        self._next_game_id = 1

    def _new_state(
        self,
        kind: ContestType,
        *,
        prize: str = "",
        channel_id: int | None = None,
    ) -> ContestState:
        state = ContestState(
            game_id=self._next_game_id,
            kind=kind,
            started_at=self._clock(),
            prize=prize,
            channel_id=channel_id,
        )
        self._next_game_id += 1
        return state

    @staticmethod
    def _copy_state(state: ContestState) -> ContestState:
        return replace(
            state,
            jackpot_hits=dict(state.jackpot_hits),
            spin_available_at=dict(state.spin_available_at),
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
        channel_id: int | None = None,
        message_stars: int = 0,
    ) -> ContestState:
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        if message_stars < 0:
            raise ValueError("message_stars must not be negative")
        async with self._lock:
            self._cancel_timer_locked(key)
            state = self._new_state(
                ContestType.INTERCEPT, prize=prize, channel_id=channel_id
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
        channel_id: int | None = None,
        jackpot_target: int = 1,
    ) -> ContestState:
        if not 1 <= jackpot_target <= 100:
            raise ValueError("jackpot_target must be between 1 and 100")
        async with self._lock:
            self._cancel_timer_locked(key)
            state = self._new_state(
                ContestType.CASINO, prize=prize, channel_id=channel_id
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
        channel_id: int | None = None,
    ) -> ContestState:
        if not 1 <= secret_number <= 100:
            raise ValueError("secret_number must be between 1 and 100")
        async with self._lock:
            self._cancel_timer_locked(key)
            state = self._new_state(
                ContestType.GUESS, prize=prize, channel_id=channel_id
            )
            state.secret_number = secret_number
            self._states[key] = state
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
