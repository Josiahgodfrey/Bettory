"""Reason builder: every check, passed or failed, with its value and threshold (shown in the full analysis view)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Layer(IntEnum):
    """Verification layers (DATA-SOURCES-AND-MARKET-MODEL.md 17, FIFTEEN-MINUTE-ODDS-CALCULATION.md 10)."""

    SCREEN = 0  # league / competition screen
    DATA = 1  # data quality gate
    MODELS = 2  # models available and in agreement
    VALUE = 3  # price checks (minimum odds, bookmaker quote)
    GRADE = 4  # confidence grade
    RED_FLAGS = 5  # context red flags


@dataclass(frozen=True)
class Check:
    layer: Layer
    code: str  # stable identifier, e.g. "MODEL_GAP_A_B"
    passed: bool | None  # None = informational (does not decide anything)
    value: str  # what was measured, e.g. "7.5 pts"
    threshold: str  # the rule, e.g. "<= 10 pts"
    detail: str = ""
    blocking: bool = True  # False: a failed check that does not decide the fixture (e.g. one quoted market)

    @property
    def blocks(self) -> bool:
        return self.passed is False and self.blocking

    def line(self) -> str:
        mark = {True: "PASS", False: "FAIL" if self.blocking else "MISS", None: "INFO"}[self.passed]
        tail = f" - {self.detail}" if self.detail else ""
        return f"[{mark}] L{int(self.layer)} {self.code}: {self.value} (rule {self.threshold}){tail}"


def pts(p: float) -> str:
    return f"{p * 100:.1f}%"


def gap(points: float) -> str:
    return f"{points:.1f} pts"
