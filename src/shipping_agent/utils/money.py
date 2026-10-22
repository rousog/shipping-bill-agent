"""金额清洗与类型转换。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def parse_decimal(value: object) -> Decimal | None:
    """把 '$1,410.40'、'(123.45)' 等清洗成 Decimal。

    Credit Note 的负数由上层根据 document_type 决定，这里只做数值解析。
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))

    text = str(value).strip()
    if not text:
        return None

    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    for token in ("$", "USD", "CNY", "EUR", "¥", "￥", ",", " "):
        text = text.replace(token, "")

    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    return -amount if negative else amount


def to_output_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None
