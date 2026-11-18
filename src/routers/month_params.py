"""Router-layer month parsing that fails loud.

parse_month_or_422 lives here — not in src/utils.py — because raising an
HTTPException is HTTP-layer behaviour: utils is imported by src/jobs and
src/services, which have no HTTP layer. Its counterpart,
parse_month_or_today in src/utils.py, silently falls back to today and is
for dashboard-style endpoints; report endpoints use this one so a bad
?month= can never silently show a different month than the one asked for.
"""
import re
from datetime import date
from typing import Optional

from fastapi import HTTPException

from src.utils import today_local

_PERIOD_FORMAT = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def parse_month_or_422(month: Optional[str], today: Optional[date] = None) -> tuple[int, int]:
    """Parse a YYYY-MM query value into (year, month). None/"" means "no
    month given" and returns today's year/month. Anything else that isn't
    a real year-month is a 422 (like any other malformed parameter) — it
    never falls back silently."""
    if month is None or month == "":
        current = today or today_local()
        return current.year, current.month
    if not _PERIOD_FORMAT.match(month):
        raise HTTPException(status_code=422, detail="month must be in YYYY-MM format")
    year, month_num = (int(part) for part in month.split("-"))
    return year, month_num
