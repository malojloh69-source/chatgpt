from __future__ import annotations

import unittest

from app.bot import format_duration, format_prize, parse_duration, premium, without_premium


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


if __name__ == "__main__":
    unittest.main()
