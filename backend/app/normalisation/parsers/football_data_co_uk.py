"""football-data.co.uk CSV parser (main-league files, e.g. mmz4281/2526/E0.csv). Pure.

Column contract (UNVERIFIED on real files - Phase 0 item V5 was blocked by the network):
required: Div, Date, HomeTeam, AwayTeam, FTHG, FTAG; optional: Time, HTHG, HTAG,
Pinnacle closing odds PSCH/PSCD/PSCA (1X2) and PC>2.5 / PC<2.5.
Missing required columns raise ParseError - the parser never guesses a layout.
Time is assumed to be UK local time (Europe/London) - to confirm with notes.txt.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.domain.enums import FixtureStatus, MarketCode, SnapshotType, SourceCode
from app.domain.models import Score, SourceMatch, SourceOdds
from app.domain.registry import Registry
from app.normalisation.parsers import ParseError, ParseResult

SOURCE = SourceCode.FOOTBALL_DATA_CO_UK
UK = ZoneInfo("Europe/London")
REQUIRED = ("Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG")
CLOSING_ODDS = (
    ("PSCH", MarketCode.FT_1X2, "HOME", None, "Match Odds"),
    ("PSCD", MarketCode.FT_1X2, "DRAW", None, "Match Odds"),
    ("PSCA", MarketCode.FT_1X2, "AWAY", None, "Match Odds"),
    ("PC>2.5", MarketCode.FT_TOTAL, "OVER", 2.5, "Over/Under 2.5"),
    ("PC<2.5", MarketCode.FT_TOTAL, "UNDER", 2.5, "Over/Under 2.5"),
)


def _date(text: str) -> date:
    day, month, year = text.strip().split("/")
    return date(int(year) + (2000 if len(year) == 2 else 0), int(month), int(day))


def _int(text: str | None) -> int | None:
    text = (text or "").strip()
    return int(text) if text else None


def parse_csv(raw: bytes, registry: Registry, season_label: str) -> ParseResult:
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="strict")))
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        raise ParseError(f"football-data.co.uk CSV missing required columns: {missing}")

    result = ParseResult()
    for row in reader:
        if not any((v or "").strip() for v in row.values()):
            continue
        comp = registry.by_provider(SOURCE, row["Div"].strip())
        if comp is None:
            result.skipped["unregistered_competition"] += 1
            continue
        try:
            day = _date(row["Date"])
            hh_mm = (row.get("Time") or "").strip()
            kickoff = (datetime.combine(day, time.fromisoformat(hh_mm), tzinfo=UK).astimezone(UTC)
                       if hh_mm else None)
            fthg, ftag, hthg, htag = (_int(row.get(c)) for c in ("FTHG", "FTAG", "HTHG", "HTAG"))
            if fthg is None or ftag is None:
                result.skipped["missing_ft_score"] += 1
                continue
            key = f"{row['Div'].strip()}|{day.isoformat()}|{row['HomeTeam'].strip()}|{row['AwayTeam'].strip()}"
            result.matches.append(SourceMatch(
                source=SOURCE, provider_match_key=key, competition_code=comp.code, season_label=season_label,
                kickoff_date=kickoff.date() if kickoff else day, kickoff_utc=kickoff,
                home_name=row["HomeTeam"], away_name=row["AwayTeam"], status=FixtureStatus.FINISHED,
                ft=Score(home=fthg, away=ftag),
                ht=Score(home=hthg, away=htag) if hthg is not None and htag is not None else None,
                has_goal_minutes=False,
            ))
        except (ValidationError, ValueError) as exc:
            reason = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
            result.skipped[f"invalid_match:{reason}"] += 1
            continue

        if kickoff is None:
            result.skipped["odds_without_kickoff_time"] += 1
            continue
        for column, market, selection, line, name in CLOSING_ODDS:
            text = (row.get(column) or "").strip()
            if not text:
                continue
            try:
                result.odds.append(SourceOdds(
                    source=SOURCE, provider_match_key=key, competition_code=comp.code, bookmaker="Pinnacle",
                    market=market, provider_market_name=name, selection=selection, line=line,
                    odds=float(text), snapshot_type=SnapshotType.CLOSING, captured_at=kickoff,
                ))
            except (ValidationError, ValueError):
                result.skipped["invalid_odds"] += 1
    return result
