"""Клеззи #Добри: original message order, including repeated IDs.

Fallbacks describe the screenshots; Telegram's actual sticker alt is loaded at
startup, so a differently tagged custom emoji is still rendered correctly.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape


@dataclass(frozen=True, slots=True)
class CustomEmoji:
    key: str
    emoji_id: str
    fallback: str


CATALOG = tuple(
    CustomEmoji(*item)
    for item in (
        ("side_smile", "5357427366194849097", "🙂"),
        ("frozen_brain", "5357593469760059395", "🧠"),
        ("tongue", "5355336107963720210", "😛"),
        ("star_eyes", "5399888628358855518", "🤩"),
        ("liar", "5355015823662522952", "🤥"),
        ("star_eyes_repeat", "5399888628358855518", "🤩"),
        ("sick", "5354862738143193630", "🤮"),
        ("dancer", "5220085642192500606", "🕺"),
        ("blue_hero", "5400242941685965198", "🦸"),
        ("agent", "5188293095971657748", "😎"),
        ("blue_hero_repeat", "5400242941685965198", "🦸"),
        ("agent_repeat", "5188293095971657748", "😎"),
        ("television", "5400369252379172299", "📺"),
        ("character", "5404581180777470198", "🧑"),
        ("duck_like", "5766933926429854499", "👍"),
        ("duck_phone", "5848183076898737707", "🐥"),
        ("duck", "5798587088077066898", "🐤"),
        ("duck_phone_repeat", "5848183076898737707", "🐥"),
        ("duck_side", "5767358836134382747", "🐤"),
        ("duck_collection", "5769268580457646836", "🐥"),
        ("sweat", "5373015670822804395", "😅"),
        ("grin", "5372954454653933911", "😀"),
        ("sweat_repeat", "5373015670822804395", "😅"),
        ("upside_down", "5373179691328871991", "🙃"),
        ("smile", "5371073319107827779", "🙂"),
        ("blush", "5370738629486319646", "😊"),
        ("bunny", "5393309541620291208", "🐰"),
        ("pink_bear", "5289761157173775507", "🧸"),
        ("bunny_repeat", "5393309541620291208", "🐰"),
        ("trophy", "5469967260380612012", "🏆"),
        ("trophy_repeat_2", "5469967260380612012", "🏆"),
        ("trophy_repeat_3", "5469967260380612012", "🏆"),
        ("seven", "5915988541644475488", "7️⃣"),
        ("slots", "5915833712368424979", "🎰"),
        ("seven_alt", "5913646886819991524", "7️⃣"),
        ("bar_vertical", "5915636603434306986", "🎰"),
        ("bar", "5915589848420322417", "🎰"),
        ("berries", "5915967083987864750", "🫐"),
        ("avocado", "5211061572306219675", "🥑"),
        ("fruit_glasses", "5474514754573709786", "😎"),
        ("avocado_repeat", "5211061572306219675", "🥑"),
        ("fruit_dark", "5251416878861006232", "🫐"),
        ("fruit_headphones", "5219823859640839566", "🍆"),
        ("fruit_face", "5285518360845495987", "🍊"),
        ("fruit_face_repeat", "5285518360845495987", "🍊"),
        ("die_7", "5796326170047814257", "🎲"),
        ("die_10", "5798876319764716215", "🎲"),
        ("die_6", "5798858658859195103", "🎲"),
        ("die_19", "5798922250144979795", "🎲"),
        ("die_19_repeat", "5798922250144979795", "🎲"),
        ("letter_z", "5397725695713491786", "💤"),
        ("badge", "5226800514322173806", "🔘"),
        ("mask", "5226660060301658486", "🎭"),
        ("skull", "5226528574172865404", "💀"),
        ("bang", "5397890704062038337", "‼️"),
        ("bang_repeat", "5397890704062038337", "‼️"),
    )
)
BY_KEY = {item.key: item for item in CATALOG}
STICKER_ALTS: dict[str, str] = {}


def premium(emoji_id: str, fallback: str) -> str:
    alt = STICKER_ALTS.get(emoji_id, fallback)
    return (
        f'<tg-emoji emoji-id="{escape(emoji_id, quote=True)}">{escape(alt)}</tg-emoji>'
    )


def icon(key: str) -> str:
    item = BY_KEY[key]
    return premium(item.emoji_id, item.fallback)


def gallery() -> str:
    groups = (
        ("Эмоции", 0, 8),
        ("Персонажи", 8, 14),
        ("Уточки", 14, 20),
        ("Улыбки", 20, 26),
        ("Подарки и кубки", 26, 32),
        ("Слоты", 32, 38),
        ("Фрукты", 38, 45),
        ("Кости", 45, 50),
        ("Символы", 50, 56),
    )
    return (
        "<b>Клеззи #Добри</b>\n<i>Порядок со скриншотов сохранён.</i>\n\n"
        + "\n\n".join(
            f"<b>{title}</b>\n"
            + " ".join(
                premium(item.emoji_id, item.fallback) for item in CATALOG[start:end]
            )
            for title, start, end in groups
        )
    )
