"""Division metadata for the drone fleet.

Maps drone nicknames (as seen in DJI SmartFarm) to the holding's
seven business divisions. Used by the web UI for filtering and
aggregation. Order in DIVISIONS is the canonical display order.
"""
from __future__ import annotations

DRONE_DIVISIONS: dict[str, str] = {
    "1 Klaster":   "Бухара Агрокластер",
    "2 Klaster":   "Бухара Агрокластер",
    "3 Gijduvon":  "Гиждувон ПТЗ",
    "4 Gijduvon":  "Гиждувон ПТЗ",
    "4 Ғиждувон":  "Гиждувон ПТЗ",   # legacy spelling, in case it survived merge
    "5 Shofirko":  "Шофиркон ПТЗ",
    "6 Shofirko":  "Шофиркон ПТЗ",
    "8 Garden":    "Гарден Бухоро Агрокластер",
    "9 Garden":    "Гарден Бухоро Агрокластер",
    "10 Kogon":    "Когон ПТЗ",
    "11 Kogon":    "Когон ПТЗ",
    "12 Peshku":   "Пешку ПТЗ",
    "13 Peshku":   "Пешку ПТЗ",
    "12 Servis":   "Сервис Бухара Агрокластер",
    "13 Servis":   "Сервис Бухара Агрокластер",
    "15 Servis":   "Сервис Бухара Агрокластер",
}

# Canonical display order
DIVISIONS: tuple[str, ...] = (
    "Бухара Агрокластер",
    "Гиждувон ПТЗ",
    "Шофиркон ПТЗ",
    "Гарден Бухоро Агрокластер",
    "Когон ПТЗ",
    "Пешку ПТЗ",
    "Сервис Бухара Агрокластер",
)

UNKNOWN_DIVISION = "—"


def division_for(nickname: str | None) -> str:
    """Return the division for a given drone nickname, or UNKNOWN_DIVISION."""
    if not nickname:
        return UNKNOWN_DIVISION
    return DRONE_DIVISIONS.get(nickname, UNKNOWN_DIVISION)


def drones_in_division(division: str) -> list[str]:
    """Return the list of drone nicknames assigned to the given division."""
    return [nick for nick, div in DRONE_DIVISIONS.items() if div == division]
