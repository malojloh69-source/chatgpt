from __future__ import annotations

import html
import re

from .database import Game


EVENT = "5213134259098761044"
CASINO_TITLE = "5213134259098761044"
FINISH_TITLE = "5280769763398671636"
PRIZE = "5400182197963496946"
FINISH_WINNER = "5436040291507247633"
TRIPLE_SEVEN = "5913646886819991524"
INTERCEPT_STARS = "5954135079662916434"
INTERCEPT_CLOCK = "5413704112220949842"
INTERCEPT_PRIZE = "5406669204898201943"
INTERCEPT_TITLE = "5213134259098761044"
TAKEOVER_TITLE = "5431870019996767493"
TAKEOVER_USER = "6032693626394382504"

CUSTOM_EMOJI_RE = re.compile(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>')
MARKDOWN_LINK_RE = re.compile(r"^\[([^\]]+)]\((https?://[^\s)]+)\)$")


def custom(emoji_id: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def without_custom_emoji(text: str) -> str:
    return CUSTOM_EMOJI_RE.sub(r"\1", text)


def format_prize(raw: str) -> str:
    value = raw.strip()
    markdown_link = MARKDOWN_LINK_RE.fullmatch(value)
    if markdown_link:
        label, url = markdown_link.groups()
        return f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'
    if value.startswith(("https://", "http://")) and " " not in value:
        safe = html.escape(value, quote=True)
        return f'<a href="{safe}">{safe}</a>'
    return html.escape(value)


def user_mention(user_id: int, full_name: str, username: str | None) -> str:
    if username:
        return f"@{html.escape(username.lstrip('@'))}"
    return f'<a href="tg://user?id={user_id}">{html.escape(full_name)}</a>'


def duration_text(seconds: int) -> str:
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} ч."
    if seconds % 60 == 0:
        return f"{seconds // 60} мин."
    return f"{seconds} сек."


def casino_start(prize: str, target_count: int) -> str:
    return (
        f"{custom(CASINO_TITLE, '🎰')} <b>Казино началось!</b>\n\n"
        f"{custom(PRIZE, '🎁')} <b>Приз:</b>\n<i>{format_prize(prize)}</i>\n\n"
        "<b>Тип:</b>  🎰 <i>Слоты</i>\n"
        f"<b>Кол-во 🎰:</b>  <b>{target_count}</b>\n"
        f"<b>Комбинация:</b> {custom(TRIPLE_SEVEN, '7️⃣7️⃣7️⃣')}\n\n"
        "<i>Кидай слоты в комментариях, чтобы выиграть!</i>"
    )


def intercept_start(prize: str, duration_seconds: int, message_price: int) -> str:
    price = "бесплатно" if message_price == 0 else f"{message_price} звёзд"
    return (
        f"{custom(INTERCEPT_TITLE, '⚠️')} <b>Ивент начался!</b>\n\n"
        f"{custom(INTERCEPT_STARS, '⭐️')} <b>1 сообщение в чате =</b> "
        f"<b>{price}</b>.\n"
        f"{custom(INTERCEPT_CLOCK, '⏰')} <b>Цель:</b> продержаться "
        f"<b>{duration_text(duration_seconds)}</b> без перебива.\n\n"
        f"{custom(INTERCEPT_PRIZE, '🐵')} <b>Приз:</b> <i>{format_prize(prize)}</i>\n\n"
        "<i>Пиши текст в комментариях под этим постом. Другой игрок может тебя перебить!</i>"
    )


def guess_start(prize: str) -> str:
    return (
        f"{custom(EVENT, '🔢')} <b>Игра «Угадай число» началась!</b>\n\n"
        f"{custom(PRIZE, '🎁')} <b>Приз:</b> <i>{format_prize(prize)}</i>\n"
        "🎯 <b>Диапазон:</b> от <b>1</b> до <b>100</b>\n\n"
        "<i>Пиши одно число в комментариях. Первый правильный ответ победит!</i>"
    )


def takeover(
    user_id: int,
    full_name: str,
    username: str | None,
    duration_seconds: int,
    *,
    first: bool,
) -> str:
    title = "Лидер определён!" if first else "Перебито!"
    return (
        f"{custom(TAKEOVER_TITLE, '🧢')} <b>{title}</b>\n"
        f"{custom(TAKEOVER_USER, '👤')} <b>Новый лидер:</b> "
        f"{user_mention(user_id, full_name, username)}\n"
        f"<b>До конца:</b> <i>{duration_text(duration_seconds)}</i>"
    )


def casino_progress(
    user_id: int,
    full_name: str,
    username: str | None,
    count: int,
    target: int,
) -> str:
    return (
        f"🎰 {user_mention(user_id, full_name, username)} выбил 777: "
        f"<b>{count}/{target}</b>"
    )


def completion(game: Game) -> str:
    if game.kind == "casino":
        title = "Казино завершено!"
    elif game.kind == "intercept":
        title = "Игра «Перебив» завершена!"
    else:
        title = "Игра «Угадай число» завершена!"
    assert game.winner_user_id is not None
    return (
        f"{custom(FINISH_TITLE, '🏆')} <b>{title}</b>\n\n"
        f"{custom(PRIZE, '🎁')} <b>Приз:</b> {format_prize(game.prize)}\n"
        f"{custom(FINISH_WINNER, '🎉')} <b>Победитель:</b> "
        f"{user_mention(game.winner_user_id, game.winner_name or 'Победитель', game.winner_username)}\n\n"
        "<b>@Monster_Tags, выдай приз победителю!</b>"
    )
