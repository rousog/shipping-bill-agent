"""多格式日期解析。

样例中出现：
- 2026-09-20
- 10-24-2026
- 10/03/2025
"""

from __future__ import annotations

from datetime import date, datetime

_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m-%d-%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%Y%m%d",
)


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip()
    for fmt in _FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        # 对 MM-DD-YYYY 与 DD-MM-YYYY 的歧义：优先按美式，月份 > 12 时再看日
        if fmt in ("%m-%d-%Y", "%m/%d/%Y") and parsed.month > 12:
            continue
        return parsed.date()
    return None


def to_iso(value: date | None) -> str | None:
    return value.isoformat() if value else None
