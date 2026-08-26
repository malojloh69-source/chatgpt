from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.bot import (
    format_duration,
    format_prize,
    paid_message_is_valid,
    parse_chance,
    parse_duration,
    parse_stars,
    premium,
    select_drop,
    without_premium,
)
from app.engine import DropOutcome


class BotHelperTests(unittest.TestCase):
    def test_duration_parser_accepts_seconds_minutes_and_hours(self) -> None:
        self.assertEqual(parse_duration("120"), 120)
        self.assertEqual(parse_duration("2м"), 120)
        self.assertEqual(parse_duration("1h"), 3600)
        self.assertIsNone(parse_duration("9"))
        self.assertIsNone(parse_duration("25h"))

    def test_duration_formatter(self) -> None:
        self.assertEqual(format_duration(120), "2 мин.")
        self.assertEqual(format_duration(65), "1 мин. 5 сек.")

    def test_markdown_style_prize_link_becomes_safe_html(self) -> None:
        result = format_prize("[NFT](https://t.me/nft/Test-1)")
        self.assertIn('<a href="https://t.me/nft/Test-1">', result)
        self.assertIn("NFT", result)

    def test_premium_emoji_has_plain_fallback(self) -> None:
        value = premium("123", "🎰")
        self.assertIn("tg-emoji", value)
        self.assertEqual(without_premium(value), "🎰")

    def test_stars_and_chance_parsers(self) -> None:
        self.assertEqual(parse_stars("1 000"), 1000)
        self.assertEqual(parse_chance("0,05%"), 0.05)
        self.assertIsNone(parse_chance("101"))

    def test_paid_message_is_checked_from_telegram_field(self) -> None:
        self.assertTrue(paid_message_is_valid(SimpleNamespace(paid_star_count=10), 10))
        self.assertFalse(paid_message_is_valid(SimpleNamespace(paid_star_count=9), 10))
        self.assertTrue(paid_message_is_valid(SimpleNamespace(), 0))

    def test_drop_selection_uses_published_percentages(self) -> None:
        drops = (DropOutcome("A", 0.05), DropOutcome("B", 0.04))
        self.assertEqual(select_drop(drops, 0.049).name, "A")
        self.assertEqual(select_drop(drops, 0.07).name, "B")
        self.assertIsNone(select_drop(drops, 0.1))


if __name__ == "__main__":
    unittest.main()
