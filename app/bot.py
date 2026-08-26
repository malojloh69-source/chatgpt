from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
from contextlib import suppress

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
    MessageOriginChannel,
    ReplyParameters,
)

from .config import Settings
from .database import Database, Game, Venue
from .templates import (
    casino_progress,
    casino_start,
    completion,
    duration_text,
    guess_start,
    intercept_start,
    takeover,
    without_custom_emoji,
)

logger = logging.getLogger(__name__)
ADMIN_STATUSES = {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}
ACTIVE_STATUSES = {
    ChatMemberStatus.CREATOR,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.RESTRICTED,
}
GAME_NAMES = {
    "casino": "🎰 Казино 777",
    "intercept": "⚡ Перебив",
    "guess": "🔢 Угадай число",
}
INTEGER_RE = re.compile(r"^\d+$")


def chat_type_value(chat_type: ChatType | str) -> str:
    return chat_type.value if isinstance(chat_type, ChatType) else str(chat_type)


class Setup(StatesGroup):
    choosing_channel = State()
    prize = State()
    choosing_count = State()
    custom_count = State()
    choosing_duration = State()
    custom_duration = State()
    choosing_price = State()
    custom_price = State()
    secret_number = State()
    screenshot = State()
    confirmation = State()


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎰 Казино 777", callback_data="new:casino")],
            [InlineKeyboardButton(text="⚡ Перебив", callback_data="new:intercept")],
            [InlineKeyboardButton(text="🔢 Угадай число", callback_data="new:guess")],
            [
                InlineKeyboardButton(text="📡 Мои каналы", callback_data="venues"),
                InlineKeyboardButton(text="🎮 Активные", callback_data="active"),
            ],
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="setup:cancel")]
        ]
    )


def count_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for start in (1, 4, 7):
        rows.append(
            [
                InlineKeyboardButton(
                    text=str(number), callback_data=f"setup:count:{number}"
                )
                for number in range(start, start + 3)
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="10", callback_data="setup:count:10"),
            InlineKeyboardButton(text="Другое", callback_data="setup:count:custom"),
        ]
    )
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="setup:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def duration_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="30 сек.", callback_data="setup:duration:30"),
                InlineKeyboardButton(text="1 мин.", callback_data="setup:duration:60"),
            ],
            [
                InlineKeyboardButton(text="2 мин.", callback_data="setup:duration:120"),
                InlineKeyboardButton(text="5 мин.", callback_data="setup:duration:300"),
            ],
            [
                InlineKeyboardButton(text="10 мин.", callback_data="setup:duration:600"),
                InlineKeyboardButton(text="Другое", callback_data="setup:duration:custom"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="setup:cancel")],
        ]
    )


def price_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Бесплатно", callback_data="setup:price:0"),
                InlineKeyboardButton(text="1 ⭐", callback_data="setup:price:1"),
            ],
            [
                InlineKeyboardButton(text="5 ⭐", callback_data="setup:price:5"),
                InlineKeyboardButton(text="10 ⭐", callback_data="setup:price:10"),
                InlineKeyboardButton(text="25 ⭐", callback_data="setup:price:25"),
            ],
            [
                InlineKeyboardButton(text="50 ⭐", callback_data="setup:price:50"),
                InlineKeyboardButton(text="100 ⭐", callback_data="setup:price:100"),
                InlineKeyboardButton(text="Другое", callback_data="setup:price:custom"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="setup:cancel")],
        ]
    )


def screenshot_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Без скриншота", callback_data="setup:no_photo")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="setup:cancel")],
        ]
    )


def confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Опубликовать", callback_data="setup:start")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="setup:cancel")],
        ]
    )


class MonsterPanelBot:
    def __init__(self, bot: Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings
        self.db = Database(settings.database_path)
        self.router = Router(name="monster-private-panel")
        self.timer_tasks: dict[int, asyncio.Task[None]] = {}
        self.bot_user_id: int | None = None
        self._register_handlers()

    def _register_handlers(self) -> None:
        self.router.my_chat_member.register(self.on_my_chat_member)

        self.router.message.register(self.cmd_start, CommandStart())
        self.router.message.register(
            self.cmd_activate,
            Command(self.settings.access_code, ignore_case=False),
        )

        self.router.callback_query.register(
            self.cb_cancel_setup, F.data == "setup:cancel"
        )
        self.router.callback_query.register(self.cb_main, F.data == "main")
        self.router.callback_query.register(self.cb_new_game, F.data.startswith("new:"))
        self.router.callback_query.register(
            self.cb_refresh_channels, F.data.startswith("refresh:")
        )
        self.router.callback_query.register(self.cb_pick_channel, F.data.startswith("pick:"))
        self.router.callback_query.register(self.cb_count, F.data.startswith("setup:count:"))
        self.router.callback_query.register(
            self.cb_duration, F.data.startswith("setup:duration:")
        )
        self.router.callback_query.register(self.cb_price, F.data.startswith("setup:price:"))
        self.router.callback_query.register(self.cb_no_photo, F.data == "setup:no_photo")
        self.router.callback_query.register(self.cb_start_game, F.data == "setup:start")
        self.router.callback_query.register(self.cb_venues, F.data == "venues")
        self.router.callback_query.register(self.cb_active, F.data == "active")
        self.router.callback_query.register(
            self.cb_cancel_game, F.data.startswith("cancelgame:")
        )

        self.router.message.register(self.receive_prize, Setup.prize, F.text)
        self.router.message.register(self.receive_custom_count, Setup.custom_count, F.text)
        self.router.message.register(
            self.receive_custom_duration, Setup.custom_duration, F.text
        )
        self.router.message.register(self.receive_custom_price, Setup.custom_price, F.text)
        self.router.message.register(self.receive_secret, Setup.secret_number, F.text)
        self.router.message.register(self.receive_screenshot, Setup.screenshot, F.photo)
        self.router.message.register(self.wrong_screenshot, Setup.screenshot)

        self.router.message.register(
            self.on_automatic_forward, F.is_automatic_forward == True
        )
        self.router.message.register(self.on_slot, F.dice.emoji == "🎰")
        self.router.message.register(self.on_group_text, F.text)
        self.router.message.register(self.on_private_fallback, F.chat.type == ChatType.PRIVATE)

    async def _is_activated_message(self, message: Message) -> bool:
        if message.from_user and self.db.is_activated(message.from_user.id):
            return True
        await message.answer(
            "🔒 <b>Доступ закрыт.</b>\n\n"
            f"Введите промокод: <code>/{html.quote(self.settings.access_code)}</code>"
        )
        return False

    async def _is_activated_callback(self, callback: CallbackQuery) -> bool:
        if self.db.is_activated(callback.from_user.id):
            return True
        await callback.answer("Сначала активируйте бота промокодом", show_alert=True)
        return False

    async def _show_main(self, message: Message, *, edit: bool = False) -> None:
        text = (
            "🎛 <b>Личная панель Monster</b>\n\n"
            "Выберите игру. Все настройки и выбор канала выполняются здесь."
        )
        if edit:
            try:
                await message.edit_text(text, reply_markup=main_keyboard())
                return
            except TelegramBadRequest:
                pass
        await message.answer(text, reply_markup=main_keyboard())

    async def cmd_start(self, message: Message, state: FSMContext) -> None:
        if message.chat.type != ChatType.PRIVATE:
            return
        await state.clear()
        if not message.from_user or not self.db.is_activated(message.from_user.id):
            await message.answer(
                "👋 <b>Monster — панель розыгрышей</b>\n\n"
                "Для доступа введите промокод отдельным сообщением:\n"
                f"<code>/{html.quote(self.settings.access_code)}</code>"
            )
            return
        await self._show_main(message)

    async def cmd_activate(self, message: Message, state: FSMContext) -> None:
        if message.chat.type != ChatType.PRIVATE or message.from_user is None:
            return
        self.db.activate_user(message.from_user.id)
        await state.clear()
        with suppress(TelegramAPIError):
            await message.delete()
        await message.answer("✅ <b>Промокод принят. Доступ открыт!</b>")
        await self._show_main(message)

    async def cb_main(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not await self._is_activated_callback(callback):
            return
        await state.clear()
        await callback.answer()
        if isinstance(callback.message, Message):
            await self._show_main(callback.message, edit=True)

    async def cb_cancel_setup(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not await self._is_activated_callback(callback):
            return
        await state.clear()
        await callback.answer("Настройка отменена")
        if isinstance(callback.message, Message):
            await self._show_main(callback.message, edit=True)

    async def cb_new_game(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not await self._is_activated_callback(callback):
            return
        kind = (callback.data or "").split(":", 1)[1]
        if kind not in GAME_NAMES:
            await callback.answer("Неизвестная игра", show_alert=True)
            return
        await state.clear()
        await state.set_state(Setup.choosing_channel)
        await state.update_data(kind=kind)
        await callback.answer()
        if isinstance(callback.message, Message):
            await self._show_channel_picker(callback.message, callback.from_user.id, kind)

    async def cb_refresh_channels(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not await self._is_activated_callback(callback):
            return
        kind = (callback.data or "refresh:casino").split(":", 1)[1]
        if kind not in GAME_NAMES:
            kind = "casino"
        await state.set_state(Setup.choosing_channel)
        await state.update_data(kind=kind)
        await callback.answer("Обновляю список...")
        if isinstance(callback.message, Message):
            await self._show_channel_picker(callback.message, callback.from_user.id, kind)

    async def _available_channels(self, user_id: int) -> list[Venue]:
        available: list[Venue] = []
        for venue in self.db.list_channels()[:40]:
            try:
                member = await self.bot.get_chat_member(venue.chat_id, user_id)
            except TelegramAPIError:
                continue
            if member.status in ADMIN_STATUSES or user_id in self.settings.owner_ids:
                available.append(venue)
        return available

    async def _show_channel_picker(
        self, message: Message, user_id: int, kind: str
    ) -> None:
        channels = await self._available_channels(user_id)
        rows = [
            [
                InlineKeyboardButton(
                    text=f"📣 {venue.title[:35]}",
                    callback_data=f"pick:{kind}:{venue.chat_id}",
                )
            ]
            for venue in channels[:20]
        ]
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔄 Обновить", callback_data=f"refresh:{kind}"
                )
            ]
        )
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main")])
        text = (
            f"📡 <b>Выберите канал для игры «{html.quote(GAME_NAMES[kind])}»</b>\n\n"
            "Канал должен быть связан с группой комментариев. Бот должен быть "
            "администратором и в канале, и в этой группе."
        )
        if not channels:
            text += (
                "\n\n<i>Каналы пока не найдены. Добавьте запущенного бота в канал "
                "администратором, добавьте его в связанную группу и нажмите «Обновить».</i>"
            )
        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
        try:
            await message.edit_text(text, reply_markup=keyboard)
        except TelegramBadRequest:
            await message.answer(text, reply_markup=keyboard)

    async def _validate_channel(
        self, channel_id: int, user_id: int
    ) -> tuple[str, int, str] | str:
        try:
            full_chat = await self.bot.get_chat(channel_id)
            user_member = await self.bot.get_chat_member(channel_id, user_id)
            if user_member.status not in ADMIN_STATUSES and user_id not in self.settings.owner_ids:
                return "Вы не администратор этого канала."
            if self.bot_user_id is None:
                self.bot_user_id = (await self.bot.get_me()).id
            bot_member = await self.bot.get_chat_member(channel_id, self.bot_user_id)
            if bot_member.status not in ADMIN_STATUSES:
                return "Назначьте бота администратором канала."
            if getattr(bot_member, "can_post_messages", True) is False:
                return "Разрешите боту публиковать сообщения в канале."
            linked_chat_id = full_chat.linked_chat_id
            if linked_chat_id is None:
                return "У канала нет привязанной группы для комментариев."
            group_member = await self.bot.get_chat_member(linked_chat_id, self.bot_user_id)
            if group_member.status not in ADMIN_STATUSES:
                return "Назначьте бота администратором связанной группы комментариев."
            linked_chat = await self.bot.get_chat(linked_chat_id)
            self.db.upsert_venue(
                linked_chat.id,
                chat_type_value(linked_chat.type),
                linked_chat.title or str(linked_chat.id),
                linked_chat.username,
                True,
            )
            return (
                full_chat.title or str(channel_id),
                linked_chat_id,
                linked_chat.title or str(linked_chat_id),
            )
        except TelegramAPIError as exc:
            logger.warning("Channel validation failed: %s", exc)
            return "Не удалось проверить канал и связанную группу. Проверьте права бота."

    async def cb_pick_channel(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not await self._is_activated_callback(callback):
            return
        parts = (callback.data or "").split(":")
        if len(parts) != 3 or parts[1] not in GAME_NAMES:
            await callback.answer("Неверная кнопка", show_alert=True)
            return
        kind = parts[1]
        try:
            channel_id = int(parts[2])
        except ValueError:
            await callback.answer("Неверный канал", show_alert=True)
            return
        await callback.answer("Проверяю канал...")
        validation = await self._validate_channel(channel_id, callback.from_user.id)
        if isinstance(validation, str):
            if isinstance(callback.message, Message):
                await callback.message.answer(f"⚠️ {html.quote(validation)}")
            return
        channel_title, discussion_chat_id, discussion_title = validation
        await state.update_data(
            kind=kind,
            channel_id=channel_id,
            channel_title=channel_title,
            discussion_chat_id=discussion_chat_id,
            discussion_title=discussion_title,
        )
        await state.set_state(Setup.prize)
        if isinstance(callback.message, Message):
            await callback.message.answer(
                f"🎁 <b>{GAME_NAMES[kind]} — укажите приз</b>\n\n"
                "Отправьте название, ссылку или запись вида:\n"
                "<code>[ViceCream-431517](https://t.me/nft/ViceCream-431517)</code>",
                reply_markup=cancel_keyboard(),
            )

    async def receive_prize(self, message: Message, state: FSMContext) -> None:
        if not await self._is_activated_message(message):
            return
        prize = (message.text or "").strip()
        if not 1 <= len(prize) <= 400:
            await message.answer("Приз должен содержать от 1 до 400 символов.")
            return
        data = await state.update_data(prize=prize)
        kind = data.get("kind")
        if kind == "casino":
            await state.set_state(Setup.choosing_count)
            await message.answer(
                "🎰 <b>Сколько комбинаций 777 должен выбить один игрок?</b>",
                reply_markup=count_keyboard(),
            )
        elif kind == "intercept":
            await state.set_state(Setup.choosing_duration)
            await message.answer(
                "⏰ <b>Сколько времени лидер должен продержаться без перебива?</b>",
                reply_markup=duration_keyboard(),
            )
        elif kind == "guess":
            await state.set_state(Setup.secret_number)
            await message.answer(
                "🔐 <b>Отправьте секретное число от 1 до 100.</b>\n"
                "Оно останется только в личной панели.",
                reply_markup=cancel_keyboard(),
            )

    async def cb_count(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not await self._is_activated_callback(callback):
            return
        value = (callback.data or "").rsplit(":", 1)[1]
        if value == "custom":
            await state.set_state(Setup.custom_count)
            await callback.answer()
            if isinstance(callback.message, Message):
                await callback.message.answer(
                    "Введите количество комбинаций 777 от 1 до 100:",
                    reply_markup=cancel_keyboard(),
                )
            return
        await state.update_data(target_count=int(value))
        await callback.answer()
        if isinstance(callback.message, Message):
            await self._ask_screenshot(callback.message, state)

    async def receive_custom_count(self, message: Message, state: FSMContext) -> None:
        raw = (message.text or "").strip()
        if not INTEGER_RE.fullmatch(raw) or not 1 <= int(raw) <= 100:
            await message.answer("Введите целое число от 1 до 100.")
            return
        await state.update_data(target_count=int(raw))
        await self._ask_screenshot(message, state)

    async def cb_duration(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not await self._is_activated_callback(callback):
            return
        value = (callback.data or "").rsplit(":", 1)[1]
        if value == "custom":
            await state.set_state(Setup.custom_duration)
            await callback.answer()
            if isinstance(callback.message, Message):
                await callback.message.answer(
                    "Введите время в секундах — от 10 до 86400:",
                    reply_markup=cancel_keyboard(),
                )
            return
        await state.update_data(duration_seconds=int(value))
        await callback.answer()
        if isinstance(callback.message, Message):
            await self._ask_price(callback.message, state)

    async def receive_custom_duration(
        self, message: Message, state: FSMContext
    ) -> None:
        raw = (message.text or "").strip()
        if not INTEGER_RE.fullmatch(raw) or not 10 <= int(raw) <= 86400:
            await message.answer("Введите целое число секунд от 10 до 86400.")
            return
        await state.update_data(duration_seconds=int(raw))
        await self._ask_price(message, state)

    async def _ask_price(self, message: Message, state: FSMContext) -> None:
        await state.set_state(Setup.choosing_price)
        await message.answer(
            "⭐️ <b>Сколько звёзд указать за одно текстовое сообщение?</b>\n\n"
            "Это отображаемая стоимость. Автоматического списания Stars за обычное "
            "сообщение Telegram не выполняет.",
            reply_markup=price_keyboard(),
        )

    async def cb_price(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not await self._is_activated_callback(callback):
            return
        value = (callback.data or "").rsplit(":", 1)[1]
        if value == "custom":
            await state.set_state(Setup.custom_price)
            await callback.answer()
            if isinstance(callback.message, Message):
                await callback.message.answer(
                    "Введите стоимость от 0 до 10000 звёзд:",
                    reply_markup=cancel_keyboard(),
                )
            return
        await state.update_data(message_price=int(value))
        await callback.answer()
        if isinstance(callback.message, Message):
            await self._ask_screenshot(callback.message, state)

    async def receive_custom_price(self, message: Message, state: FSMContext) -> None:
        raw = (message.text or "").strip()
        if not INTEGER_RE.fullmatch(raw) or not 0 <= int(raw) <= 10000:
            await message.answer("Введите целое число от 0 до 10000.")
            return
        await state.update_data(message_price=int(raw))
        await self._ask_screenshot(message, state)

    async def receive_secret(self, message: Message, state: FSMContext) -> None:
        raw = (message.text or "").strip()
        if not INTEGER_RE.fullmatch(raw) or not 1 <= int(raw) <= 100:
            await message.answer("Введите целое число от 1 до 100.")
            return
        await state.update_data(secret_number=int(raw))
        await self._ask_screenshot(message, state)

    async def _ask_screenshot(self, message: Message, state: FSMContext) -> None:
        await state.set_state(Setup.screenshot)
        await message.answer(
            "🖼 <b>Прикрепить скриншот к стартовому посту?</b>\n\n"
            "Отправьте фотографию или нажмите «Без скриншота».",
            reply_markup=screenshot_keyboard(),
        )

    async def receive_screenshot(self, message: Message, state: FSMContext) -> None:
        assert message.photo
        await state.update_data(screenshot_file_id=message.photo[-1].file_id)
        await self._show_confirmation(message, state)

    async def wrong_screenshot(self, message: Message) -> None:
        await message.answer("Отправьте именно фотографию или нажмите «Без скриншота».")

    async def cb_no_photo(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not await self._is_activated_callback(callback):
            return
        await state.update_data(screenshot_file_id=None)
        await callback.answer()
        if isinstance(callback.message, Message):
            await self._show_confirmation(callback.message, state)

    async def _show_confirmation(self, message: Message, state: FSMContext) -> None:
        await state.set_state(Setup.confirmation)
        data = await state.get_data()
        kind = str(data.get("kind"))
        lines = [
            "✅ <b>Проверьте настройки</b>",
            "",
            f"<b>Игра:</b> {GAME_NAMES.get(kind, kind)}",
            f"<b>Канал:</b> {html.quote(str(data.get('channel_title', '—')))}",
            f"<b>Чат:</b> {html.quote(str(data.get('discussion_title', '—')))}",
            f"<b>Приз:</b> {html.quote(str(data.get('prize', '—')))}",
        ]
        if kind == "casino":
            lines.append(f"<b>Нужно 777:</b> {data.get('target_count')} раз")
        elif kind == "intercept":
            lines.append(
                f"<b>Время:</b> {duration_text(int(data.get('duration_seconds', 0)))}"
            )
            lines.append(f"<b>Цена сообщения:</b> {data.get('message_price')} ⭐")
        elif kind == "guess":
            lines.append("<b>Секретное число:</b> задано 🔐")
        lines.append(
            f"<b>Скриншот:</b> {'прикреплён' if data.get('screenshot_file_id') else 'нет'}"
        )
        await message.answer("\n".join(lines), reply_markup=confirmation_keyboard())

    async def cb_start_game(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not await self._is_activated_callback(callback):
            return
        data = await state.get_data()
        kind = data.get("kind")
        required = {"channel_id", "discussion_chat_id", "prize"}
        if kind == "casino":
            required.add("target_count")
        elif kind == "intercept":
            required.update({"duration_seconds", "message_price"})
        elif kind == "guess":
            required.add("secret_number")
        if kind not in GAME_NAMES or not required.issubset(data):
            await callback.answer("Настройки устарели. Начните заново.", show_alert=True)
            await state.clear()
            return
        await callback.answer("Публикую игру...")
        game = self.db.create_game(
            kind=str(kind),
            channel_id=int(data["channel_id"]),
            discussion_chat_id=int(data["discussion_chat_id"]),
            creator_id=callback.from_user.id,
            prize=str(data["prize"]),
            screenshot_file_id=data.get("screenshot_file_id"),
            target_count=data.get("target_count"),
            duration_seconds=data.get("duration_seconds"),
            message_price=data.get("message_price"),
            secret_number=data.get("secret_number"),
        )
        try:
            post_text = self._start_text(game)
            sent = await self._send_channel_post(
                game.channel_id, post_text, game.screenshot_file_id
            )
            self.db.set_channel_message(game.id, sent.message_id)
        except TelegramAPIError as exc:
            self.db.mark_failed(game.id)
            logger.exception("Could not publish game")
            if isinstance(callback.message, Message):
                await callback.message.answer(
                    "❌ Не удалось опубликовать игру. Проверьте права бота в канале.\n"
                    f"<code>{html.quote(type(exc).__name__)}</code>"
                )
            await state.clear()
            return
        await state.clear()
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "✅ <b>Игра опубликована!</b>\n\n"
                "Бот автоматически привяжет комментарии к посту и будет следить "
                "за участниками.",
                reply_markup=main_keyboard(),
            )

    @staticmethod
    def _start_text(game: Game) -> str:
        if game.kind == "casino":
            return casino_start(game.prize, int(game.target_count or 1))
        if game.kind == "intercept":
            return intercept_start(
                game.prize,
                int(game.duration_seconds or 120),
                int(game.message_price or 0),
            )
        return guess_start(game.prize)

    async def _send_channel_post(
        self, chat_id: int, text: str, photo_file_id: str | None
    ) -> Message:
        try:
            if photo_file_id:
                return await self.bot.send_photo(chat_id, photo_file_id, caption=text)
            return await self.bot.send_message(chat_id, text)
        except TelegramBadRequest:
            plain_text = without_custom_emoji(text)
            if photo_file_id:
                return await self.bot.send_photo(chat_id, photo_file_id, caption=plain_text)
            return await self.bot.send_message(chat_id, plain_text)

    async def _send_group_text(
        self, chat_id: int, thread_id: int | None, text: str
    ) -> Message:
        reply = (
            ReplyParameters(message_id=thread_id, allow_sending_without_reply=True)
            if thread_id is not None
            else None
        )
        try:
            return await self.bot.send_message(chat_id, text, reply_parameters=reply)
        except TelegramBadRequest:
            return await self.bot.send_message(
                chat_id,
                without_custom_emoji(text),
                reply_parameters=reply,
            )

    async def cb_venues(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not await self._is_activated_callback(callback):
            return
        await state.clear()
        await callback.answer("Проверяю каналы...")
        channels = await self._available_channels(callback.from_user.id)
        if channels:
            listing = "\n".join(f"• {html.quote(item.title)}" for item in channels)
            text = f"📡 <b>Доступные каналы:</b>\n\n{listing}"
        else:
            text = (
                "📡 <b>Каналов пока нет.</b>\n\n"
                "Добавьте запущенного бота администратором канала и связанной "
                "группы комментариев. После этого вернитесь в панель."
            )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="main")]]
        )
        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_text(text, reply_markup=keyboard)
            except TelegramBadRequest:
                await callback.message.answer(text, reply_markup=keyboard)

    async def cb_active(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not await self._is_activated_callback(callback):
            return
        await state.clear()
        await callback.answer()
        games = self.db.list_active_for_creator(callback.from_user.id)
        rows = [
            [
                InlineKeyboardButton(
                    text=f"⛔ {GAME_NAMES[game.kind]} · #{game.id}",
                    callback_data=f"cancelgame:{game.id}",
                )
            ]
            for game in games
        ]
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main")])
        text = (
            "🎮 <b>Активные игры</b>\n\nНажмите игру, чтобы остановить."
            if games
            else "🎮 <b>Активных игр нет.</b>"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_text(text, reply_markup=keyboard)
            except TelegramBadRequest:
                await callback.message.answer(text, reply_markup=keyboard)

    async def cb_cancel_game(self, callback: CallbackQuery) -> None:
        if not await self._is_activated_callback(callback):
            return
        try:
            game_id = int((callback.data or "").split(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("Неверная игра", show_alert=True)
            return
        game = self.db.cancel_game(game_id, callback.from_user.id)
        if game is None:
            await callback.answer("Игра уже завершена или принадлежит другому пользователю")
            return
        task = self.timer_tasks.pop(game.id, None)
        if task:
            task.cancel()
        await callback.answer("Игра остановлена")
        with suppress(TelegramAPIError):
            await self.bot.send_message(
                game.channel_id, "⛔ <b>Игра остановлена организатором.</b>"
            )
        if isinstance(callback.message, Message):
            await callback.message.answer("⛔ Игра остановлена.", reply_markup=main_keyboard())

    async def on_my_chat_member(self, event: ChatMemberUpdated) -> None:
        if event.chat.type == ChatType.PRIVATE:
            return
        status = event.new_chat_member.status
        active = status in ACTIVE_STATUSES
        self.db.upsert_venue(
            event.chat.id,
            chat_type_value(event.chat.type),
            event.chat.title or event.chat.username or str(event.chat.id),
            event.chat.username,
            active,
        )

    async def on_automatic_forward(self, message: Message) -> None:
        origin = message.forward_origin
        if not isinstance(origin, MessageOriginChannel):
            return
        self.db.bind_discussion_root(
            channel_id=origin.chat.id,
            channel_message_id=origin.message_id,
            discussion_chat_id=message.chat.id,
            root_message_id=message.message_id,
        )

    def _game_for_message(self, message: Message) -> Game | None:
        reply_message_id = (
            message.reply_to_message.message_id if message.reply_to_message else None
        )
        game = self.db.find_active_game(
            message.chat.id, message.message_thread_id, reply_message_id
        )
        if game is not None or message.reply_to_message is None:
            return game
        origin = message.reply_to_message.forward_origin
        if not isinstance(origin, MessageOriginChannel):
            return None
        return self.db.bind_discussion_root(
            channel_id=origin.chat.id,
            channel_message_id=origin.message_id,
            discussion_chat_id=message.chat.id,
            root_message_id=message.reply_to_message.message_id,
        )

    async def on_slot(self, message: Message) -> None:
        if (
            message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}
            or message.from_user is None
            or message.from_user.is_bot
            or message.dice is None
        ):
            return
        game = self._game_for_message(message)
        if game is None or game.kind != "casino" or message.dice.value != 64:
            return
        result = self.db.record_jackpot(
            game.id,
            message.from_user.id,
            message.from_user.full_name,
            message.from_user.username,
        )
        if result is None:
            return
        await asyncio.sleep(3.2)
        if result.won:
            await self._publish_completion(result.game)
            return
        await self._send_group_text(
            game.discussion_chat_id,
            game.discussion_root_message_id,
            casino_progress(
                message.from_user.id,
                message.from_user.full_name,
                message.from_user.username,
                result.count,
                int(game.target_count or 1),
            ),
        )

    async def on_group_text(self, message: Message) -> None:
        if (
            message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}
            or message.from_user is None
            or message.from_user.is_bot
            or (message.text or "").startswith("/")
        ):
            return
        game = self._game_for_message(message)
        if game is None:
            return
        if game.kind == "guess":
            raw = (message.text or "").strip()
            if not INTEGER_RE.fullmatch(raw) or not 1 <= int(raw) <= 100:
                return
            completed = self.db.complete_guess_if_correct(
                game.id,
                int(raw),
                message.from_user.id,
                message.from_user.full_name,
                message.from_user.username,
            )
            if completed:
                await self._publish_completion(completed)
            return
        if game.kind != "intercept":
            return
        result = self.db.set_intercept_leader(
            game.id,
            message.from_user.id,
            message.from_user.full_name,
            message.from_user.username,
        )
        if result is None or not result.accepted:
            return
        assert result.game.deadline is not None
        self._schedule_intercept(result.game.id, result.game.deadline)
        await self._send_group_text(
            result.game.discussion_chat_id,
            result.game.discussion_root_message_id,
            takeover(
                message.from_user.id,
                message.from_user.full_name,
                message.from_user.username,
                int(result.game.duration_seconds or 120),
                first=result.first_leader,
            ),
        )

    def _schedule_intercept(self, game_id: int, deadline: float) -> None:
        old_task = self.timer_tasks.pop(game_id, None)
        if old_task and old_task is not asyncio.current_task():
            old_task.cancel()
        self.timer_tasks[game_id] = asyncio.create_task(
            self._intercept_timer(game_id, deadline),
            name=f"intercept-game-{game_id}",
        )

    async def _intercept_timer(self, game_id: int, deadline: float) -> None:
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(max(0.0, deadline - time.time()))
            completed = self.db.complete_intercept_if_due(game_id, deadline)
            if completed:
                await self._publish_completion(completed)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Intercept timer failed for game %s", game_id)
        finally:
            if self.timer_tasks.get(game_id) is current_task:
                self.timer_tasks.pop(game_id, None)

    async def _publish_completion(self, game: Game) -> None:
        text = completion(game)
        try:
            await self._send_channel_post(game.channel_id, text, None)
        except TelegramAPIError:
            logger.exception("Could not publish completion for game %s", game.id)
            if game.discussion_root_message_id:
                with suppress(TelegramAPIError):
                    await self._send_group_text(
                        game.discussion_chat_id,
                        game.discussion_root_message_id,
                        text,
                    )

    async def restore_timers(self) -> None:
        for game in self.db.active_intercepts_with_leader():
            if game.deadline is not None:
                self._schedule_intercept(game.id, game.deadline)

    async def on_private_fallback(self, message: Message) -> None:
        if message.from_user is None:
            return
        if not self.db.is_activated(message.from_user.id):
            await message.answer(
                "🔒 Неверный или отсутствующий промокод. Используйте:\n"
                f"<code>/{html.quote(self.settings.access_code)}</code>"
            )
            return
        await message.answer("Используйте кнопки личной панели:", reply_markup=main_keyboard())

    async def close(self) -> None:
        tasks = list(self.timer_tasks.values())
        self.timer_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.db.close()


async def main() -> None:
    settings = Settings.from_env()
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,
        ),
    )
    panel = MonsterPanelBot(bot, settings)
    dispatcher = Dispatcher()
    dispatcher.include_router(panel.router)

    try:
        await bot.set_my_commands(
            [BotCommand(command="start", description="Открыть личную панель")],
            scope=BotCommandScopeAllPrivateChats(),
        )
    except TelegramAPIError:
        logger.warning("Could not publish private bot commands", exc_info=True)

    await panel.restore_timers()
    logger.info("Monster private panel started")
    try:
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    finally:
        await panel.close()


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
