from __future__ import annotations

import asyncio
import logging
import math
import re
import secrets
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

from .config import Settings
from .engine import (
    ContestKey,
    ContestManager,
    ContestState,
    ContestType,
    DropOutcome,
    FootballJoinStatus,
    FootballPlayerStatus,
    ParkourAttemptUpdate,
    Participant,
    SpinStatus,
)
from .storage import BotStorage, KnownChat, SavedCase, SavedDrop

logger = logging.getLogger(__name__)

GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
ACTIVE_MEMBER_STATUSES = {
    ChatMemberStatus.CREATOR,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.RESTRICTED,
}
ADMIN_STATUSES = {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}

MARKDOWN_LINK_RE = re.compile(r"^\[([^\]]+)]\((https?://[^\s)]+)\)$")
CUSTOM_EMOJI_TAG_RE = re.compile(r"</?tg-emoji(?:\s[^>]*)?>")
DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhсмч]?)\s*$", re.IGNORECASE)

# Premium emoji IDs supplied in the brief, kept in the requested order.
CASINO_START_IDS = (
    "5213134259098761044",
    "5400182197963496946",
    "5436040291507247633",
)
CASINO_COMBINATION_ID = "5913646886819991524"
WINNER_IDS = (
    "5280769763398671636",
    "5400182197963496946",
    "5436040291507247633",
)
INTERCEPT_START_IDS = (
    "5213134259098761044",
    "5954135079662916434",
    "5413704112220949842",
    "5406669204898201943",
)
INTERCEPT_UPDATE_IDS = (
    "5431870019996767493",
    "6032693626394382504",
)
ACTIVITY_IDS = (
    "5348125643852758491",
    "5280598054901145762",
    "5283228279988309088",
    "5280947338821524402",
    "5280922999241859582",
    "5350596259365273418",
)
ACTIVITY_DROPS = (
    DropOutcome("🧸", 0.05),
    DropOutcome("💝", 0.05),
    DropOutcome("🌹", 0.04),
    DropOutcome("💎", 0.03),
)
AIRPLANE_IDS = (
    "5247023951950927550",  # airplane
    "5174659134607328175",  # bird
)
PARKOUR_IDS = (
    "6046353095569445454",  # runner
    "5026367778030879516",  # wall
    "5019500511871632068",  # trash
    "5389040357012945045",  # animal
)
SNAKE_IDS = (
    "5197646705813634076",  # snake
    "5262482084710078610",  # apple
)
PICKAXE_IDS = (
    "5195005047523530190",  # pickaxe
    "5197247342574587535",  # diamond
    "5197697622650933048",  # emerald
    "5197369053357822982",  # gold
    "5197319493730192308",  # iron
    "5197629538829356988",  # coal
)
FOOTBALL_STATUS_IDS = (
    "5945082439654183858",  # occupied / check
    "5834629976883732083",  # available / cross
)
FOOTBALL_BALL_ID = "6037464686520178031"
FOOTBALL_MULTIPLIERS = (2.0, 1.5, 1.3, 1.2, 1.1)
FOOTBALLERS = (
    ("Мбаппе", "5258486897541400778", "🇫🇷"),
    ("Роналдо", "5447194519143480167", "🐐"),
    ("Месси", "5445275244287785469", "🐐"),
    ("Неймар", "5447264557175175819", "🇧🇷"),
    ("Винисиус", "5217692284551711148", "🇧🇷"),
    ("Зидан", "5251235893234132621", "🇸🇪"),
    ("Роналдо прайм", "5465518047924081162", "🇧🇷"),
    ("Рональдино", "5465496852260476264", "🇧🇷"),
    ("Сон", "5361682145481881497", "🇰🇷"),
    ("Холланд", "5217535423756126999", "🇳🇴"),
)
PICKAXE_RESOURCES = (
    (PICKAXE_IDS[1], "💎", "Алмаз"),
    (PICKAXE_IDS[2], "💚", "Изумруд"),
    (PICKAXE_IDS[3], "🟨", "Золото"),
    (PICKAXE_IDS[4], "⚙️", "Железо"),
    (PICKAXE_IDS[5], "⚫", "Уголь"),
)

ARCADE_TITLES = {
    ContestType.AIRPLANE: "Самолётик",
    ContestType.PARKOUR: "Паркур",
    ContestType.SNAKE: "Змейка",
    ContestType.PICKAXE: "Кирка",
}


class CasinoSetup(StatesGroup):
    prize = State()
    jackpot_target = State()
    screenshot = State()


class InterceptSetup(StatesGroup):
    prize = State()
    duration = State()
    stars = State()
    screenshot = State()


class GuessSetup(StatesGroup):
    prize = State()
    screenshot = State()
    secret_number = State()
    stars = State()


class RaceSetup(StatesGroup):
    prize = State()
    stars = State()
    duration = State()
    screenshot = State()


class CaseSetup(StatesGroup):
    drop_name = State()
    drop_chance = State()
    drops_ready = State()
    name = State()
    stars = State()
    duration = State()
    screenshot = State()


class ArcadeSetup(StatesGroup):
    prize = State()
    stars = State()
    duration = State()
    screenshot = State()


class FootballSetup(StatesGroup):
    team_a = State()
    team_b = State()
    stars = State()
    duration = State()
    screenshot = State()


def game_key(chat_id: int) -> ContestKey:
    return chat_id, None


def participant_from(user: User) -> Participant:
    return Participant(user.id, user.full_name, user.username)


def participant_link(participant: Participant) -> str:
    if participant.username:
        return html.link(
            f"@{html.quote(participant.username)}",
            f"https://t.me/{participant.username}",
        )
    return html.link(
        html.quote(participant.full_name), f"tg://user?id={participant.user_id}"
    )


def premium(emoji_id: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def without_premium(text: str) -> str:
    return CUSTOM_EMOJI_TAG_RE.sub("", text)


def without_premium_markup(
    markup: InlineKeyboardMarkup | None,
) -> InlineKeyboardMarkup | None:
    if markup is None:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button.model_copy(update={"icon_custom_emoji_id": None})
                for button in row
            ]
            for row in markup.inline_keyboard
        ]
    )


def format_prize(raw: str) -> str:
    value = raw.strip()
    match = MARKDOWN_LINK_RE.fullmatch(value)
    if match:
        label, url = match.groups()
        return html.link(html.quote(label), html.quote(url))
    return html.quote(value)


def parse_duration(raw: str) -> int | None:
    match = DURATION_RE.fullmatch(raw)
    if not match:
        return None
    value = int(match.group(1))
    suffix = match.group(2).lower()
    multiplier = 1
    if suffix in {"m", "м"}:
        multiplier = 60
    elif suffix in {"h", "ч"}:
        multiplier = 3600
    seconds = value * multiplier
    return seconds if 10 <= seconds <= 86400 else None


def parse_stars(raw: str) -> int | None:
    value = raw.replace(" ", "").strip()
    if not value.isdigit():
        return None
    stars = int(value)
    return stars if 0 <= stars <= 1_000_000 else None


def parse_chance(raw: str) -> float | None:
    value = raw.strip().replace(",", ".").removesuffix("%").strip()
    try:
        chance = float(value)
    except ValueError:
        return None
    return chance if math.isfinite(chance) and 0 <= chance <= 100 else None


def format_chance(chance: float) -> str:
    return f"{chance:.6f}".rstrip("0").rstrip(".")


def select_drop(
    drops: tuple[DropOutcome, ...], roll_percent: float
) -> DropOutcome | None:
    cumulative = 0.0
    for drop in drops:
        cumulative += drop.chance
        if roll_percent < cumulative:
            return drop
    return None


def format_duration(seconds: float) -> str:
    total = max(0, math.ceil(seconds))
    if total % 3600 == 0:
        return f"{total // 3600} ч."
    if total % 60 == 0:
        return f"{total // 60} мин."
    minutes, rest = divmod(total, 60)
    if minutes:
        return f"{minutes} мин. {rest} сек."
    return f"{total} сек."


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="setup:cancel")]
        ]
    )


def screenshot_keyboard(kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏭ Без скриншота", callback_data=f"setup:{kind}:skip"
                )
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="setup:cancel")],
        ]
    )


def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎛 Открыть панель", callback_data="panel:home")]
        ]
    )


def panel_keyboard(activity_enabled: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Выбрать группу", callback_data="panel:group"
                )
            ],
            [
                InlineKeyboardButton(text="🎰 777", callback_data="game:casino"),
                InlineKeyboardButton(
                    text="⚡ Перебив", callback_data="game:intercept"
                ),
            ],
            [
                InlineKeyboardButton(text="🔢 Угадай число", callback_data="game:guess"),
                InlineKeyboardButton(text="🏎 Гонки", callback_data="game:race"),
            ],
            [InlineKeyboardButton(text="📦 Кейсы", callback_data="game:cases")],
            [
                InlineKeyboardButton(
                    text="✈️ Самолётик", callback_data="game:airplane"
                ),
                InlineKeyboardButton(text="🏃 Паркур", callback_data="game:parkour"),
            ],
            [
                InlineKeyboardButton(text="🐍 Змейка", callback_data="game:snake"),
                InlineKeyboardButton(text="💅 Кирка", callback_data="game:pickaxe"),
            ],
            [InlineKeyboardButton(text="⚽ Футбол", callback_data="game:football")],
            [
                InlineKeyboardButton(
                    text=(
                        "🔕 Выключить подарки за актив"
                        if activity_enabled
                        else "🔔 Включить подарки за актив"
                    ),
                    callback_data="game:activity",
                )
            ],
            [
                InlineKeyboardButton(text="ℹ️ Статус", callback_data="game:status"),
                InlineKeyboardButton(text="⛔ Стоп", callback_data="game:stop"),
            ],
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="panel:home")],
        ]
    )


def cases_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать кейс", callback_data="case:create")],
            [
                InlineKeyboardButton(
                    text="💾 Сохранённые кейсы", callback_data="case:saved"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📚 Сохранённые дропы", callback_data="case:drops"
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="panel:home")],
        ]
    )


def case_drop_keyboard(has_drops: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="➕ Добавить новый дроп", callback_data="case:add_drop")],
        [
            InlineKeyboardButton(
                text="📚 Взять сохранённый дроп", callback_data="case:reuse_drop"
            )
        ]
    ]
    if has_drops:
        rows.append(
            [InlineKeyboardButton(text="✅ Дропы готовы", callback_data="case:drops_ready")]
        )
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="setup:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def saved_drops_keyboard(drops: list[SavedDrop]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for drop in drops[:25]:
        label = f"{drop.name} — {format_chance(drop.chance)}%"
        if len(label) > 48:
            label = f"{label[:45]}…"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label, callback_data=f"case:pick_drop:{drop.drop_id}"
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="game:cases")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def saved_cases_keyboard(cases: list[SavedCase]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for saved_case in cases[:25]:
        label = saved_case.name
        if len(label) > 44:
            label = f"{label[:41]}…"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"▶️ {label}",
                    callback_data=f"case:start:{saved_case.case_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="game:cases")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def choices_keyboard(chats: list[KnownChat]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for chat in chats:
        title = chat.title if len(chat.title) <= 36 else f"{chat.title[:33]}…"
        rows.append(
            [
                InlineKeyboardButton(
                    text=title,
                    callback_data=f"pick:{chat.chat_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="panel:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def football_pick_keyboard(
    team_a: str,
    team_b: str,
    selected_players: set[int] | None = None,
    *,
    premium_icons: bool = True,
) -> InlineKeyboardMarkup:
    def label(value: str) -> str:
        return value if len(value) <= 16 else f"{value[:13]}…"

    occupied = selected_players or set()
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"🔵 {label(team_a)} — 5 игроков",
                callback_data="football:info:a",
            ),
            InlineKeyboardButton(
                text=f"🔴 {label(team_b)} — 5 игроков",
                callback_data="football:info:b",
            ),
        ],
    ]
    for position in range(5):
        player_row: list[InlineKeyboardButton] = []
        for player_index in (position, position + 5):
            name, _, _ = FOOTBALLERS[player_index]
            is_occupied = player_index in occupied
            status = "✅" if is_occupied else "❌"
            button_args = {
                "text": f"{status} {name}",
                "callback_data": f"football:player:{player_index}",
            }
            if premium_icons:
                button_args["icon_custom_emoji_id"] = (
                    FOOTBALL_STATUS_IDS[0]
                    if is_occupied
                    else FOOTBALL_STATUS_IDS[1]
                )
            player_row.append(InlineKeyboardButton(**button_args))
        rows.append(player_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


class ContestBot:
    def __init__(self, bot: Bot, settings: Settings, storage: BotStorage) -> None:
        self.bot = bot
        self.settings = settings
        self.storage = storage
        self._random = secrets.SystemRandom()
        self.manager = ContestManager(
            self._announce_timed_winner,
            timed_finish_handler=self._finish_timed_game,
        )
        self.router = Router(name="monster-contests")
        self._register_handlers()

    def _register_handlers(self) -> None:
        self.router.my_chat_member.register(self.on_my_chat_member)

        self.router.message.register(
            self.cmd_access,
            F.chat.type == ChatType.PRIVATE,
            F.text == self.settings.access_code,
        )
        self.router.message.register(self.cmd_start, CommandStart())
        self.router.message.register(self.cmd_help, Command("help"))
        self.router.message.register(self.cmd_panel, Command("contest"))

        self.router.callback_query.register(self.cb_panel, F.data == "panel:home")
        self.router.callback_query.register(
            self.cb_choose_group, F.data == "panel:group"
        )
        self.router.callback_query.register(
            self.cb_pick_group, F.data.startswith("pick:")
        )
        self.router.callback_query.register(
            self.cb_casino_setup, F.data == "game:casino"
        )
        self.router.callback_query.register(
            self.cb_intercept_setup, F.data == "game:intercept"
        )
        self.router.callback_query.register(
            self.cb_guess_setup, F.data == "game:guess"
        )
        self.router.callback_query.register(
            self.cb_race_setup, F.data == "game:race"
        )
        self.router.callback_query.register(
            self.cb_cases, F.data == "game:cases"
        )
        self.router.callback_query.register(
            self.cb_arcade_setup,
            F.data.in_(
                {
                    "game:airplane",
                    "game:parkour",
                    "game:snake",
                    "game:pickaxe",
                }
            ),
        )
        self.router.callback_query.register(
            self.cb_football_setup, F.data == "game:football"
        )
        self.router.callback_query.register(
            self.cb_football_info, F.data.startswith("football:info:")
        )
        self.router.callback_query.register(
            self.cb_football_player, F.data.startswith("football:player:")
        )
        self.router.callback_query.register(
            self.cb_create_case, F.data == "case:create"
        )
        self.router.callback_query.register(
            self.cb_saved_cases, F.data == "case:saved"
        )
        self.router.callback_query.register(
            self.cb_saved_drops, F.data == "case:drops"
        )
        self.router.callback_query.register(
            self.cb_reuse_drop, F.data == "case:reuse_drop"
        )
        self.router.callback_query.register(
            self.cb_add_case_drop, F.data == "case:add_drop"
        )
        self.router.callback_query.register(
            self.cb_pick_saved_drop, F.data.startswith("case:pick_drop:")
        )
        self.router.callback_query.register(
            self.cb_case_drops_ready, F.data == "case:drops_ready"
        )
        self.router.callback_query.register(
            self.cb_start_saved_case, F.data.startswith("case:start:")
        )
        self.router.callback_query.register(
            self.cb_toggle_activity, F.data == "game:activity"
        )
        self.router.callback_query.register(
            self.cb_status, F.data == "game:status"
        )
        self.router.callback_query.register(self.cb_stop, F.data == "game:stop")
        self.router.callback_query.register(
            self.cb_cancel_setup, F.data == "setup:cancel"
        )
        self.router.callback_query.register(
            self.cb_skip_casino_screenshot, F.data == "setup:casino:skip"
        )
        self.router.callback_query.register(
            self.cb_skip_intercept_screenshot, F.data == "setup:intercept:skip"
        )
        self.router.callback_query.register(
            self.cb_skip_guess_screenshot, F.data == "setup:guess:skip"
        )
        self.router.callback_query.register(
            self.cb_skip_race_screenshot, F.data == "setup:race:skip"
        )
        self.router.callback_query.register(
            self.cb_skip_case_screenshot, F.data == "setup:case:skip"
        )
        self.router.callback_query.register(
            self.cb_skip_arcade_screenshot, F.data == "setup:arcade:skip"
        )
        self.router.callback_query.register(
            self.cb_skip_football_screenshot, F.data == "setup:football:skip"
        )

        self.router.message.register(self.receive_casino_prize, CasinoSetup.prize)
        self.router.message.register(
            self.receive_casino_target, CasinoSetup.jackpot_target
        )
        self.router.message.register(
            self.receive_casino_screenshot, CasinoSetup.screenshot
        )
        self.router.message.register(
            self.receive_intercept_prize, InterceptSetup.prize
        )
        self.router.message.register(
            self.receive_intercept_duration, InterceptSetup.duration
        )
        self.router.message.register(
            self.receive_intercept_stars, InterceptSetup.stars
        )
        self.router.message.register(
            self.receive_intercept_screenshot, InterceptSetup.screenshot
        )
        self.router.message.register(self.receive_guess_prize, GuessSetup.prize)
        self.router.message.register(
            self.receive_guess_screenshot, GuessSetup.screenshot
        )
        self.router.message.register(
            self.receive_guess_number, GuessSetup.secret_number
        )
        self.router.message.register(self.receive_guess_stars, GuessSetup.stars)
        self.router.message.register(self.receive_race_prize, RaceSetup.prize)
        self.router.message.register(self.receive_race_stars, RaceSetup.stars)
        self.router.message.register(
            self.receive_race_duration, RaceSetup.duration
        )
        self.router.message.register(
            self.receive_race_screenshot, RaceSetup.screenshot
        )
        self.router.message.register(self.receive_case_drop_name, CaseSetup.drop_name)
        self.router.message.register(
            self.receive_case_drop_chance, CaseSetup.drop_chance
        )
        self.router.message.register(self.receive_case_name, CaseSetup.name)
        self.router.message.register(self.receive_case_stars, CaseSetup.stars)
        self.router.message.register(
            self.receive_case_duration, CaseSetup.duration
        )
        self.router.message.register(
            self.receive_case_screenshot, CaseSetup.screenshot
        )
        self.router.message.register(self.receive_arcade_prize, ArcadeSetup.prize)
        self.router.message.register(self.receive_arcade_stars, ArcadeSetup.stars)
        self.router.message.register(
            self.receive_arcade_duration, ArcadeSetup.duration
        )
        self.router.message.register(
            self.receive_arcade_screenshot, ArcadeSetup.screenshot
        )
        self.router.message.register(self.receive_football_team_a, FootballSetup.team_a)
        self.router.message.register(self.receive_football_team_b, FootballSetup.team_b)
        self.router.message.register(self.receive_football_stars, FootballSetup.stars)
        self.router.message.register(
            self.receive_football_duration, FootballSetup.duration
        )
        self.router.message.register(
            self.receive_football_screenshot, FootballSetup.screenshot
        )

        self.router.message.register(self.on_message)

    async def _remember_chat(self, chat) -> None:
        if chat.type not in GROUP_TYPES:
            return
        kind = chat.type.value if hasattr(chat.type, "value") else str(chat.type)
        self.storage.upsert_chat(
            chat.id,
            chat.title or chat.username or str(chat.id),
            kind,
            chat.username,
        )

    async def on_my_chat_member(self, update: ChatMemberUpdated) -> None:
        if update.new_chat_member.status in ACTIVE_MEMBER_STATUSES:
            await self._remember_chat(update.chat)

    async def _is_user_admin(self, chat_id: int, user_id: int) -> bool:
        try:
            member = await self.bot.get_chat_member(chat_id, user_id)
        except TelegramAPIError:
            return False
        return member.status in ADMIN_STATUSES

    async def _is_bot_ready(self, chat_id: int) -> bool:
        try:
            member = await self.bot.get_chat_member(chat_id, self.bot.id)
        except TelegramAPIError:
            return False
        # Administrator status guarantees that Telegram delivers ordinary
        # messages and native dice even when Privacy Mode is still enabled.
        return member.status in ADMIN_STATUSES

    async def _require_access_message(self, message: Message) -> bool:
        if message.chat.type != ChatType.PRIVATE or message.from_user is None:
            return False
        if self.storage.is_authorized(message.from_user.id):
            return True
        await message.answer(
            "🔒 <b>Доступ закрыт.</b>\n\n"
            "Введите выданный вам промокод-команду."
        )
        return False

    async def _require_access_callback(self, callback: CallbackQuery) -> bool:
        if self.storage.is_authorized(callback.from_user.id):
            return True
        await callback.answer("Сначала введите промокод", show_alert=True)
        return False

    async def cmd_access(self, message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        self.storage.authorize(message.from_user.id)
        await state.clear()
        await message.answer(
            "✅ <b>Промокод принят.</b>\n\n"
            "Теперь управление ботом доступно в личной панели.",
            reply_markup=home_keyboard(),
        )

    async def cmd_start(self, message: Message, state: FSMContext) -> None:
        if message.chat.type != ChatType.PRIVATE:
            return
        await state.clear()
        if message.from_user and self.storage.is_authorized(message.from_user.id):
            await message.answer(
                "🎁 <b>Monster Contest Bot</b>\n\n"
                "Выберите группу, затем настройте игру.",
                reply_markup=home_keyboard(),
            )
            return
        await message.answer(
            "🔒 <b>Для доступа нужен промокод.</b>\n\n"
            "Введите выданный вам промокод-команду."
        )

    async def cmd_help(self, message: Message) -> None:
        if not await self._require_access_message(message):
            return
        await message.answer(
            "<b>Как запустить игру</b>\n\n"
            "1. Добавьте бота в группу и назначьте администратором.\n"
            "2. Через @BotFather отключите Privacy Mode, чтобы бот видел сообщения "
            "и слоты в группе.\n"
            "3. Отправьте в группе любое сообщение, чтобы она появилась в списке.\n"
            "4. Откройте /contest, выберите группу, затем игру.\n\n"
            "<i>Все настройки выполняются только здесь, в личных сообщениях с ботом.</i>"
        )

    async def cmd_panel(self, message: Message, state: FSMContext) -> None:
        if not await self._require_access_message(message):
            return
        await state.clear()
        await self._show_panel(message, message.from_user.id)

    def _panel_text(self, user_id: int) -> str:
        targets = self.storage.get_targets(user_id)
        group = self.storage.get_chat(targets.group_id)
        group_name = html.quote(group.title) if group else "<i>не выбран</i>"
        activity = self.storage.is_activity_enabled(targets.group_id)
        return (
            "🎛 <b>Панель управления</b>\n\n"
            f"💬 <b>Группа игры:</b> {group_name}\n\n"
            f"🔔 <b>Подарки за актив:</b> "
            f"{'включены' if activity else 'выключены'}\n\n"
            "Выберите игру и заполните настройки."
        )

    def _panel_keyboard(self, user_id: int) -> InlineKeyboardMarkup:
        targets = self.storage.get_targets(user_id)
        return panel_keyboard(self.storage.is_activity_enabled(targets.group_id))

    async def _show_panel(self, message: Message, user_id: int) -> None:
        await message.answer(
            self._panel_text(user_id), reply_markup=self._panel_keyboard(user_id)
        )

    async def cb_panel(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not await self._require_access_callback(callback):
            return
        await state.clear()
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                self._panel_text(callback.from_user.id),
                reply_markup=self._panel_keyboard(callback.from_user.id),
            )

    async def _available_chats(
        self, user_id: int, kinds: tuple[str, ...]
    ) -> list[KnownChat]:
        available: list[KnownChat] = []
        for chat in self.storage.list_chats(kinds):
            if await self._is_user_admin(chat.chat_id, user_id):
                available.append(chat)
            if len(available) >= 25:
                break
        return available

    async def cb_choose_group(self, callback: CallbackQuery) -> None:
        if not await self._require_access_callback(callback):
            return
        if not isinstance(callback.message, Message):
            await callback.answer("Сообщение недоступно", show_alert=True)
            return
        chats = await self._available_chats(
            callback.from_user.id, (ChatType.GROUP.value, ChatType.SUPERGROUP.value)
        )
        await callback.answer()
        text = "💬 <b>Выберите группу для игры:</b>"
        if not chats:
            text += (
                "\n\nСписок пуст. Добавьте бота в группу, сделайте себя "
                "администратором и отправьте там любое сообщение, затем обновите панель."
            )
        await callback.message.edit_text(
            text, reply_markup=choices_keyboard(chats)
        )

    async def cb_pick_group(self, callback: CallbackQuery) -> None:
        if not await self._require_access_callback(callback):
            return
        if not callback.data or not isinstance(callback.message, Message):
            return
        try:
            _, raw_chat_id = callback.data.split(":", 1)
            chat_id = int(raw_chat_id)
        except (ValueError, TypeError):
            await callback.answer("Некорректный выбор", show_alert=True)
            return
        chat = self.storage.get_chat(chat_id)
        if chat is None or not await self._is_user_admin(chat_id, callback.from_user.id):
            await callback.answer("Вы больше не администратор этого чата", show_alert=True)
            return
        if chat.kind not in {ChatType.GROUP.value, ChatType.SUPERGROUP.value}:
            await callback.answer("Это не группа", show_alert=True)
            return
        self.storage.set_group(callback.from_user.id, chat_id)
        await callback.answer("Сохранено")
        await callback.message.edit_text(
            self._panel_text(callback.from_user.id),
            reply_markup=self._panel_keyboard(callback.from_user.id),
        )

    async def _validate_targets(self, user_id: int) -> int | str:
        targets = self.storage.get_targets(user_id)
        if targets.group_id is None:
            return "Сначала выберите группу для игры."
        if not await self._is_user_admin(targets.group_id, user_id):
            return "Вы должны быть администратором выбранной группы."
        if not await self._is_bot_ready(targets.group_id):
            return "Назначьте бота администратором выбранной группы."
        return targets.group_id

    async def cb_casino_setup(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        targets = await self._validate_targets(callback.from_user.id)
        if isinstance(targets, str):
            await callback.answer(targets, show_alert=True)
            return
        await state.clear()
        await state.set_state(CasinoSetup.prize)
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "🎰 <b>Настройка казино 777</b>\n\n"
                "Введите приз текстом или ссылкой. Например:\n"
                "<code>https://t.me/nft/ViceCream-431517</code>",
                reply_markup=back_keyboard(),
            )

    async def receive_casino_prize(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        if not message.text or not message.text.strip():
            await message.answer("Введите приз текстом.", reply_markup=back_keyboard())
            return
        prize = message.text.strip()
        if len(prize) > 500:
            await message.answer("Приз должен быть не длиннее 500 символов.")
            return
        await state.update_data(prize=prize)
        await state.set_state(CasinoSetup.jackpot_target)
        await message.answer(
            "Сколько раз <b>один и тот же игрок</b> должен выбить 777 для победы?\n"
            "Введите число от <b>1 до 100</b>.",
            reply_markup=back_keyboard(),
        )

    async def receive_casino_target(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        raw = (message.text or "").strip()
        if not raw.isdigit() or not 1 <= int(raw) <= 100:
            await message.answer("Введите целое число от 1 до 100.")
            return
        await state.update_data(jackpot_target=int(raw))
        await state.set_state(CasinoSetup.screenshot)
        await message.answer(
            "Прикрепите скриншот приза как фото или файл-изображение.\n"
            "Либо нажмите <i>«Без скриншота»</i>.",
            reply_markup=screenshot_keyboard("casino"),
        )

    @staticmethod
    def _screenshot_from(message: Message) -> tuple[str, str] | None:
        if message.photo:
            return "photo", message.photo[-1].file_id
        if message.document and (message.document.mime_type or "").startswith("image/"):
            return "document", message.document.file_id
        return None

    async def receive_casino_screenshot(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        screenshot = self._screenshot_from(message)
        if screenshot is None:
            await message.answer("Отправьте фото/изображение или нажмите «Без скриншота».")
            return
        await state.update_data(
            screenshot_kind=screenshot[0], screenshot_file_id=screenshot[1]
        )
        await self._finish_casino_setup(message, state)

    async def cb_skip_casino_screenshot(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        if await state.get_state() != CasinoSetup.screenshot.state:
            await callback.answer("Настройка уже завершена", show_alert=True)
            return
        await callback.answer()
        if isinstance(callback.message, Message):
            await self._finish_casino_setup(callback.message, state, callback.from_user.id)

    async def _finish_casino_setup(
        self, message: Message, state: FSMContext, user_id: int | None = None
    ) -> None:
        operator_id = user_id or (message.from_user.id if message.from_user else 0)
        targets = await self._validate_targets(operator_id)
        if isinstance(targets, str):
            await message.answer(targets, reply_markup=home_keyboard())
            await state.clear()
            return
        group_id = targets
        if await self.manager.snapshot(game_key(group_id)) is not None:
            await message.answer(
                "В выбранном чате уже идёт игра. Сначала остановите её в панели.",
                reply_markup=home_keyboard(),
            )
            await state.clear()
            return
        data = await state.get_data()
        prize = str(data["prize"])
        target = int(data["jackpot_target"])
        text = self._casino_start_text(prize, target)
        try:
            group_post = await self._send_public(
                group_id,
                self._tracking_text(text),
                data.get("screenshot_kind"),
                data.get("screenshot_file_id"),
            )
        except TelegramAPIError as exc:
            logger.exception("Could not publish casino announcements")
            await message.answer(
                "Не удалось опубликовать стартовое сообщение в группе: "
                f"<code>{html.quote(str(exc))}</code>"
            )
            return
        await self.manager.start_casino(
            game_key(group_id),
            prize=prize,
            jackpot_target=target,
            tracking_after_message_id=group_post.message_id,
        )
        await state.clear()
        await message.answer(
            "✅ <b>Казино запущено.</b> Стартовое сообщение опубликовано в группе. "
            "Бот учитывает слоты 🎰, отправленные после своего сообщения.",
            reply_markup=home_keyboard(),
        )

    async def cb_intercept_setup(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        targets = await self._validate_targets(callback.from_user.id)
        if isinstance(targets, str):
            await callback.answer(targets, show_alert=True)
            return
        await state.clear()
        await state.set_state(InterceptSetup.prize)
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "⚡ <b>Настройка «Перебива»</b>\n\nВведите приз текстом или ссылкой.",
                reply_markup=back_keyboard(),
            )

    async def receive_intercept_prize(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        if not message.text or not message.text.strip():
            await message.answer("Введите приз текстом.")
            return
        prize = message.text.strip()
        if len(prize) > 500:
            await message.answer("Приз должен быть не длиннее 500 символов.")
            return
        await state.update_data(prize=prize)
        await state.set_state(InterceptSetup.duration)
        await message.answer(
            "Сколько лидер должен продержаться без перебива?\n"
            "Введите <code>120</code>, <code>2м</code> или <code>1ч</code> "
            "(от 10 секунд до 24 часов).",
            reply_markup=back_keyboard(),
        )

    async def receive_intercept_duration(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        seconds = parse_duration(message.text or "")
        if seconds is None:
            await message.answer("Введите время от 10 секунд до 24 часов, например 2м.")
            return
        await state.update_data(duration=seconds)
        await state.set_state(InterceptSetup.stars)
        await message.answer(
            "Сколько звёзд стоит одно сообщение?\n"
            "Введите целое число от <b>0 до 1 000 000</b>. "
            "Значение будет показано в посте.",
            reply_markup=back_keyboard(),
        )

    async def receive_intercept_stars(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        raw = (message.text or "").replace(" ", "").strip()
        if not raw.isdigit() or not 0 <= int(raw) <= 1_000_000:
            await message.answer("Введите целое число от 0 до 1 000 000.")
            return
        await state.update_data(stars=int(raw))
        await state.set_state(InterceptSetup.screenshot)
        await message.answer(
            "Прикрепите скриншот приза как фото или файл-изображение.\n"
            "Либо нажмите <i>«Без скриншота»</i>.",
            reply_markup=screenshot_keyboard("intercept"),
        )

    async def receive_intercept_screenshot(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        screenshot = self._screenshot_from(message)
        if screenshot is None:
            await message.answer("Отправьте фото/изображение или нажмите «Без скриншота».")
            return
        await state.update_data(
            screenshot_kind=screenshot[0], screenshot_file_id=screenshot[1]
        )
        await self._finish_intercept_setup(message, state)

    async def cb_skip_intercept_screenshot(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        if await state.get_state() != InterceptSetup.screenshot.state:
            await callback.answer("Настройка уже завершена", show_alert=True)
            return
        await callback.answer()
        if isinstance(callback.message, Message):
            await self._finish_intercept_setup(
                callback.message, state, callback.from_user.id
            )

    async def _finish_intercept_setup(
        self, message: Message, state: FSMContext, user_id: int | None = None
    ) -> None:
        operator_id = user_id or (message.from_user.id if message.from_user else 0)
        targets = await self._validate_targets(operator_id)
        if isinstance(targets, str):
            await message.answer(targets, reply_markup=home_keyboard())
            await state.clear()
            return
        group_id = targets
        if await self.manager.snapshot(game_key(group_id)) is not None:
            await message.answer(
                "В выбранном чате уже идёт игра. Сначала остановите её в панели.",
                reply_markup=home_keyboard(),
            )
            await state.clear()
            return
        data = await state.get_data()
        prize = str(data["prize"])
        duration = int(data["duration"])
        stars = int(data["stars"])
        text = self._intercept_start_text(prize, duration, stars)
        try:
            group_post = await self._send_public(
                group_id,
                self._tracking_text(text),
                data.get("screenshot_kind"),
                data.get("screenshot_file_id"),
            )
        except TelegramAPIError as exc:
            logger.exception("Could not publish intercept announcements")
            await message.answer(
                "Не удалось опубликовать стартовое сообщение в группе: "
                f"<code>{html.quote(str(exc))}</code>"
            )
            return
        await self.manager.start_intercept(
            game_key(group_id),
            duration,
            prize=prize,
            message_stars=stars,
            tracking_after_message_id=group_post.message_id,
        )
        await state.clear()
        await message.answer(
            "✅ <b>«Перебив» запущен.</b> Стартовое сообщение опубликовано в группе. "
            "Отслеживание началось после сообщения бота.",
            reply_markup=home_keyboard(),
        )

    async def cb_guess_setup(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        targets = await self._validate_targets(callback.from_user.id)
        if isinstance(targets, str):
            await callback.answer(targets, show_alert=True)
            return
        await state.clear()
        await state.set_state(GuessSetup.prize)
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "🔢 <b>Настройка «Угадай число»</b>\n\n"
                "Введите приз текстом или ссылкой.",
                reply_markup=back_keyboard(),
            )

    async def receive_guess_prize(self, message: Message, state: FSMContext) -> None:
        if not await self._require_access_message(message):
            return
        prize = (message.text or "").strip()
        if not prize or len(prize) > 500:
            await message.answer("Введите приз текстом, не длиннее 500 символов.")
            return
        await state.update_data(prize=prize)
        await state.set_state(GuessSetup.screenshot)
        await message.answer(
            "Прикрепите скриншот приза как фото или файл-изображение.\n"
            "Либо нажмите <i>«Без скриншота»</i>.",
            reply_markup=screenshot_keyboard("guess"),
        )

    async def receive_guess_screenshot(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        screenshot = self._screenshot_from(message)
        if screenshot is None:
            await message.answer("Отправьте фото/изображение или нажмите «Без скриншота».")
            return
        await state.update_data(
            screenshot_kind=screenshot[0], screenshot_file_id=screenshot[1]
        )
        await self._continue_guess_setup(message, state)

    async def cb_skip_guess_screenshot(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        if await state.get_state() != GuessSetup.screenshot.state:
            await callback.answer("Настройка уже завершена", show_alert=True)
            return
        await callback.answer()
        if isinstance(callback.message, Message):
            await self._continue_guess_setup(callback.message, state)

    async def _continue_guess_setup(
        self, message: Message, state: FSMContext
    ) -> None:
        await state.set_state(GuessSetup.secret_number)
        await message.answer(
            "Какое число должны угадать участники?\n"
            "Введите целое число от <b>1 до 100</b>.",
            reply_markup=back_keyboard(),
        )

    async def receive_guess_number(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        raw = (message.text or "").strip()
        if not raw.isdigit() or not 1 <= int(raw) <= 100:
            await message.answer("Введите целое число от 1 до 100.")
            return
        await state.update_data(secret_number=int(raw))
        await state.set_state(GuessSetup.stars)
        await message.answer(
            "Сколько звёзд стоит одна попытка?\n"
            "Введите целое число от <b>0 до 1 000 000</b>.",
            reply_markup=back_keyboard(),
        )

    async def receive_guess_stars(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        stars = parse_stars(message.text or "")
        if stars is None:
            await message.answer("Введите целое число от 0 до 1 000 000.")
            return
        await state.update_data(stars=stars)
        await self._finish_guess_setup(message, state)

    async def _finish_guess_setup(
        self, message: Message, state: FSMContext, user_id: int | None = None
    ) -> None:
        operator_id = user_id or (message.from_user.id if message.from_user else 0)
        targets = await self._validate_targets(operator_id)
        if isinstance(targets, str):
            await message.answer(targets, reply_markup=home_keyboard())
            await state.clear()
            return
        group_id = targets
        if await self.manager.snapshot(game_key(group_id)) is not None:
            await message.answer(
                "В выбранном чате уже идёт игра. Сначала остановите её в панели.",
                reply_markup=home_keyboard(),
            )
            await state.clear()
            return
        data = await state.get_data()
        stars = int(data["stars"])
        prize = str(data["prize"])
        secret_number = int(data["secret_number"])
        try:
            group_post = await self._send_public(
                group_id,
                self._tracking_text(self._guess_start_text(prize, stars)),
                data.get("screenshot_kind"),
                data.get("screenshot_file_id"),
            )
        except TelegramAPIError as exc:
            await message.answer(
                "Не удалось опубликовать игру: "
                f"<code>{html.quote(str(exc))}</code>"
            )
            return
        await self.manager.start_guess(
            game_key(group_id),
            secret_number,
            prize=prize,
            message_stars=stars,
            tracking_after_message_id=group_post.message_id,
        )
        await state.clear()
        await message.answer(
            "✅ <b>«Угадай число» запущена.</b>", reply_markup=home_keyboard()
        )

    async def cb_race_setup(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        targets = await self._validate_targets(callback.from_user.id)
        if isinstance(targets, str):
            await callback.answer(targets, show_alert=True)
            return
        await state.clear()
        await state.set_state(RaceSetup.prize)
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "🏎 <b>Настройка «Гонок»</b>\n\nВведите приз текстом или ссылкой.",
                reply_markup=back_keyboard(),
            )

    async def receive_race_prize(self, message: Message, state: FSMContext) -> None:
        if not await self._require_access_message(message):
            return
        prize = (message.text or "").strip()
        if not prize or len(prize) > 500:
            await message.answer("Введите приз текстом, не длиннее 500 символов.")
            return
        await state.update_data(prize=prize)
        await state.set_state(RaceSetup.stars)
        await message.answer(
            "Сколько звёзд стоит сообщение для участия?\n"
            "Введите целое число от <b>0 до 1 000 000</b>.",
            reply_markup=back_keyboard(),
        )

    async def receive_race_stars(self, message: Message, state: FSMContext) -> None:
        if not await self._require_access_message(message):
            return
        stars = parse_stars(message.text or "")
        if stars is None:
            await message.answer("Введите целое число от 0 до 1 000 000.")
            return
        await state.update_data(stars=stars)
        await state.set_state(RaceSetup.duration)
        await message.answer(
            "Сколько длится набор участников?\n"
            "Введите <code>60</code>, <code>2м</code> или <code>1ч</code>.",
            reply_markup=back_keyboard(),
        )

    async def receive_race_duration(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        duration = parse_duration(message.text or "")
        if duration is None:
            await message.answer("Введите время от 10 секунд до 24 часов.")
            return
        await state.update_data(duration=duration)
        await state.set_state(RaceSetup.screenshot)
        await message.answer(
            "Прикрепите скриншот приза или нажмите <i>«Без скриншота»</i>.",
            reply_markup=screenshot_keyboard("race"),
        )

    async def receive_race_screenshot(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        screenshot = self._screenshot_from(message)
        if screenshot is None:
            await message.answer("Отправьте фото/изображение или нажмите «Без скриншота».")
            return
        await state.update_data(
            screenshot_kind=screenshot[0], screenshot_file_id=screenshot[1]
        )
        await self._finish_race_setup(message, state)

    async def cb_skip_race_screenshot(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        if await state.get_state() != RaceSetup.screenshot.state:
            await callback.answer("Настройка уже завершена", show_alert=True)
            return
        await callback.answer()
        if isinstance(callback.message, Message):
            await self._finish_race_setup(callback.message, state, callback.from_user.id)

    async def _finish_race_setup(
        self, message: Message, state: FSMContext, user_id: int | None = None
    ) -> None:
        operator_id = user_id or (message.from_user.id if message.from_user else 0)
        targets = await self._validate_targets(operator_id)
        if isinstance(targets, str):
            await message.answer(targets, reply_markup=home_keyboard())
            await state.clear()
            return
        group_id = targets
        if await self.manager.snapshot(game_key(group_id)) is not None:
            await message.answer(
                "В выбранном чате уже идёт игра. Сначала остановите её в панели.",
                reply_markup=home_keyboard(),
            )
            await state.clear()
            return
        data = await state.get_data()
        stars = int(data["stars"])
        prize = str(data["prize"])
        duration = int(data["duration"])
        try:
            group_post = await self._send_public(
                group_id,
                self._tracking_text(self._race_start_text(prize, stars, duration)),
                data.get("screenshot_kind"),
                data.get("screenshot_file_id"),
            )
        except TelegramAPIError as exc:
            await message.answer(
                "Не удалось опубликовать гонку: "
                f"<code>{html.quote(str(exc))}</code>"
            )
            return
        await self.manager.start_race(
            game_key(group_id),
            duration,
            prize=prize,
            message_stars=stars,
            tracking_after_message_id=group_post.message_id,
        )
        await state.clear()
        await message.answer(
            "✅ <b>Набор на гонку начался.</b>", reply_markup=home_keyboard()
        )

    async def cb_arcade_setup(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        targets = await self._validate_targets(callback.from_user.id)
        if isinstance(targets, str):
            await callback.answer(targets, show_alert=True)
            return
        raw_kind = (callback.data or "").removeprefix("game:")
        try:
            kind = ContestType(raw_kind)
            title = ARCADE_TITLES[kind]
        except (ValueError, KeyError):
            await callback.answer("Неизвестная игра", show_alert=True)
            return
        await state.clear()
        await state.set_data({"arcade_kind": kind.value})
        await state.set_state(ArcadeSetup.prize)
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.answer(
                f"<b>Настройка игры «{title}»</b>\n\n"
                "Введите приз текстом или ссылкой.",
                reply_markup=back_keyboard(),
            )

    async def receive_arcade_prize(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        prize = (message.text or "").strip()
        if not prize or len(prize) > 500:
            await message.answer("Введите приз текстом, не длиннее 500 символов.")
            return
        await state.update_data(prize=prize)
        await state.set_state(ArcadeSetup.stars)
        await message.answer(
            "Сколько звёзд стоит сообщение для участия или одна попытка?\n"
            "Введите целое число от <b>0 до 1 000 000</b>.",
            reply_markup=back_keyboard(),
        )

    async def receive_arcade_stars(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        stars = parse_stars(message.text or "")
        if stars is None:
            await message.answer("Введите целое число от 0 до 1 000 000.")
            return
        await state.update_data(stars=stars)
        await state.set_state(ArcadeSetup.duration)
        await message.answer(
            "Сколько длится набор участников / приём попыток?\n"
            "Введите <code>60</code>, <code>2м</code> или <code>1ч</code>.",
            reply_markup=back_keyboard(),
        )

    async def receive_arcade_duration(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        duration = parse_duration(message.text or "")
        if duration is None:
            await message.answer("Введите время от 10 секунд до 24 часов.")
            return
        await state.update_data(duration=duration)
        await state.set_state(ArcadeSetup.screenshot)
        await message.answer(
            "Прикрепите скриншот приза или нажмите <i>«Без скриншота»</i>.",
            reply_markup=screenshot_keyboard("arcade"),
        )

    async def receive_arcade_screenshot(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        screenshot = self._screenshot_from(message)
        if screenshot is None:
            await message.answer("Отправьте фото/изображение или нажмите «Без скриншота».")
            return
        await state.update_data(
            screenshot_kind=screenshot[0], screenshot_file_id=screenshot[1]
        )
        await self._finish_arcade_setup(message, state)

    async def cb_skip_arcade_screenshot(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        if await state.get_state() != ArcadeSetup.screenshot.state:
            await callback.answer("Настройка уже завершена", show_alert=True)
            return
        await callback.answer()
        if isinstance(callback.message, Message):
            await self._finish_arcade_setup(
                callback.message, state, callback.from_user.id
            )

    async def _finish_arcade_setup(
        self, message: Message, state: FSMContext, user_id: int | None = None
    ) -> None:
        operator_id = user_id or (message.from_user.id if message.from_user else 0)
        targets = await self._validate_targets(operator_id)
        if isinstance(targets, str):
            await message.answer(targets, reply_markup=home_keyboard())
            await state.clear()
            return
        group_id = targets
        if await self.manager.snapshot(game_key(group_id)) is not None:
            await message.answer(
                "В выбранном чате уже идёт игра. Сначала остановите её в панели.",
                reply_markup=home_keyboard(),
            )
            await state.clear()
            return
        data = await state.get_data()
        try:
            kind = ContestType(str(data["arcade_kind"]))
            title = ARCADE_TITLES[kind]
        except (KeyError, ValueError):
            await message.answer("Настройка игры потеряна. Начните заново.")
            await state.clear()
            return
        prize = str(data["prize"])
        stars = int(data["stars"])
        duration = int(data["duration"])
        try:
            group_post = await self._send_public(
                group_id,
                self._tracking_text(
                    self._arcade_start_text(kind, prize, stars, duration)
                ),
                data.get("screenshot_kind"),
                data.get("screenshot_file_id"),
            )
        except TelegramAPIError as exc:
            await message.answer(
                f"Не удалось опубликовать игру: <code>{html.quote(str(exc))}</code>"
            )
            return
        await self.manager.start_arcade(
            game_key(group_id),
            kind,
            duration,
            prize=prize,
            message_stars=stars,
            tracking_after_message_id=group_post.message_id,
        )
        await state.clear()
        await message.answer(
            f"✅ <b>Игра «{title}» запущена.</b>", reply_markup=home_keyboard()
        )

    async def cb_football_setup(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        targets = await self._validate_targets(callback.from_user.id)
        if isinstance(targets, str):
            await callback.answer(targets, show_alert=True)
            return
        await state.clear()
        await state.set_state(FootballSetup.team_a)
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "⚽ <b>Настройка автоматического футбола</b>\n\n"
                "Введите название синей команды.",
                reply_markup=back_keyboard(),
            )

    async def receive_football_team_a(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        name = (message.text or "").strip()
        if not name or len(name) > 40:
            await message.answer("Название должно содержать от 1 до 40 символов.")
            return
        await state.update_data(team_a=name)
        await state.set_state(FootballSetup.team_b)
        await message.answer("Введите название красной команды.", reply_markup=back_keyboard())

    async def receive_football_team_b(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        name = (message.text or "").strip()
        data = await state.get_data()
        if not name or len(name) > 40:
            await message.answer("Название должно содержать от 1 до 40 символов.")
            return
        if name.casefold() == str(data.get("team_a", "")).casefold():
            await message.answer("Названия команд должны отличаться.")
            return
        await state.update_data(team_b=name)
        await state.set_state(FootballSetup.stars)
        await message.answer(
            "Введите стоимость платного сообщения для входа в команду в Stars. "
            "Бот проверит фактически уплаченную сумму.",
            reply_markup=back_keyboard(),
        )

    async def receive_football_stars(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        stars = parse_stars(message.text or "")
        if stars is None or stars == 0:
            await message.answer("Введите целое число от 1 до 1 000 000.")
            return
        await state.update_data(stars=stars)
        await state.set_state(FootballSetup.duration)
        await message.answer(
            "Сколько времени открыт набор в команды?\n"
            "Введите <code>60</code>, <code>2м</code> или <code>1ч</code>.",
            reply_markup=back_keyboard(),
        )

    async def receive_football_duration(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        duration = parse_duration(message.text or "")
        if duration is None:
            await message.answer("Введите время от 10 секунд до 24 часов.")
            return
        await state.update_data(duration=duration)
        await state.set_state(FootballSetup.screenshot)
        await message.answer(
            "Прикрепите изображение матча или нажмите <i>«Без скриншота»</i>.",
            reply_markup=screenshot_keyboard("football"),
        )

    async def receive_football_screenshot(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        screenshot = self._screenshot_from(message)
        if screenshot is None:
            await message.answer("Отправьте фото/изображение или нажмите «Без скриншота».")
            return
        await state.update_data(
            screenshot_kind=screenshot[0], screenshot_file_id=screenshot[1]
        )
        await self._finish_football_setup(message, state)

    async def cb_skip_football_screenshot(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        if await state.get_state() != FootballSetup.screenshot.state:
            await callback.answer("Настройка уже завершена", show_alert=True)
            return
        await callback.answer()
        if isinstance(callback.message, Message):
            await self._finish_football_setup(
                callback.message, state, callback.from_user.id
            )

    async def _finish_football_setup(
        self, message: Message, state: FSMContext, user_id: int | None = None
    ) -> None:
        operator_id = user_id or (message.from_user.id if message.from_user else 0)
        targets = await self._validate_targets(operator_id)
        if isinstance(targets, str):
            await message.answer(targets, reply_markup=home_keyboard())
            await state.clear()
            return
        group_id = targets
        if await self.manager.snapshot(game_key(group_id)) is not None:
            await message.answer(
                "В выбранном чате уже идёт игра. Сначала остановите её в панели.",
                reply_markup=home_keyboard(),
            )
            await state.clear()
            return
        data = await state.get_data()
        team_a = str(data["team_a"])
        team_b = str(data["team_b"])
        stars = int(data["stars"])
        duration = int(data["duration"])
        try:
            group_post = await self._send_public(
                group_id,
                self._football_start_text(team_a, team_b, stars, duration),
                data.get("screenshot_kind"),
                data.get("screenshot_file_id"),
                reply_markup=football_pick_keyboard(team_a, team_b, set()),
            )
        except TelegramAPIError as exc:
            await message.answer(
                f"Не удалось опубликовать матч: <code>{html.quote(str(exc))}</code>"
            )
            return
        await self.manager.start_football(
            game_key(group_id),
            team_a,
            team_b,
            duration,
            message_stars=stars,
            tracking_after_message_id=group_post.message_id,
        )
        await state.clear()
        await message.answer(
            "✅ <b>Набор в футбольные команды начался.</b>",
            reply_markup=home_keyboard(),
        )

    async def cb_football_info(self, callback: CallbackQuery) -> None:
        await callback.answer(
            "Напишите точное название команды в чат, затем выберите свободного игрока.",
            show_alert=True,
        )

    async def cb_football_player(self, callback: CallbackQuery) -> None:
        if not callback.data or not isinstance(callback.message, Message):
            return
        if callback.message.chat.type not in GROUP_TYPES:
            await callback.answer("Выбор доступен только в группе", show_alert=True)
            return
        try:
            player_index = int(callback.data.rsplit(":", 1)[-1])
            player_name = FOOTBALLERS[player_index][0]
        except (ValueError, IndexError):
            await callback.answer("Неизвестный футболист", show_alert=True)
            return
        key = game_key(callback.message.chat.id)
        state = await self.manager.snapshot(key)
        if (
            state is None
            or state.kind is not ContestType.FOOTBALL
            or state.tracking_after_message_id != callback.message.message_id
        ):
            await callback.answer("Выбор игроков уже завершён", show_alert=True)
            return
        update = await self.manager.submit_football_player(
            key, participant_from(callback.from_user), player_index
        )
        if update is None:
            await callback.answer("Выбор игроков уже завершён", show_alert=True)
            return
        if update.status is FootballPlayerStatus.NOT_JOINED:
            await callback.answer(
                "Сначала оплатите сообщение и напишите точное название команды.",
                show_alert=True,
            )
            return
        if update.status is FootballPlayerStatus.WRONG_TEAM:
            await callback.answer(
                "Этот футболист играет за другую команду.", show_alert=True
            )
            return
        if update.status is FootballPlayerStatus.ALREADY_SELECTED:
            selected_name = FOOTBALLERS[update.player_index][0]
            await callback.answer(
                f"Вы уже выбрали {selected_name}. Поменять футболиста нельзя.",
                show_alert=True,
            )
            return
        if update.status is FootballPlayerStatus.OCCUPIED:
            await callback.answer("Этот футболист уже занят.", show_alert=True)
            return

        latest = await self.manager.snapshot(key)
        if latest is not None:
            await self._refresh_football_post(key, latest)
        await callback.answer(f"Ваш футболист: {player_name}. Выбор зафиксирован.")

    async def cb_cases(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not await self._require_access_callback(callback):
            return
        targets = await self._validate_targets(callback.from_user.id)
        if isinstance(targets, str):
            await callback.answer(targets, show_alert=True)
            return
        await state.clear()
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                "📦 <b>Кейсы</b>\n\n"
                "Создайте новый кейс или запустите сохранённый.",
                reply_markup=cases_keyboard(),
            )

    async def cb_create_case(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        targets = await self._validate_targets(callback.from_user.id)
        if isinstance(targets, str):
            await callback.answer(targets, show_alert=True)
            return
        await state.clear()
        await state.set_data({"drop_ids": []})
        await state.set_state(CaseSetup.drop_name)
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "📦 <b>Новый кейс</b>\n\n"
                "Введите название первого дропа, например <code>🧸 Мишка</code>.",
                reply_markup=case_drop_keyboard(),
            )

    async def cb_add_case_drop(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        data = await state.get_data()
        if "drop_ids" not in data:
            await callback.answer("Сначала создайте кейс", show_alert=True)
            return
        if len(data.get("drop_ids", [])) >= 20:
            await callback.answer("В одном кейсе максимум 20 дропов", show_alert=True)
            return
        await state.set_state(CaseSetup.drop_name)
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "Введите название следующего дропа.",
                reply_markup=case_drop_keyboard(bool(data.get("drop_ids"))),
            )

    async def receive_case_drop_name(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        name = (message.text or "").strip()
        if not name or len(name) > 80:
            await message.answer("Название дропа должно содержать от 1 до 80 символов.")
            return
        await state.update_data(pending_drop_name=name)
        await state.set_state(CaseSetup.drop_chance)
        await message.answer(
            "Введите честный шанс выпадения этого дропа в процентах.\n"
            "Например: <code>0.05</code>. Этот же процент будет показан в посте.",
            reply_markup=back_keyboard(),
        )

    async def receive_case_drop_chance(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        chance = parse_chance(message.text or "")
        if chance is None:
            await message.answer("Введите процент числом от 0 до 100, например 0.05.")
            return
        data = await state.get_data()
        drop_ids = [int(value) for value in data.get("drop_ids", [])]
        existing = [
            drop
            for drop_id in drop_ids
            if (drop := self.storage.get_drop(message.from_user.id, drop_id))
            is not None
        ]
        if sum(drop.chance for drop in existing) + chance > 100.0000001:
            await message.answer("Сумма шансов всех дропов не может превышать 100%.")
            return
        saved = self.storage.save_drop(
            message.from_user.id, str(data["pending_drop_name"]), chance
        )
        drop_ids.append(saved.drop_id)
        await state.update_data(drop_ids=drop_ids, pending_drop_name=None)
        await state.set_state(CaseSetup.drops_ready)
        await message.answer(
            f"✅ Дроп <b>{html.quote(saved.name)}</b> сохранён отдельно.\n"
            f"Шанс: <b>{format_chance(saved.chance)}%</b>.\n\n"
            "Добавьте ещё один дроп или переходите к настройке кейса.",
            reply_markup=case_drop_keyboard(True),
        )

    async def cb_reuse_drop(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        data = await state.get_data()
        if "drop_ids" not in data:
            await callback.answer("Сначала создайте кейс", show_alert=True)
            return
        drops = self.storage.list_drops(callback.from_user.id, 25)
        if not drops:
            await callback.answer("Сохранённых дропов пока нет", show_alert=True)
            return
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "📚 <b>Выберите сохранённый дроп:</b>",
                reply_markup=saved_drops_keyboard(drops),
            )

    async def cb_pick_saved_drop(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        data = await state.get_data()
        if "drop_ids" not in data or not callback.data:
            await callback.answer("Сначала создайте кейс", show_alert=True)
            return
        try:
            drop_id = int(callback.data.rsplit(":", 1)[1])
        except ValueError:
            await callback.answer("Некорректный дроп", show_alert=True)
            return
        saved = self.storage.get_drop(callback.from_user.id, drop_id)
        if saved is None:
            await callback.answer("Дроп не найден", show_alert=True)
            return
        drop_ids = [int(value) for value in data.get("drop_ids", [])]
        if len(drop_ids) >= 20:
            await callback.answer("В одном кейсе максимум 20 дропов", show_alert=True)
            return
        current = [
            drop
            for current_id in drop_ids
            if (drop := self.storage.get_drop(callback.from_user.id, current_id))
            is not None
        ]
        if sum(drop.chance for drop in current) + saved.chance > 100.0000001:
            await callback.answer("Сумма шансов превысит 100%", show_alert=True)
            return
        drop_ids.append(saved.drop_id)
        await state.update_data(drop_ids=drop_ids)
        await state.set_state(CaseSetup.drops_ready)
        await callback.answer("Дроп добавлен")
        if isinstance(callback.message, Message):
            await callback.message.answer(
                f"✅ <b>{html.quote(saved.name)}</b> добавлен в кейс.",
                reply_markup=case_drop_keyboard(True),
            )

    async def cb_case_drops_ready(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        data = await state.get_data()
        drop_ids = [int(value) for value in data.get("drop_ids", [])]
        if not drop_ids:
            await callback.answer("Добавьте хотя бы один дроп", show_alert=True)
            return
        await state.set_state(CaseSetup.name)
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "Введите название кейса.", reply_markup=back_keyboard()
            )

    async def receive_case_name(self, message: Message, state: FSMContext) -> None:
        if not await self._require_access_message(message):
            return
        name = (message.text or "").strip()
        if not name or len(name) > 80:
            await message.answer("Название кейса должно содержать от 1 до 80 символов.")
            return
        await state.update_data(case_name=name)
        await state.set_state(CaseSetup.stars)
        await message.answer(
            "Сколько звёзд стоит одно открытие?\n"
            "Введите целое число от <b>0 до 1 000 000</b>.",
            reply_markup=back_keyboard(),
        )

    async def receive_case_stars(self, message: Message, state: FSMContext) -> None:
        if not await self._require_access_message(message):
            return
        stars = parse_stars(message.text or "")
        if stars is None:
            await message.answer("Введите целое число от 0 до 1 000 000.")
            return
        await state.update_data(stars=stars)
        await state.set_state(CaseSetup.duration)
        await message.answer(
            "Через сколько кейс закроется?\n"
            "Введите <code>60</code>, <code>10м</code> или <code>1ч</code>.",
            reply_markup=back_keyboard(),
        )

    async def receive_case_duration(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        duration = parse_duration(message.text or "")
        if duration is None:
            await message.answer("Введите время от 10 секунд до 24 часов.")
            return
        await state.update_data(duration=duration)
        await state.set_state(CaseSetup.screenshot)
        await message.answer(
            "Прикрепите скриншот кейса или нажмите <i>«Без скриншота»</i>.",
            reply_markup=screenshot_keyboard("case"),
        )

    async def receive_case_screenshot(
        self, message: Message, state: FSMContext
    ) -> None:
        if not await self._require_access_message(message):
            return
        screenshot = self._screenshot_from(message)
        if screenshot is None:
            await message.answer("Отправьте фото/изображение или нажмите «Без скриншота».")
            return
        await state.update_data(
            screenshot_kind=screenshot[0], screenshot_file_id=screenshot[1]
        )
        await self._finish_case_setup(message, state)

    async def cb_skip_case_screenshot(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        if await state.get_state() != CaseSetup.screenshot.state:
            await callback.answer("Настройка уже завершена", show_alert=True)
            return
        await callback.answer()
        if isinstance(callback.message, Message):
            await self._finish_case_setup(callback.message, state, callback.from_user.id)

    async def _finish_case_setup(
        self, message: Message, state: FSMContext, user_id: int | None = None
    ) -> None:
        operator_id = user_id or (message.from_user.id if message.from_user else 0)
        data = await state.get_data()
        saved_case = self.storage.save_case(
            operator_id,
            str(data["case_name"]),
            int(data["stars"]),
            int(data["duration"]),
            [int(value) for value in data["drop_ids"]],
            screenshot_kind=data.get("screenshot_kind"),
            screenshot_file_id=data.get("screenshot_file_id"),
        )
        await state.clear()
        await self._launch_saved_case(message, operator_id, saved_case)

    async def cb_saved_cases(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        await state.clear()
        cases = self.storage.list_cases(callback.from_user.id)
        await callback.answer()
        if isinstance(callback.message, Message):
            text = "💾 <b>Сохранённые кейсы</b>\n\nВыберите кейс для запуска."
            if not cases:
                text = "💾 Сохранённых кейсов пока нет."
            await callback.message.edit_text(
                text, reply_markup=saved_cases_keyboard(cases)
            )

    async def cb_saved_drops(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        await state.clear()
        drops = self.storage.list_drops(callback.from_user.id, 30)
        if drops:
            lines = [
                f"{index}. {html.quote(drop.name)} — <b>{format_chance(drop.chance)}%</b>"
                for index, drop in enumerate(drops, 1)
            ]
            text = "📚 <b>Сохранённые дропы</b>\n\n" + "\n".join(lines)
        else:
            text = "📚 Сохранённых дропов пока нет."
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.edit_text(text, reply_markup=cases_keyboard())

    async def cb_start_saved_case(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        if not callback.data or not isinstance(callback.message, Message):
            return
        try:
            case_id = int(callback.data.rsplit(":", 1)[1])
        except ValueError:
            await callback.answer("Некорректный кейс", show_alert=True)
            return
        saved_case = self.storage.get_case(callback.from_user.id, case_id)
        if saved_case is None:
            await callback.answer("Кейс не найден", show_alert=True)
            return
        await state.clear()
        await callback.answer()
        await self._launch_saved_case(
            callback.message, callback.from_user.id, saved_case
        )

    async def _launch_saved_case(
        self, message: Message, operator_id: int, saved_case: SavedCase
    ) -> None:
        targets = await self._validate_targets(operator_id)
        if isinstance(targets, str):
            await message.answer(targets, reply_markup=home_keyboard())
            return
        group_id = targets
        if await self.manager.snapshot(game_key(group_id)) is not None:
            await message.answer(
                "В выбранном чате уже идёт игра. Сначала остановите её в панели.",
                reply_markup=home_keyboard(),
            )
            return
        try:
            group_post = await self._send_public(
                group_id,
                self._tracking_text(self._case_start_text(saved_case)),
                saved_case.screenshot_kind,
                saved_case.screenshot_file_id,
            )
        except TelegramAPIError as exc:
            await message.answer(
                "Не удалось опубликовать кейс: "
                f"<code>{html.quote(str(exc))}</code>"
            )
            return
        drops = tuple(DropOutcome(drop.name, drop.chance) for drop in saved_case.drops)
        await self.manager.start_case(
            game_key(group_id),
            saved_case.name,
            drops,
            saved_case.duration_seconds,
            message_stars=saved_case.stars,
            tracking_after_message_id=group_post.message_id,
        )
        await message.answer(
            "✅ <b>Кейс запущен и сохранён.</b>", reply_markup=home_keyboard()
        )

    async def cb_toggle_activity(self, callback: CallbackQuery) -> None:
        if not await self._require_access_callback(callback):
            return
        targets = await self._validate_targets(callback.from_user.id)
        if isinstance(targets, str):
            await callback.answer(targets, show_alert=True)
            return
        group_id = targets
        enabled = not self.storage.is_activity_enabled(group_id)
        if enabled:
            try:
                await self._send_public(group_id, self._activity_start_text())
            except TelegramAPIError as exc:
                await callback.answer(
                    f"Не удалось включить: {str(exc)[:120]}", show_alert=True
                )
                return
        self.storage.set_activity_enabled(group_id, enabled, callback.from_user.id)
        await callback.answer("Настройка сохранена")
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                self._panel_text(callback.from_user.id),
                reply_markup=self._panel_keyboard(callback.from_user.id),
            )

    async def cb_cancel_setup(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        await state.clear()
        await callback.answer("Настройка отменена")
        if isinstance(callback.message, Message):
            await callback.message.answer(
                self._panel_text(callback.from_user.id),
                reply_markup=self._panel_keyboard(callback.from_user.id),
            )

    async def cb_status(self, callback: CallbackQuery) -> None:
        if not await self._require_access_callback(callback):
            return
        targets = self.storage.get_targets(callback.from_user.id)
        if targets.group_id is None:
            await callback.answer("Сначала выберите чат", show_alert=True)
            return
        state = await self.manager.snapshot(game_key(targets.group_id))
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.answer(self._status_text(state))

    async def cb_stop(self, callback: CallbackQuery) -> None:
        if not await self._require_access_callback(callback):
            return
        targets = self.storage.get_targets(callback.from_user.id)
        if targets.group_id is None:
            await callback.answer("Сначала выберите чат", show_alert=True)
            return
        if not await self._is_user_admin(targets.group_id, callback.from_user.id):
            await callback.answer("Вы больше не администратор чата", show_alert=True)
            return
        stopped = await self.manager.stop(game_key(targets.group_id))
        await callback.answer("Игра остановлена" if stopped else "Активной игры нет")
        if stopped and isinstance(callback.message, Message):
            await callback.message.answer("⛔ Игра остановлена без победителя.")

    async def on_message(self, message: Message) -> None:
        await self._remember_chat(message.chat)
        if message.chat.type not in GROUP_TYPES:
            if (
                message.chat.type == ChatType.PRIVATE
                and message.from_user
                and not self.storage.is_authorized(message.from_user.id)
            ):
                await self._require_access_message(message)
            return
        if (
            message.from_user is None
            or message.from_user.is_bot
            or message.from_user.id == self.bot.id
            or getattr(message, "sender_chat", None) is not None
            or bool(getattr(message, "is_automatic_forward", False))
            or (message.text and message.text.startswith("/"))
        ):
            return

        participant = participant_from(message.from_user)
        if self.storage.is_activity_enabled(message.chat.id):
            await self._process_activity_message(message, participant)

        key = game_key(message.chat.id)
        state = await self.manager.snapshot(key)
        if state is None:
            return
        if (
            state.tracking_after_message_id is not None
            and message.message_id <= state.tracking_after_message_id
        ):
            return

        if state.kind is ContestType.FOOTBALL:
            raw_team = (message.text or "").strip().casefold()
            team = None
            if raw_team == state.team_a_name.casefold():
                team = "a"
            elif raw_team == state.team_b_name.casefold():
                team = "b"
            if team is None:
                return

            paid_stars = int(getattr(message, "paid_star_count", 0) or 0)
            if state.message_stars > 0 and paid_stars < state.message_stars:
                await self._reply_with_fallback(
                    message,
                    f"{premium(FOOTBALL_BALL_ID, '⚽')} Вход не засчитан: "
                    f"нужно оплатить <b>{state.message_stars} Stars</b> за сообщение. "
                    f"Оплачено: <b>{paid_stars}</b>.",
                )
                return

            update = await self.manager.submit_football_team(
                key, participant, team
            )
            if update is None:
                return
            team_name = (
                state.team_a_name if update.team == "a" else state.team_b_name
            )
            if update.status is FootballJoinStatus.TEAM_FULL:
                await self._reply_with_fallback(
                    message,
                    f"Команда <b>{html.quote(team_name)}</b> уже заполнена — 5/5.",
                )
                return
            if update.status is FootballJoinStatus.ALREADY_JOINED:
                await self._reply_with_fallback(
                    message,
                    f"Вы уже состоите в команде <b>{html.quote(team_name)}</b>. "
                    "Теперь выберите свободного футболиста под стартовым постом.",
                )
                return

            latest = await self.manager.snapshot(key)
            if latest is not None:
                await self._refresh_football_post(key, latest)
            await self._reply_with_fallback(
                message,
                f"{premium(FOOTBALL_BALL_ID, '⚽')} "
                f"{participant_link(participant)}, вы в команде "
                f"<b>{html.quote(team_name)}</b> — "
                f"<b>{update.counts[update.team]}/5</b>. "
                "Выберите одного свободного футболиста под стартовым постом; "
                "поменять выбор нельзя.",
            )
            return

        if state.kind is ContestType.INTERCEPT:
            update = await self.manager.submit_intercept(key, participant)
            if update and update.accepted:
                if update.first_leader:
                    text = (
                        f"{premium(INTERCEPT_UPDATE_IDS[1], '👤')} "
                        f"<b>Лидер:</b> {participant_link(participant)}\n"
                        f"<b>До конца:</b> {format_duration(update.remaining_seconds)}"
                    )
                else:
                    text = (
                        f"{premium(INTERCEPT_UPDATE_IDS[0], '🧢')} <b>Перебито!</b>\n"
                        f"{premium(INTERCEPT_UPDATE_IDS[1], '👤')} "
                        f"<b>Новый лидер:</b> {participant_link(participant)}\n"
                        f"<b>До конца:</b> {format_duration(update.remaining_seconds)}"
                    )
                await self._reply_with_fallback(message, text)
            return

        if (
            state.kind is ContestType.CASINO
            and message.dice is not None
            and message.dice.emoji == "🎰"
        ):
            reservation = await self.manager.reserve_spin(
                # Each incoming native dice is already a separate Telegram
                # message, so it must never be silently discarded by cooldown.
                key,
                message.from_user.id,
                0,
            )
            if reservation.status is not SpinStatus.ACCEPTED:
                return
            assert reservation.game_id is not None
            result = await self.manager.resolve_spin(
                key,
                reservation.game_id,
                participant,
                message.dice.value,
            )
            if result is None or not result.jackpot:
                return
            if result.winner and result.finished_state:
                await message.reply(
                    "🎰 <b>777! Победа засчитана.</b> "
                    f"Победитель: {participant_link(result.winner)}"
                )
                await asyncio.sleep(3.2)
                await self._announce_winner(
                    key, result.winner, result.finished_state
                )
            else:
                await message.reply(
                    "🎰 <b>777 засчитано!</b> "
                    f"{participant_link(participant)} — "
                    f"<b>{result.hits}/{result.target}</b>"
                )
            return

        if state.kind is ContestType.GUESS:
            raw = (message.text or "").strip()
            if not raw.isdigit() or not 1 <= int(raw) <= 100:
                return
            result = await self.manager.submit_guess(key, participant, int(raw))
            if result is not None:
                winner, finished_state = result
                await message.reply(
                    f"🔢 <b>Число {finished_state.secret_number} угадано!</b>"
                )
                await self._announce_winner(key, winner, finished_state)
            return

        if state.kind is ContestType.RACE:
            update = await self.manager.submit_race(key, participant)
            if update and update.accepted:
                await message.reply(
                    f"🏎 {participant_link(participant)}, вы в гонке! "
                    f"Участников: <b>{update.participant_count}</b>."
                )
            return

        if state.kind in {
            ContestType.AIRPLANE,
            ContestType.SNAKE,
            ContestType.PICKAXE,
        }:
            update = await self.manager.submit_arcade_join(key, participant)
            if update and update.accepted:
                icon = {
                    ContestType.AIRPLANE: premium(AIRPLANE_IDS[0], "✈️"),
                    ContestType.SNAKE: premium(SNAKE_IDS[1], "🍎"),
                    ContestType.PICKAXE: premium(PICKAXE_IDS[0], "⛏️"),
                }[state.kind]
                limits = {
                    ContestType.AIRPLANE: "30",
                    ContestType.SNAKE: "8",
                    ContestType.PICKAXE: "5",
                }
                await self._reply_with_fallback(
                    message,
                    f"{icon} {participant_link(participant)}, участие принято! "
                    f"Участников: <b>{update.participant_count}/{limits[state.kind]}</b>.",
                )
                if update.collection_ready:
                    finished = await self.manager.finish_collection_now(key)
                    if finished is not None:
                        await self._finish_timed_game(key, finished)
            return

        if state.kind is ContestType.PARKOUR:
            attempt = await self.manager.submit_parkour(
                key, participant, message.message_id
            )
            if attempt is None:
                return
            await self._reply_with_fallback(
                message, self._parkour_attempt_text(participant, attempt)
            )
            if attempt.winner is not None and attempt.finished_state is not None:
                await self._announce_winner(
                    key, attempt.winner, attempt.finished_state
                )
            return

        if state.kind is ContestType.CASE:
            opened = await self.manager.open_case(key, message.message_id)
            if opened is None:
                return
            if opened.outcome is None:
                await message.reply(
                    f"📦 <b>{html.quote(opened.state.case_name)}</b> открыт. "
                    "В этот раз кейс пуст."
                )
            else:
                await message.reply(
                    f"📦 <b>{html.quote(opened.state.case_name)}</b> открыт!\n"
                    f"🎁 {participant_link(participant)} получает "
                    f"<b>{html.quote(opened.outcome.name)}</b>.\n\n"
                    f"<b>{html.quote(self.settings.prize_call)}</b>"
                )
            return

    async def _process_activity_message(
        self, message: Message, participant: Participant
    ) -> None:
        drop = select_drop(ACTIVITY_DROPS, self._random.random() * 100)
        if drop is None:
            return
        text = (
            "🎁 <b>Подарок за актив!</b>\n\n"
            f"{participant_link(participant)} получает "
            f"<b>{html.quote(drop.name)}</b>.\n\n"
            f"<b>{html.quote(self.settings.prize_call)}</b>"
        )
        await self._reply_with_fallback(message, text)

    async def _reply_with_fallback(self, message: Message, text: str) -> None:
        try:
            await message.reply(text)
        except TelegramBadRequest:
            await message.reply(without_premium(text))

    async def _send_public(
        self,
        chat_id: int,
        text: str,
        screenshot_kind: object = None,
        screenshot_file_id: object = None,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        media_kind = screenshot_kind
        if (
            len(text) > 900
            and isinstance(screenshot_file_id, str)
            and media_kind in {"photo", "document"}
        ):
            if media_kind == "photo":
                await self.bot.send_photo(chat_id, screenshot_file_id)
            else:
                await self.bot.send_document(chat_id, screenshot_file_id)
            media_kind = None

        async def send(
            value: str, markup: InlineKeyboardMarkup | None
        ) -> Message:
            if media_kind == "photo" and isinstance(screenshot_file_id, str):
                return await self.bot.send_photo(
                    chat_id,
                    screenshot_file_id,
                    caption=value,
                    reply_markup=markup,
                )
            if media_kind == "document" and isinstance(screenshot_file_id, str):
                return await self.bot.send_document(
                    chat_id,
                    screenshot_file_id,
                    caption=value,
                    reply_markup=markup,
                )
            if markup is not None:
                return await self.bot.send_message(
                    chat_id, value, reply_markup=markup
                )
            return await self.bot.send_message(chat_id, value)

        try:
            return await send(text, reply_markup)
        except TelegramBadRequest:
            fallback_text = without_premium(text)
            fallback_markup = without_premium_markup(reply_markup)
            if fallback_text == text and fallback_markup == reply_markup:
                raise
            return await send(fallback_text, fallback_markup)

    def _casino_start_text(self, prize: str, target: int) -> str:
        seven = premium(CASINO_COMBINATION_ID, "7️⃣")
        return (
            f"{premium(CASINO_START_IDS[0], '🎰')} <b>Казино началось!</b>\n\n"
            f"{premium(CASINO_START_IDS[1], '🎁')} <b>Приз:</b> "
            f"<i>{format_prize(prize)}</i>\n"
            "<b>Тип:</b> <i>🎰 Слоты</i>\n"
            f"{premium(CASINO_START_IDS[2], '🎰')} <b>Кол-во 🎰:</b> {target}\n"
            f"<b>Комбинация:</b> {seven}{seven}{seven}\n\n"
            "<i>Кидай слоты в этой группе, чтобы выиграть!</i>"
        )

    @staticmethod
    def _tracking_text(text: str) -> str:
        return (
            f"{text}\n\n"
            "<b>⬇️ Отслеживание участников начинается после этого сообщения.</b>"
        )

    def _intercept_start_text(self, prize: str, seconds: int, stars: int) -> str:
        return (
            f"{premium(INTERCEPT_START_IDS[0], '⚠️')} <b>Ивент начался!</b>\n\n"
            f"{premium(INTERCEPT_START_IDS[1], '⭐️')} "
            f"<b>1 сообщение в чате =</b> <i>{stars} звёзд.</i>\n"
            f"{premium(INTERCEPT_START_IDS[2], '⏰')} <b>Цель:</b> "
            f"продержаться <b>{format_duration(seconds)}</b> без перебива.\n\n"
            f"{premium(INTERCEPT_START_IDS[3], '🐵')} <b>Приз:</b> "
            f"<i>{format_prize(prize)}</i>"
        )

    @staticmethod
    def _price_text(stars: int) -> str:
        return "бесплатно" if stars == 0 else f"{stars} ⭐️"

    def _guess_start_text(self, prize: str, stars: int) -> str:
        return (
            "🔢 <b>Игра «Угадай число» началась!</b>\n\n"
            f"🎁 <b>Приз:</b> <i>{format_prize(prize)}</i>\n"
            "🎯 <b>Диапазон:</b> от 1 до 100\n"
            f"⭐️ <b>Одна попытка:</b> {self._price_text(stars)}\n\n"
            "Отправляйте число отдельным сообщением. Первый правильный ответ победит."
        )

    def _race_start_text(self, prize: str, stars: int, seconds: int) -> str:
        return (
            "🏎 <b>Набор участников на гонку!</b>\n\n"
            f"🎁 <b>Приз:</b> <i>{format_prize(prize)}</i>\n"
            f"⭐️ <b>Сообщение для участия:</b> {self._price_text(stars)}\n"
            f"⏰ <b>Набор длится:</b> {format_duration(seconds)}\n\n"
            "Отправьте одно сообщение после этого поста. Каждый пользователь "
            "попадает в гонку один раз. После набора машины стартуют автоматически."
        )

    def _arcade_start_text(
        self, kind: ContestType, prize: str, stars: int, seconds: int
    ) -> str:
        gift = premium(WINNER_IDS[1], "🎁")
        clock = premium(INTERCEPT_START_IDS[2], "⏰")
        price = premium(INTERCEPT_START_IDS[1], "⭐️")
        common = (
            f"{gift} <b>Приз:</b> <i>{format_prize(prize)}</i>\n"
            f"{price} <b>Сообщение:</b> {self._price_text(stars)}\n"
            f"{clock} <b>Приём участников:</b> {format_duration(seconds)}\n\n"
        )
        if kind is ContestType.AIRPLANE:
            plane = premium(AIRPLANE_IDS[0], "✈️")
            bird = premium(AIRPLANE_IDS[1], "🕊")
            return (
                f"{plane} <b>Игра «Самолётик» началась!</b>\n\n"
                + common
                + f"Отправьте одно сообщение, чтобы запустить самолёт. {bird} Птица "
                "появляется с шансом <b>20%</b>, сбивает скорость и тянет самолёт вниз. "
                "После набора все самолёты стартуют автоматически; первый на финише победит."
            )
        if kind is ContestType.PARKOUR:
            runner = premium(PARKOUR_IDS[0], "🏃‍♂️")
            obstacles = " ".join(
                (
                    premium(PARKOUR_IDS[1], "🧱"),
                    premium(PARKOUR_IDS[2], "🗑"),
                    premium(PARKOUR_IDS[3], "🐖"),
                )
            )
            return (
                f"{runner} <b>Игра «Паркур» началась!</b>\n\n"
                + common
                + f"На трассе 10 препятствий: {obstacles}. Одно сообщение запускает "
                "одну попытку. Шанс столкновения на каждом препятствии — <b>25%</b>. "
                "Первый добежавший до финиша победит."
            )
        if kind is ContestType.SNAKE:
            snake = premium(SNAKE_IDS[0], "🐍")
            apple = premium(SNAKE_IDS[1], "🍎")
            return (
                f"{snake} <b>Игра «Змейка» началась!</b>\n\n"
                + common
                + f"Нужно от <b>5 до 8</b> участников. Одно сообщение превращает "
                f"участника в {apple}. После набора змея 30 секунд ищет добычу, "
                "останавливается возле яблок и в конце выбирает победителя."
            )
        pickaxe = premium(PICKAXE_IDS[0], "⛏️")
        resources = " ".join(
            premium(emoji_id, fallback)
            for emoji_id, fallback, _ in PICKAXE_RESOURCES
        )
        return (
            f"{pickaxe} <b>Игра «Кирка» началась!</b>\n\n"
            + common
            + f"Нужно ровно <b>5</b> участников — по одному на ресурс: {resources}. "
            "После набора кирка автоматически добывает ресурсы 60 секунд. "
            "Участник с самым большим количеством добычи победит."
        )

    def _parkour_attempt_text(
        self, participant: Participant, attempt: ParkourAttemptUpdate
    ) -> str:
        runner = premium(PARKOUR_IDS[0], "🏃‍♂️")
        obstacle_icons = {
            "wall": premium(PARKOUR_IDS[1], "🧱"),
            "trash": premium(PARKOUR_IDS[2], "🗑"),
            "animal": premium(PARKOUR_IDS[3], "🐖"),
        }
        route: list[str] = []
        for index, kind in enumerate(attempt.obstacle_kinds):
            if attempt.collision_index == index:
                route.append(f"💥{obstacle_icons[kind]}")
            else:
                route.append(obstacle_icons[kind])
        if attempt.collision_index is None:
            result = (
                f"{runner} <b>Финиш!</b> {participant_link(participant)} прошёл "
                "все 10 препятствий и победил."
            )
        else:
            result = (
                f"{runner} {participant_link(participant)} столкнулся с препятствием "
                f"№<b>{attempt.collision_index + 1}</b>, упал и проиграл попытку."
            )
        return f"{result}\n\n{'  '.join(route)}  🏁"

    def _football_start_text(
        self,
        team_a: str,
        team_b: str,
        stars: int,
        seconds: int,
        football_teams: dict[int, str] | None = None,
        football_players: dict[int, int] | None = None,
    ) -> str:
        teams = football_teams or {}
        occupied = set((football_players or {}).values())
        counts = {
            side: sum(value == side for value in teams.values())
            for side in ("a", "b")
        }

        def lineup(start: int, end: int) -> str:
            lines = []
            for index in range(start, end):
                name, emoji_id, fallback = FOOTBALLERS[index]
                taken = index in occupied
                status = premium(
                    FOOTBALL_STATUS_IDS[0 if taken else 1],
                    "✅" if taken else "❌",
                )
                lines.append(
                    f"{status} {premium(emoji_id, fallback)} {html.quote(name)}"
                )
            return "\n".join(lines)

        lineup_a = lineup(0, 5)
        lineup_b = lineup(5, 10)
        ball = premium(FOOTBALL_BALL_ID, "⚽")
        entry_price = (
            "бесплатное сообщение"
            if stars == 0
            else f"платное сообщение за {self._price_text(stars)}"
        )
        join_instruction = (
            "сообщением"
            if stars == 0
            else "платным сообщением с указанной стоимостью"
        )
        return (
            f"{ball} <b>Матч: {html.quote(team_a)} vs {html.quote(team_b)}</b>\n"
            "🏟️ <b>Стадион:</b> «Монстер-Арена»\n\n"
            f"🔵 <b>{html.quote(team_a)} — {counts['a']}/5</b>\n{lineup_a}\n\n"
            f"🔴 <b>{html.quote(team_b)} — {counts['b']}/5</b>\n{lineup_b}\n\n"
            f"⭐️ <b>Вход в команду:</b> {entry_price}\n"
            "💰 <b>Выплаты игрокам победившей команды:</b> "
            "1-е — 2×, 2-е — 1,5×, 3-е — 1,3×, 4-е — 1,2×, 5-е — 1,1×.\n"
            "🤝 <b>При ничьей:</b> выплат нет.\n"
            f"⏰ <b>Набор открыт:</b> {format_duration(seconds)}\n\n"
            f"Чтобы войти, отправьте точное название <b>{html.quote(team_a)}</b> "
            f"или <b>{html.quote(team_b)}</b> {join_instruction}. Затем выберите "
            "свободного футболиста кнопкой. ✅ — занят, ❌ — свободен. "
            "Команду и футболиста изменить нельзя."
        )

    async def _refresh_football_post(
        self, key: ContestKey, state: ContestState
    ) -> None:
        if state.tracking_after_message_id is None:
            return
        remaining = max(
            0,
            math.ceil((state.deadline or 0) - asyncio.get_running_loop().time()),
        )
        text = self._football_start_text(
            state.team_a_name,
            state.team_b_name,
            state.message_stars,
            remaining,
            state.football_teams,
            state.football_players,
        )
        markup = football_pick_keyboard(
            state.team_a_name,
            state.team_b_name,
            set(state.football_players.values()),
        )

        async def edit(
            value: str, keyboard: InlineKeyboardMarkup | None
        ) -> None:
            try:
                await self.bot.edit_message_text(
                    chat_id=key[0],
                    message_id=state.tracking_after_message_id,
                    text=value,
                    reply_markup=keyboard,
                )
            except TelegramBadRequest:
                await self.bot.edit_message_caption(
                    chat_id=key[0],
                    message_id=state.tracking_after_message_id,
                    caption=value,
                    reply_markup=keyboard,
                )

        try:
            await edit(text, markup)
        except TelegramBadRequest:
            try:
                await edit(without_premium(text), without_premium_markup(markup))
            except TelegramAPIError:
                logger.debug("Could not refresh football post", exc_info=True)
        except TelegramAPIError:
            logger.debug("Could not refresh football post", exc_info=True)

    def _case_start_text(self, saved_case: SavedCase) -> str:
        lines = [
            f"{index}. {html.quote(drop.name)} — {format_chance(drop.chance)}%"
            for index, drop in enumerate(saved_case.drops, 1)
        ]
        chances = "\n".join(lines)
        return (
            f"📦 <b>Кейс «{html.quote(saved_case.name)}» открыт!</b>\n\n"
            f"⭐️ <b>Стоимость открытия:</b> {self._price_text(saved_case.stars)}\n"
            f"⏰ <b>Закроется через:</b> "
            f"{format_duration(saved_case.duration_seconds)}\n\n"
            "🔔 <b>Шансы на дроп:</b>\n\n"
            f"<blockquote>{chances}</blockquote>\n\n"
            "Одно сообщение после этого поста = одно открытие. Всем удачи!"
        )

    @staticmethod
    def _activity_start_text() -> str:
        drop_lines = []
        for index, (drop, emoji_id) in enumerate(
            zip(ACTIVITY_DROPS, ACTIVITY_IDS[1:5], strict=True), 1
        ):
            drop_lines.append(
                f"{index}. {premium(emoji_id, drop.name)} — "
                f"{format_chance(drop.chance)}%"
            )
        drop_text = "\n".join(drop_lines)
        return (
            f"{premium(ACTIVITY_IDS[0], '🔔')} <b>Шансы на дроп:</b>\n\n"
            f"<blockquote>{drop_text}</blockquote>\n\n"
            f"{premium(ACTIVITY_IDS[5], '👋')} <b>Всем удачи!</b>\n\n"
            "Каждое обычное сообщение в чате участвует в постоянном розыгрыше."
        )

    async def _finish_timed_game(
        self, key: ContestKey, state: ContestState
    ) -> None:
        if state.kind is ContestType.RACE:
            await self._finish_race(key, state)
        elif state.kind is ContestType.CASE:
            try:
                await self._send_public(
                    key[0],
                    f"📦 <b>Кейс «{html.quote(state.case_name)}» закрыт.</b>"
                )
            except TelegramAPIError:
                logger.exception("Could not announce case expiration")
        elif state.kind is ContestType.AIRPLANE:
            await self._finish_airplane(key, state)
        elif state.kind is ContestType.PARKOUR:
            await self._finish_parkour(key, state)
        elif state.kind is ContestType.SNAKE:
            await self._finish_snake(key, state)
        elif state.kind is ContestType.PICKAXE:
            await self._finish_pickaxe(key, state)
        elif state.kind is ContestType.FOOTBALL:
            await self._finish_football(key, state)

    async def _finish_race(self, key: ContestKey, state: ContestState) -> None:
        participants = list(state.participants.values())
        if not participants:
            try:
                await self._send_public(
                    key[0], "🏎 <b>Гонка отменена:</b> участников нет."
                )
            except TelegramAPIError:
                logger.exception("Could not announce empty race")
            return

        winner = self._random.choice(participants)
        frames = self._race_frames(participants, winner)
        try:
            race_message = await self._send_public(key[0], frames[0])
            for frame in frames[1:]:
                await asyncio.sleep(0.65)
                try:
                    await race_message.edit_text(frame)
                except TelegramBadRequest:
                    logger.debug("Race frame was not changed")
            await asyncio.sleep(0.8)
            await self._announce_winner(key, winner, state)
        except TelegramAPIError:
            logger.exception("Could not animate race")

    def _race_frames(
        self, participants: list[Participant], winner: Participant
    ) -> list[str]:
        visible = participants[:30]
        if winner not in visible:
            visible[-1] = winner
        positions = {participant.user_id: 0 for participant in visible}
        frames: list[str] = []
        for step in range(7):
            if step:
                for participant in visible:
                    if participant.user_id == winner.user_id:
                        positions[participant.user_id] = min(10, step * 2)
                    else:
                        positions[participant.user_id] = min(
                            9,
                            positions[participant.user_id] + self._random.randint(0, 2),
                        )
            lines = []
            for participant in visible:
                position = positions[participant.user_id]
                track = "·" * position + "🏎" + "·" * (10 - position) + "🏁"
                nickname = (
                    f"@{participant.username}"
                    if participant.username
                    else participant.full_name
                )
                lines.append(f"{track} {html.quote(nickname)}")
            if len(participants) > len(visible):
                lines.append(f"…и ещё {len(participants) - len(visible)} участников")
            title = "🏁 <b>Финиш!</b>" if step == 6 else "🏎 <b>Гонка идёт!</b>"
            frames.append(f"{title}\n\n" + "\n".join(lines))
        return frames

    async def _finish_airplane(
        self, key: ContestKey, state: ContestState
    ) -> None:
        participants = list(state.participants.values())
        if not participants:
            await self._safe_public(
                key[0],
                f"{premium(AIRPLANE_IDS[0], '✈️')} "
                "<b>Полёт отменён:</b> участников нет.",
            )
            return
        winner = self._random.choice(participants)
        frames = self._airplane_frames(participants, winner)
        try:
            flight_message = await self._send_public(key[0], frames[0])
            for frame in frames[1:]:
                await asyncio.sleep(0.8)
                try:
                    await flight_message.edit_text(frame)
                except TelegramBadRequest:
                    logger.debug("Airplane frame was not changed")
            await asyncio.sleep(0.8)
            await self._announce_winner(key, winner, state)
        except TelegramAPIError:
            logger.exception("Could not animate airplane game")

    def _airplane_frames(
        self, participants: list[Participant], winner: Participant
    ) -> list[str]:
        visible = participants[:30]
        if winner not in visible:
            visible[-1] = winner
        positions = {participant.user_id: 0 for participant in visible}
        frames: list[str] = []
        for step in range(11):
            birds: set[int] = set()
            if step:
                for participant in visible:
                    hit = self._random.random() < 0.20
                    if hit:
                        birds.add(participant.user_id)
                        positions[participant.user_id] = max(
                            0, positions[participant.user_id] - 1
                        )
                    else:
                        positions[participant.user_id] = min(
                            11,
                            positions[participant.user_id]
                            + self._random.randint(1, 2),
                        )
                if step == 10:
                    positions[winner.user_id] = 12
                    birds.discard(winner.user_id)
                    for participant in visible:
                        if participant.user_id != winner.user_id:
                            positions[participant.user_id] = min(
                                11, positions[participant.user_id]
                            )
            lines = []
            for participant in visible:
                position = positions[participant.user_id]
                plane = premium(AIRPLANE_IDS[0], "✈️")
                bird = (
                    premium(AIRPLANE_IDS[1], "🕊")
                    if participant.user_id in birds
                    else ""
                )
                track = "·" * position + bird + plane + "·" * (12 - position) + "🏁"
                nickname = (
                    f"@{participant.username}"
                    if participant.username
                    else participant.full_name
                )
                lines.append(f"{track} {html.quote(nickname)}")
            title = (
                "🏁 <b>Первый самолёт на финише!</b>"
                if step == 10
                else f"{premium(AIRPLANE_IDS[0], '✈️')} <b>Полёт продолжается!</b>"
            )
            note = (
                f"\n{premium(AIRPLANE_IDS[1], '🕊')} "
                "Птица замедляет самолёт и опускает его назад."
                if birds
                else ""
            )
            frames.append(f"{title}{note}\n\n" + "\n".join(lines))
        return frames

    async def _finish_parkour(self, key: ContestKey, state: ContestState) -> None:
        await self._safe_public(
            key[0],
            f"{premium(PARKOUR_IDS[0], '🏃‍♂️')} <b>Паркур завершён.</b>\n\n"
            "За отведённое время никто не прошёл все 10 препятствий. "
            "Победителя нет.",
        )

    async def _finish_snake(self, key: ContestKey, state: ContestState) -> None:
        participants = list(state.participants.values())
        if len(participants) < 5:
            await self._safe_public(
                key[0],
                f"{premium(SNAKE_IDS[0], '🐍')} <b>Змейка отменена:</b> "
                f"нужно минимум 5 участников, набралось {len(participants)}.",
            )
            return
        participants = participants[:8]
        winner = self._random.choice(participants)
        frames = self._snake_frames(participants, winner)
        snake_message: Message | None = None
        try:
            for step, frame in enumerate(frames):
                if step:
                    await asyncio.sleep(2)
                if snake_message is None:
                    snake_message = await self._send_public(key[0], frame)
                else:
                    try:
                        await snake_message.edit_text(frame)
                    except TelegramBadRequest:
                        try:
                            await snake_message.edit_text(without_premium(frame))
                        except TelegramBadRequest:
                            logger.debug("Snake frame was not changed")
            await self._announce_winner(key, winner, state)
        except TelegramAPIError:
            logger.exception("Could not animate snake game")

    @staticmethod
    def _snake_frames(
        participants: list[Participant], winner: Participant
    ) -> list[str]:
        size = 7
        path = [
            (1, 1), (1, 2), (1, 3), (1, 4), (1, 5),
            (2, 5), (3, 5), (4, 5), (5, 5), (5, 4),
            (5, 3), (5, 2), (5, 1), (4, 1), (3, 1), (2, 1),
        ]
        apple_steps = (4, 8, 12, 15)
        apple_targets = [
            participants[index % len(participants)]
            for index in range(len(apple_steps) - 1)
        ] + [winner]
        frames: list[str] = []
        snake_icon = premium(SNAKE_IDS[0], "🐍")
        apple_icon = premium(SNAKE_IDS[1], "🍎")

        for step, head in enumerate(path):
            eaten = sum(milestone <= step for milestone in apple_steps)
            length = 2 + eaten
            body = set(path[max(0, step - length + 1) : step])
            target_index = next(
                (index for index, milestone in enumerate(apple_steps) if milestone > step),
                len(apple_steps) - 1,
            )
            finished = step == len(path) - 1
            apple_position = None if finished else path[apple_steps[target_index]]
            target = winner if finished else apple_targets[target_index]

            rows: list[str] = []
            for row in range(size):
                cells: list[str] = []
                for column in range(size):
                    cell = (row, column)
                    if cell == head:
                        cells.append(snake_icon)
                    elif cell in body:
                        cells.append("🟩")
                    elif cell == apple_position:
                        cells.append(apple_icon)
                    else:
                        cells.append("⬛")
                rows.append("".join(cells))

            if finished:
                title = f"{snake_icon} <b>Яблоко поймано!</b>"
                target_line = f"🏆 <b>Победитель:</b> {participant_link(winner)}"
            else:
                title = f"{snake_icon} <b>Змейка движется по полю…</b>"
                target_line = f"{apple_icon} <b>Цель:</b> {participant_link(target)}"
            frames.append(f"{title}\n\n" + "\n".join(rows) + f"\n\n{target_line}")
        return frames

    async def _finish_pickaxe(self, key: ContestKey, state: ContestState) -> None:
        participants = list(state.participants.values())[:5]
        if len(participants) < 5:
            await self._safe_public(
                key[0],
                f"{premium(PICKAXE_IDS[0], '⛏️')} <b>Кирка отменена:</b> "
                f"нужно 5 участников, набралось {len(participants)}.",
            )
            return
        counts = {participant.user_id: 0 for participant in participants}
        mining_message: Message | None = None
        winner: Participant | None = None
        try:
            for step in range(13):
                mined_for: Participant | None = None
                if step:
                    await asyncio.sleep(5)
                    mined_for = self._random.choice(participants)
                    counts[mined_for.user_id] += 1
                finished = step == 12
                if finished:
                    top = max(counts.values())
                    leaders = [
                        participant
                        for participant in participants
                        if counts[participant.user_id] == top
                    ]
                    winner = self._random.choice(leaders)
                    if len(leaders) > 1:
                        counts[winner.user_id] += 1
                frame = self._pickaxe_frame(
                    participants,
                    counts,
                    finished,
                    depth=step,
                    mined_user_id=(mined_for.user_id if mined_for else None),
                )
                if mining_message is None:
                    mining_message = await self._send_public(key[0], frame)
                else:
                    try:
                        await mining_message.edit_text(frame)
                    except TelegramBadRequest:
                        try:
                            await mining_message.edit_text(without_premium(frame))
                        except TelegramBadRequest:
                            logger.debug("Pickaxe frame was not changed")
            assert winner is not None
            await self._announce_winner(key, winner, state)
        except TelegramAPIError:
            logger.exception("Could not animate pickaxe game")

    @staticmethod
    def _pickaxe_frame(
        participants: list[Participant],
        counts: dict[int, int],
        finished: bool,
        *,
        depth: int = 0,
        mined_user_id: int | None = None,
    ) -> str:
        pickaxe = premium(PICKAXE_IDS[0], "⛏️")
        shaft_depth = 13
        current_depth = min(max(depth, 0), shaft_depth - 1)
        shaft: list[str] = []
        for row in range(shaft_depth):
            if row < current_depth or finished:
                center = "⬛"
            elif row == current_depth:
                center = pickaxe
            else:
                resource = PICKAXE_RESOURCES[row % len(PICKAXE_RESOURCES)]
                center = premium(resource[0], resource[1])
            shaft.append(f"🟫{center}🟫")

        lines = []
        mined_icon = ""
        for participant, (emoji_id, fallback, resource_name) in zip(
            participants, PICKAXE_RESOURCES, strict=True
        ):
            nickname = (
                f"@{participant.username}"
                if participant.username
                else participant.full_name
            )
            lines.append(
                f"{premium(emoji_id, fallback)} {html.quote(resource_name)} · "
                f"{html.quote(nickname)} — "
                f"<b>{counts[participant.user_id]}</b>"
            )
            if participant.user_id == mined_user_id:
                mined_icon = premium(emoji_id, fallback)
        title = (
            f"{pickaxe} <b>Минута закончилась — шахта выкопана!</b>"
            if finished
            else f"{pickaxe} <b>Кирка копает вниз…</b>"
        )
        mined = f"\nДобыто: {mined_icon}" if mined_icon else ""
        return (
            f"{title}{mined}\n\n"
            + "\n".join(shaft)
            + "\n\n<b>Добыча участников:</b>\n"
            + "\n".join(lines)
        )

    async def _finish_football(self, key: ContestKey, state: ContestState) -> None:
        try:
            if state.tracking_after_message_id is not None:
                await self.bot.edit_message_reply_markup(
                    chat_id=key[0],
                    message_id=state.tracking_after_message_id,
                    reply_markup=None,
                )
        except TelegramAPIError:
            logger.debug("Could not remove football player keyboard")

        score = [0, 0]
        pass_bonus = [0.0, 0.0]
        attacks = [0, 0]
        player_stats = {index: 0 for index in range(len(FOOTBALLERS))}
        match_message: Message | None = None
        try:
            for turn in range(20):
                side = turn % 2
                attacks[side] += 1
                player_index = self._random.choice(
                    range(side * 5, side * 5 + 5)
                )
                action = self._random.choice(("strong", "accurate", "pass"))
                defense = self._random.choice(("keeper", "wall", "intercept"))
                commentary, goal = self._resolve_football_attack(
                    action, defense, pass_bonus, side
                )
                if goal:
                    score[side] += 1
                    player_stats[player_index] += 3
                elif action == "pass" and commentary.startswith("Точный пас"):
                    player_stats[player_index] += 2
                else:
                    player_stats[player_index] += 1
                frame = self._football_scoreboard(
                    state,
                    score,
                    attacks,
                    side,
                    player_index,
                    action,
                    defense,
                    commentary,
                    goal,
                )
                if match_message is None:
                    match_message = await self._send_public(key[0], frame)
                else:
                    await asyncio.sleep(0.7)
                    try:
                        await match_message.edit_text(frame)
                    except TelegramBadRequest:
                        logger.debug("Football frame was not changed")
            await asyncio.sleep(0.7)
            winning_side = 0 if score[0] > score[1] else 1
            ranking = sorted(
                range(winning_side * 5, winning_side * 5 + 5),
                key=lambda index: (-player_stats[index], index),
            )
            await self._announce_football_result(key, state, score, ranking)
        except TelegramAPIError:
            logger.exception("Could not animate football match")

    def _resolve_football_attack(
        self,
        action: str,
        defense: str,
        pass_bonus: list[float],
        side: int,
    ) -> tuple[str, bool]:
        if action == "pass":
            if defense == "intercept" and self._random.random() < 0.55:
                pass_bonus[side] = 0.0
                return "Перехват! Защита читает передачу и забирает мяч.", False
            pass_bonus[side] = min(0.36, pass_bonus[side] + 0.12)
            return "Точный пас! Сила следующей атаки повышена.", False

        probability = 0.46 if action == "strong" else 0.34
        probability += pass_bonus[side]
        pass_bonus[side] = 0.0
        if defense == "keeper":
            probability -= 0.14
        elif defense == "wall" and action == "strong":
            probability -= 0.28
        elif defense == "intercept":
            probability -= 0.08
        probability = max(0.06, min(0.82, probability))
        if self._random.random() < probability:
            description = (
                "Мощнейший удар — мяч в сетке!"
                if action == "strong"
                else "Ювелирный удар в угол — ГОЛ!"
            )
            return description, True
        miss = self._random.choice(
            (
                "Вратарь отбивает мяч!",
                "Удар в штангу — защита выносит!",
                "Мяч уходит в аут.",
                "Защитник блокирует удар.",
            )
        )
        return miss, False

    @staticmethod
    def _football_scoreboard(
        state: ContestState,
        score: list[int],
        attacks: list[int],
        side: int,
        player_index: int,
        action: str,
        defense: str,
        commentary: str,
        goal: bool,
    ) -> str:
        action_labels = {
            "strong": "🚀 Сильный удар",
            "accurate": "🎯 Точный удар",
            "pass": "🤝 Пас",
        }
        defense_labels = {
            "keeper": "🧤 Вратарь на месте",
            "wall": "🛡️ Защитная стенка",
            "intercept": "🏃 Перехват",
        }
        attacker = state.team_a_name if side == 0 else state.team_b_name
        player_name, player_emoji_id, player_fallback = FOOTBALLERS[player_index]
        player = (
            f"{premium(player_emoji_id, player_fallback)} "
            f"{html.quote(player_name)}"
        )
        ball = premium(FOOTBALL_BALL_ID, "⚽")
        minute = min(90, round((sum(attacks) / 20) * 90))
        goal_art = (
            "\n<pre>     ________\n    |   ГОЛ  |\n    |___⚽___|</pre>"
            if goal
            else ""
        )
        return (
            f"{ball} <b>{minute}' — атакует {html.quote(attacker)}</b>\n"
            f"Футболист: {player}\n\n"
            f"Атака: <b>{action_labels[action]}</b>\n"
            f"Защита: <b>{defense_labels[defense]}</b>\n\n"
            f"⚡️ <i>{commentary}</i>{goal_art}\n\n"
            f"🔵 {html.quote(state.team_a_name)} <b>{score[0]} - {score[1]}</b> "
            f"{html.quote(state.team_b_name)} 🔴\n"
            f"Атаки: {attacks[0]}/10 — {attacks[1]}/10"
        )

    async def _announce_football_result(
        self,
        key: ContestKey,
        state: ContestState,
        score: list[int],
        player_ranking: list[int] | None = None,
    ) -> None:
        ball = premium(FOOTBALL_BALL_ID, "⚽")
        if score[0] == score[1]:
            text = (
                "🤝 <b>Матч завершён вничью!</b>\n\n"
                f"{ball} {html.quote(state.team_a_name)} "
                f"<b>{score[0]} - {score[1]}</b> "
                f"{html.quote(state.team_b_name)}\n\n"
                "Победителей нет. Все суммы за вход сгорают."
            )
            await self._safe_public(key[0], text)
            return

        winning_choice = "a" if score[0] > score[1] else "b"
        winning_side = 0 if winning_choice == "a" else 1
        winning_name = (
            state.team_a_name if winning_choice == "a" else state.team_b_name
        )
        expected_players = list(range(winning_side * 5, winning_side * 5 + 5))
        ranking = [
            index
            for index in (player_ranking or expected_players)
            if index in expected_players
        ]
        ranking.extend(index for index in expected_players if index not in ranking)
        ranking = ranking[:5]
        participant_by_player = {
            player_index: state.participants[user_id]
            for user_id, player_index in state.football_players.items()
            if user_id in state.participants
            and state.football_teams.get(user_id) == winning_choice
        }

        def amount(multiplier: float) -> str:
            value = state.message_stars * multiplier
            return (
                str(int(value))
                if value.is_integer()
                else str(value).replace(".", ",")
            )

        def multiplier_text(multiplier: float) -> str:
            return (
                str(int(multiplier))
                if multiplier.is_integer()
                else str(multiplier).replace(".", ",")
            )

        lines = []
        paid_winners = 0
        for place, (player_index, multiplier) in enumerate(
            zip(ranking, FOOTBALL_MULTIPLIERS, strict=True), 1
        ):
            name, emoji_id, fallback = FOOTBALLERS[player_index]
            participant = participant_by_player.get(player_index)
            if participant is None:
                receiver = "<i>не выбран</i>"
            else:
                paid_winners += 1
                receiver = (
                    f"{participant_link(participant)} получает "
                    f"<b>{amount(multiplier)} Stars</b>"
                )
            lines.append(
                f"{place}. {premium(emoji_id, fallback)} <b>{html.quote(name)}</b> "
                f"— <b>{multiplier_text(multiplier)}×</b> — {receiver}"
            )
        ranking_text = "\n".join(lines)
        winners_note = (
            f"Выплаты получают <b>{paid_winners}</b> участников."
            if paid_winners
            else "<i>В победившей команде никто не зафиксировал футболиста.</i>"
        )
        text = (
            f"🏆 <b>Победила команда {html.quote(winning_name)}!</b>\n\n"
            f"{ball} {html.quote(state.team_a_name)} "
            f"<b>{score[0]} - {score[1]}</b> "
            f"{html.quote(state.team_b_name)}\n"
            f"💰 {winners_note}\n\n"
            f"<b>Рейтинг футболистов победившей команды:</b>\n{ranking_text}\n\n"
            f"<b>{html.quote(self.settings.prize_call)}</b>"
        )
        await self._safe_public(key[0], text)

    async def _safe_public(self, chat_id: int, text: str) -> None:
        try:
            await self._send_public(chat_id, text)
        except TelegramAPIError:
            logger.exception("Could not publish contest result")

    def _winner_text(
        self, winner: Participant, state: ContestState
    ) -> str:
        if state.kind is ContestType.CASINO:
            title = "Казино завершено!"
        elif state.kind is ContestType.INTERCEPT:
            title = "Игра «Перебив» завершена!"
        elif state.kind is ContestType.GUESS:
            title = "Число угадано!"
        elif state.kind is ContestType.RACE:
            title = "Гонка завершена!"
        elif state.kind is ContestType.AIRPLANE:
            title = "Полёт завершён!"
        elif state.kind is ContestType.PARKOUR:
            title = "Паркур пройден!"
        elif state.kind is ContestType.SNAKE:
            title = "Змейка выбрала добычу!"
        elif state.kind is ContestType.PICKAXE:
            title = "Добыча ресурсов завершена!"
        else:
            title = "Игра завершена!"
        return (
            f"{premium(WINNER_IDS[0], '🏆')} <b>{title}</b>\n\n"
            f"{premium(WINNER_IDS[1], '🎁')} <b>Приз:</b> "
            f"<i>{format_prize(state.prize)}</i>\n"
            f"{premium(WINNER_IDS[2], '🎉')} <b>Победитель:</b> "
            f"{participant_link(winner)}\n\n"
            f"<b>{html.quote(self.settings.prize_call)}</b>"
        )

    async def _announce_timed_winner(
        self, key: ContestKey, winner: Participant, state: ContestState
    ) -> None:
        await self._announce_winner(key, winner, state)

    async def _announce_winner(
        self, key: ContestKey, winner: Participant, state: ContestState
    ) -> None:
        text = self._winner_text(winner, state)
        try:
            await self._send_public(key[0], text)
        except TelegramAPIError:
            logger.exception("Could not announce winner in group")

    def _status_text(self, state: ContestState | None) -> str:
        if state is None:
            return "Сейчас активной игры нет."
        if state.kind is ContestType.INTERCEPT:
            if state.leader is None:
                return "⚡ «Перебив» активен. Ждём первое сообщение участника."
            remaining = max(
                0,
                math.ceil(
                    (state.deadline or 0) - asyncio.get_running_loop().time()
                ),
            )
            return (
                f"⚡ <b>«Перебив»</b>\n"
                f"Лидер: {participant_link(state.leader)}\n"
                f"Осталось: <b>{format_duration(remaining)}</b>\n"
                f"Приз: <i>{format_prize(state.prize)}</i>"
            )
        if state.kind is ContestType.CASINO:
            return (
                "🎰 <b>Казино 777 активно.</b>\n"
                f"Для победы нужно выбить 777: <b>{state.jackpot_target} раз.</b>\n"
                f"Приз: <i>{format_prize(state.prize)}</i>"
            )
        if state.kind is ContestType.GUESS:
            return (
                "🔢 <b>«Угадай число» активна.</b>\n"
                f"Одна попытка: <b>{self._price_text(state.message_stars)}</b>\n"
                f"Приз: <i>{format_prize(state.prize)}</i>"
            )
        if state.kind is ContestType.RACE:
            remaining = max(
                0,
                math.ceil((state.deadline or 0) - asyncio.get_running_loop().time()),
            )
            return (
                "🏎 <b>Идёт набор на гонку.</b>\n"
                f"Участников: <b>{len(state.participants)}</b>\n"
                f"Осталось: <b>{format_duration(remaining)}</b>\n"
                f"Приз: <i>{format_prize(state.prize)}</i>"
            )
        if state.kind is ContestType.CASE:
            remaining = max(
                0,
                math.ceil((state.deadline or 0) - asyncio.get_running_loop().time()),
            )
            return (
                f"📦 <b>Кейс «{html.quote(state.case_name)}» активен.</b>\n"
                f"Дропов: <b>{len(state.case_drops)}</b>\n"
                f"Осталось: <b>{format_duration(remaining)}</b>"
            )
        if state.kind in {
            ContestType.AIRPLANE,
            ContestType.PARKOUR,
            ContestType.SNAKE,
            ContestType.PICKAXE,
        }:
            remaining = max(
                0,
                math.ceil((state.deadline or 0) - asyncio.get_running_loop().time()),
            )
            title = ARCADE_TITLES[state.kind]
            mode = (
                "попыток"
                if state.kind is ContestType.PARKOUR
                else "участников"
            )
            return (
                f"<b>«{title}» активна.</b>\n"
                f"Принято {mode}: <b>{len(state.participants)}</b>\n"
                f"Осталось: <b>{format_duration(remaining)}</b>\n"
                f"Приз: <i>{format_prize(state.prize)}</i>"
            )
        if state.kind is ContestType.FOOTBALL:
            remaining = max(
                0,
                math.ceil((state.deadline or 0) - asyncio.get_running_loop().time()),
            )
            team_a_count = sum(
                team == "a" for team in state.football_teams.values()
            )
            team_b_count = sum(
                team == "b" for team in state.football_teams.values()
            )
            return (
                f"{premium(FOOTBALL_BALL_ID, '⚽')} "
                f"<b>{html.quote(state.team_a_name)} vs "
                f"{html.quote(state.team_b_name)}</b>\n"
                f"Команды: <b>{team_a_count}/5 — {team_b_count}/5</b>\n"
                f"Футболистов выбрано: <b>{len(state.football_players)}/10</b>\n"
                f"До матча: <b>{format_duration(remaining)}</b>\n"
                "Выплаты по местам: <b>2× / 1,5× / 1,3× / 1,2× / 1,1×</b>."
            )
        return "Игра активна."


async def main() -> None:
    settings = Settings.from_env()
    data_path = Path(settings.data_file)
    if not data_path.is_absolute():
        data_path = Path(__file__).resolve().parent.parent / data_path
    storage = BotStorage(data_path)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    contest_bot = ContestBot(bot, settings, storage)
    dispatcher = Dispatcher()
    dispatcher.include_router(contest_bot.router)

    commands = [
        BotCommand(command="contest", description="Открыть панель"),
        BotCommand(command="help", description="Инструкция"),
    ]
    try:
        await bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
    except TelegramAPIError:
        logger.warning("Could not publish bot commands", exc_info=True)

    logger.info("Monster Contest Bot started")
    try:
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    finally:
        await contest_bot.manager.close()


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        stream=sys.stdout,
    )
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
