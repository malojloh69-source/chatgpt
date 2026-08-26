from __future__ import annotations

import asyncio
import logging
import math
import re
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
    Participant,
    SpinStatus,
)
from .storage import BotStorage, KnownChat

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


class CasinoSetup(StatesGroup):
    prize = State()
    jackpot_target = State()
    screenshot = State()


class InterceptSetup(StatesGroup):
    prize = State()
    duration = State()
    stars = State()
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


def panel_keyboard() -> InlineKeyboardMarkup:
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
                InlineKeyboardButton(text="ℹ️ Статус", callback_data="game:status"),
                InlineKeyboardButton(text="⛔ Стоп", callback_data="game:stop"),
            ],
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="panel:home")],
        ]
    )


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


class ContestBot:
    def __init__(self, bot: Bot, settings: Settings, storage: BotStorage) -> None:
        self.bot = bot
        self.settings = settings
        self.storage = storage
        self.manager = ContestManager(self._announce_timed_winner)
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
        return (
            "🎛 <b>Панель управления</b>\n\n"
            f"💬 <b>Группа игры:</b> {group_name}\n\n"
            "Выберите игру и заполните настройки."
        )

    async def _show_panel(self, message: Message, user_id: int) -> None:
        await message.answer(self._panel_text(user_id), reply_markup=panel_keyboard())

    async def cb_panel(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not await self._require_access_callback(callback):
            return
        await state.clear()
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                self._panel_text(callback.from_user.id), reply_markup=panel_keyboard()
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
            self._panel_text(callback.from_user.id), reply_markup=panel_keyboard()
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

    async def cb_cancel_setup(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._require_access_callback(callback):
            return
        await state.clear()
        await callback.answer("Настройка отменена")
        if isinstance(callback.message, Message):
            await callback.message.answer(
                self._panel_text(callback.from_user.id), reply_markup=panel_keyboard()
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

        key = game_key(message.chat.id)
        state = await self.manager.snapshot(key)
        if state is None:
            return
        if (
            state.tracking_after_message_id is not None
            and message.message_id <= state.tracking_after_message_id
        ):
            return
        participant = participant_from(message.from_user)

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
    ) -> Message:
        async def send(value: str) -> Message:
            if screenshot_kind == "photo" and isinstance(screenshot_file_id, str):
                return await self.bot.send_photo(
                    chat_id, screenshot_file_id, caption=value
                )
            if screenshot_kind == "document" and isinstance(screenshot_file_id, str):
                return await self.bot.send_document(
                    chat_id, screenshot_file_id, caption=value
                )
            return await self.bot.send_message(chat_id, value)

        try:
            return await send(text)
        except TelegramBadRequest:
            if "<tg-emoji" not in text:
                raise
            return await send(without_premium(text))

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

    def _winner_text(
        self, winner: Participant, state: ContestState
    ) -> str:
        if state.kind is ContestType.CASINO:
            title = "Казино завершено!"
        elif state.kind is ContestType.INTERCEPT:
            title = "Игра «Перебив» завершена!"
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
