from __future__ import annotations

import unittest

from app.database import Game
from app.templates import (
    CASINO_TITLE,
    FINISH_TITLE,
    FINISH_WINNER,
    INTERCEPT_CLOCK,
    INTERCEPT_PRIZE,
    INTERCEPT_STARS,
    PRIZE,
    TAKEOVER_TITLE,
    TAKEOVER_USER,
    TRIPLE_SEVEN,
    casino_start,
    completion,
    format_prize,
    intercept_start,
    takeover,
    without_custom_emoji,
)


def completed_game(kind: str) -> Game:
    return Game(
        id=1,
        kind=kind,
        channel_id=-1,
        discussion_chat_id=-2,
        creator_id=10,
        prize="[ViceCream-431517](https://t.me/nft/ViceCream-431517)",
        screenshot_file_id=None,
        target_count=2,
        duration_seconds=120,
        message_price=5,
        secret_number=42,
        status="completed",
        channel_message_id=1,
        discussion_root_message_id=2,
        leader_user_id=None,
        leader_name=None,
        leader_username=None,
        deadline=None,
        winner_user_id=100,
        winner_name="Winner",
        winner_username="winner",
        created_at=0,
        completed_at=None,
    )


class TemplateTests(unittest.TestCase):
    def test_casino_template_contains_requested_custom_emoji_and_formatting(self) -> None:
        text = casino_start("Gift", 3)
        self.assertIn(TRIPLE_SEVEN, text)
        self.assertIn(PRIZE, text)
        self.assertIn("<b>Казино началось!</b>", text)
        self.assertIn("<i>Gift</i>", text)
        self.assertIn("<b>3</b>", text)

    def test_intercept_template_contains_time_price_and_ids(self) -> None:
        text = intercept_start("Gift", 120, 25)
        self.assertIn(INTERCEPT_CLOCK, text)
        self.assertIn(INTERCEPT_PRIZE, text)
        self.assertIn("2 мин.", text)
        self.assertIn("25 звёзд", text)

    def test_takeover_uses_correct_heading(self) -> None:
        self.assertIn("Лидер определён!", takeover(1, "A", "a", 60, first=True))
        self.assertIn("Перебито!", takeover(2, "B", "b", 60, first=False))

    def test_completion_has_correct_grammar_for_every_game(self) -> None:
        casino = completion(completed_game("casino"))
        intercept = completion(completed_game("intercept"))
        guess = completion(completed_game("guess"))
        self.assertIn("Казино завершено!", casino)
        self.assertIn("Игра «Перебив» завершена!", intercept)
        self.assertIn("Игра «Угадай число» завершена!", guess)
        self.assertIn(FINISH_TITLE, casino)
        self.assertIn("@Monster_Tags, выдай приз победителю!", casino)

    def test_markdown_prize_becomes_safe_html_link(self) -> None:
        result = format_prize("[Gift](https://t.me/nft/Test-1)")
        self.assertEqual(result, '<a href="https://t.me/nft/Test-1">Gift</a>')
        self.assertNotIn("<script>", format_prize("<script>"))

    def test_custom_emoji_can_fall_back_to_plain_emoji(self) -> None:
        rich = casino_start("Gift", 1)
        plain = without_custom_emoji(rich)
        self.assertNotIn("tg-emoji", plain)
        self.assertIn("7️⃣7️⃣7️⃣", plain)

    def test_requested_premium_emoji_order_is_preserved(self) -> None:
        casino = casino_start("Gift", 2)
        self.assertLess(casino.index(CASINO_TITLE), casino.index(PRIZE))
        self.assertLess(casino.index(PRIZE), casino.index(TRIPLE_SEVEN))

        intercept = intercept_start("Gift", 120, 5)
        self.assertLess(intercept.index(INTERCEPT_STARS), intercept.index(INTERCEPT_CLOCK))
        self.assertLess(intercept.index(INTERCEPT_CLOCK), intercept.index(INTERCEPT_PRIZE))

        replaced = takeover(1, "A", "a", 60, first=False)
        self.assertLess(replaced.index(TAKEOVER_TITLE), replaced.index(TAKEOVER_USER))

        finished = completion(completed_game("casino"))
        self.assertLess(finished.index(FINISH_TITLE), finished.index(PRIZE))
        self.assertLess(finished.index(PRIZE), finished.index(FINISH_WINNER))


if __name__ == "__main__":
    unittest.main()
