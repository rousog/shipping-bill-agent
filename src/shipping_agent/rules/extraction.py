"""抽取规则：文档类型、单据号、B/L、箱号、日期、金额、费用行。"""

from __future__ import annotations

import re
from decimal import Decimal

from ..models import ExtractedCharge
from ..utils.money import parse_decimal

# ---------------------------------------------------------------------------
# 表头识别（Excel / Word / PDF 表格共用）
# ---------------------------------------------------------------------------

_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "reference_no": (
        "单据号",
        "单号",
        "账单号",
        "发票号",
        "invoice no",
        "invoice",
        "d/c no",
        "dc no",
        "reference",
        "reference no",
        "document no",
    ),
    "bl_no": (
        "提单号",
        "提单",
        "b/l",
        "bl no",
        "bill of lading",
        "mbl",
        "hbl",
    ),
    "booking_no": ("订舱号", "订舱", "booking", "booking no"),
    "container_no": (
        "箱号",
        "集装箱号",
        "container",
        "container no",
        "cntr",
    ),
    "charge_item": (
        "费用名称",
        "费用",
        "收费项目",
        "项目",
        "charge",
        "charge item",
        "description",
        "item",
    ),
    "amount": ("金额", "合计", "总额", "amount", "total", "debit", "credit"),
    "currency": ("币种", "货币", "currency", "curr", "ccy"),
    "payment_term": (
        "付款方式",
        "预付到付",
        "p/c",
        "pp/cc",
        "payment",
        "prepaid/collect",
    ),
    "quantity": ("数量", "qty", "quantity"),
    "unit_price": ("单价", "unit price", "rate"),
}


def _normalize_header(value: str) -> str:
    text = value.lower().strip()
    text = re.sub(r"[\s_\-/]+", " ", text)
    return text


def match_field(cell: str) -> str | None:
    normalized = _normalize_header(cell)
    if not normalized:
        return None
    for field, aliases in _HEADER_ALIASES.items():
        if any(_normalize_header(alias) == normalized for alias in aliases):
            return field
    candidates: list[tuple[int, str]] = []
    for field, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            normalized_alias = _normalize_header(alias)
            if normalized_alias and normalized_alias in normalized:
                candidates.append((len(normalized_alias), field))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    return None


def detect_header(rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    best_index = 0
    best_mapping: dict[str, int] = {}
    best_score = 0
    for index, row in enumerate(rows[:15]):
        mapping: dict[str, int] = {}
        for col, cell in enumerate(row):
            field = match_field(cell)
            if field and field not in mapping:
                mapping[field] = col
        if len(mapping) > best_score:
            best_index, best_mapping, best_score = index, mapping, len(mapping)
    return best_index, best_mapping


# ---------------------------------------------------------------------------
# 文档类型 / 单据号 / 日期 / B/L / 箱号
# ---------------------------------------------------------------------------

_DOCUMENT_TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Debit Note", ("DEBIT NOTE", "DEBIT MEMO", "DEBIT ADVICE")),
    ("Credit Note", ("CREDIT NOTE", "CREDIT MEMO", "CREDIT ADVICE")),
    ("Arrival Notice", ("ARRIVAL NOTICE", "GENERAL NOTICE")),
    ("Pre-Alert", ("PRE-ALERT", "PRE ALERT", "PREALERT")),
    ("Invoice", ("INVOICE",)),
)

_CURRENCY_RE = re.compile(
    r"\b(USD|CNY|RMB|EUR|GBP|JPY|HKD|SGD|KRW|AUD|CAD|CHF|TWD|MYR|THB|VND|INR)\b",
    re.IGNORECASE,
)

_REFERENCE_PATTERNS = (
    re.compile(r"\b(DC[-/][A-Z0-9][A-Z0-9\-/]{2,})\b", re.IGNORECASE),
    re.compile(r"\b(DC\d{4,})\b", re.IGNORECASE),
    re.compile(r"\b(INV[-/][A-Z0-9][A-Z0-9\-/]{2,})\b", re.IGNORECASE),
    re.compile(r"\b(INV\d{3,})\b", re.IGNORECASE),
    re.compile(
        r"(?:INVOICE|INV)\s+(?:NO\.?|NUMBER)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{2,})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:REFERENCE|REF)\s*(?:NO\.?|NUMBER)\s*[:#]?[ \t]*([A-Z0-9][A-Z0-9\-/]{3,})",
        re.IGNORECASE,
    ),
    re.compile(
        r"EXPORT\s+REFERENCES\s*[:#]?[ \t]*([A-Z0-9]{6,})",
        re.IGNORECASE,
    ),
)

_DATE_PATTERN = re.compile(
    r"(?:D/C\s*DATE|INVOICE\s*DATE|DOCUMENT\s*DATE|DATE)\s*[:#]?\s*"
    r"(\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})",
    re.IGNORECASE,
)

_MBL_PATTERN = re.compile(
    r"(?:MASTER\s*B/L|MBL)\s*(?:NO\.?|NUMBER)?[ \t]*[:#]?[ \t]*([A-Z0-9]{6,})",
    re.IGNORECASE,
)
_HBL_PATTERN = re.compile(
    r"(?:HOUSE\s*B/L|HBL)\s*(?:NO\.?|NUMBER)?[ \t]*[:#]?[ \t]*([A-Z0-9]{6,})",
    re.IGNORECASE,
)
_BL_PATTERN = re.compile(
    r"(?:MASTER\s+B/L|HOUSE\s+B/L|B/L)\s*(?:NO\.?|NUMBER)[ \t]*[:#]?[ \t]*([A-Z0-9]{6,})",
    re.IGNORECASE,
)
_BL_NEWLINE_PATTERN = re.compile(
    r"(?:following\s+)?B/L\s*[:#]?\s*\n+\s*([A-Z0-9]{8,})",
    re.IGNORECASE,
)
_BOOKING_PATTERN = re.compile(
    r"(?:BOOKING|订舱)\s*(?:NO\.?|NUMBER)?\s*[:#]?[ \t]*([A-Z0-9\-]{5,})",
    re.IGNORECASE,
)
_CONTAINER_PATTERN = re.compile(r"\b([A-Z]{4}\d{7})\b")

_TOTAL_PATTERNS = (
    re.compile(
        r"TOTAL\s+AMOUNT\s*[:#]?\s*([\d,]+(?:\.\d+)?)\s*(?:CURRENCY\s*)?([A-Z]{3})?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:GRAND\s+TOTAL|TOTAL\s+CHARGES\s+TO\s+BE\s+PAID)\s*[:#]?\s*"
        r"(?:([A-Z]{3})\s*)?([\d,]+(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    re.compile(r"\bTOTAL\b\s*[:#]?\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE),
)

_DOC_PAYMENT_PATTERNS = (
    ("PP", re.compile(r"\bFREIGHT\s+PREPAID\b|\bPREPAID\b", re.IGNORECASE)),
    ("CC", re.compile(r"\bFREIGHT\s+COLLECT\b|\bCOLLECT\b", re.IGNORECASE)),
)

_SKIP_LINE_KEYWORDS = (
    "TOTAL",
    "GRAND TOTAL",
    "PAID AMOUNT",
    "BALANCE",
    "CURRENCY",
    "D/C DATE",
    "INVOICE DATE",
    "DUE DATE",
    "MBL",
    "HBL",
    "B/L",
    "AMS",
    "ATD",
    "ETA",
    "BOOKING",
    "CONTAINER",
    "CNTR",
    "VESSEL",
    "SHIPPER",
    "CONSIGNEE",
    "NOTIFY",
    "REMARK",
    "PLEASE",
    "BENEFICIARY",
    "BANK",
    "ACCOUNT",
    "ROUTING",
    "DESCRIPTION",
    "UNIT RATE",
    "DEBIT",
    "CREDIT",
    "NO.",
    "FILE NO",
    "CBM",
    "CFT",
    "KGS",
    "LBS",
    "PKGS",
    "TERMS",
    "SERVICE FEE OF",
    "BILLED",
    "NOTICE",
    "EFFECTIVE",
    "INLAND",
    "CANCELLED",
    "MERCHANT",
    "SENT BY",
    "DELIVERY ORDER",
    "CHASSIS",
    "HOUR",
    "CHECKS",
    "TEL:",
    "FAX",
    "EMAIL",
    "ACCT",
    "ACH",
    "THE PARTY",
    "WILL BE",
)

_CHARGE_KEYWORDS = (
    "4 ADD'L DETENTION",
    "ADD'L DETENTION",
    "ON CARRIAGE",
    "HANDLING",
    "WHARFAGE",
    "DETENTION",
    "DEMURRAGE",
    "FREIGHT",
    "TRUCKING",
    "STORAGE",
    "CUSTOMS",
    "DOC FEE",
    "THC",
    "H/C",
)

_DECIMAL_AMOUNT_RE = re.compile(
    r"(?<![\w.])(?:\$|USD|CNY|EUR)?\s*([0-9][0-9,]*\.\d{1,4})(?![\w.])",
    re.IGNORECASE,
)
_AMOUNT_RE = re.compile(
    r"(?<![\w.])(?:\$|USD|CNY|EUR)?\s*([0-9][0-9,]*(?:\.\d{1,4})?)(?![\w.])",
    re.IGNORECASE,
)


def infer_document_type(text: str | None, file_name: str | None = None) -> str | None:
    haystack = f"{file_name or ''}\n{text or ''}".upper()
    for document_type, keywords in _DOCUMENT_TYPE_KEYWORDS:
        for keyword in keywords:
            if keyword in haystack:
                return document_type
    return None


def extract_reference_no(text: str | None) -> str | None:
    text = text or ""
    for pattern in _REFERENCE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return None


def extract_date_text(text: str | None) -> str | None:
    match = _DATE_PATTERN.search(text or "")
    return match.group(1) if match else None


def extract_bl_numbers(text: str | None) -> dict[str, str | None]:
    text = text or ""
    mbl = _MBL_PATTERN.search(text)
    hbl = _HBL_PATTERN.search(text)
    bl = _BL_PATTERN.search(text) or _BL_NEWLINE_PATTERN.search(text)
    bl_value = bl.group(1) if bl else (mbl.group(1) if mbl else None)

    # B/L 标签在一行、号码在下一行的情况（MBL/HBL 附件常见）
    if not bl_value:
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if not re.search(r"B/L\s*(?:NO\.?|NUMBER)", line, re.IGNORECASE):
                continue
            for next_line in lines[index + 1 : index + 4]:
                tokens = [
                    token
                    for token in re.findall(r"[A-Z0-9]{6,}", next_line)
                    if token
                    not in {"NUMBER", "EXPORTER", "REFERENCES", "SHIPPER"}
                ]
                if tokens:
                    bl_value = tokens[-1]
                    break
            if bl_value:
                break

    return {
        "bl_no": bl_value,
        "hbl_no": hbl.group(1) if hbl else None,
    }


def extract_booking_no(text: str | None) -> str | None:
    match = _BOOKING_PATTERN.search(text or "")
    return match.group(1) if match else None


def extract_containers(text: str | None) -> list[str]:
    seen: list[str] = []
    for match in _CONTAINER_PATTERN.finditer(text or ""):
        value = match.group(1)
        if value not in seen:
            seen.append(value)
    return seen


def extract_currency(text: str | None) -> str | None:
    text = text or ""
    if "$" in text and not re.search(r"\b(CNY|RMB)\b", text, re.IGNORECASE):
        return "USD"
    match = _CURRENCY_RE.search(text)
    if not match:
        return None
    value = match.group(1).upper()
    return "CNY" if value == "RMB" else value


def extract_total(text: str | None) -> tuple[Decimal | None, str | None]:
    text = text or ""
    for pattern in _TOTAL_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        groups = match.groups()
        amount_text = None
        currency = None
        for group in groups:
            if group and re.fullmatch(r"[A-Z]{3}", group):
                currency = group
            elif group and re.search(r"\d", group):
                amount_text = group
        if amount_text:
            return parse_decimal(amount_text), currency
    return None, None


def extract_document_payment_term(text: str | None) -> str | None:
    for value, pattern in _DOC_PAYMENT_PATTERNS:
        if pattern.search(text or ""):
            return value
    return None


def _clean_party(value: str) -> str:
    value = value.strip()
    for marker in (
        " NO. OF PKGS",
        " NO. OF PACKAGES",
        " KGS",
        " LBS",
        " CBM",
        " CFT",
        " CONSIGNEE",
        " NOTIFY",
        " VESSEL",
        " CNTR",
        " PORT OF",
        " PLACE OF",
    ):
        if marker in value:
            value = value.split(marker, 1)[0]
    return value.strip(" ,;|")


def extract_parties(text: str | None) -> dict[str, str | None]:
    text = text or ""
    result: dict[str, str | None] = {
        "issuer": None,
        "recipient": None,
        "shipper": None,
        "consignee": None,
    }
    patterns = {
        "shipper": re.compile(
            r"SHIPPER(?:\s*/\s*EXPORTER)?\s*[:：]\s*(.+)", re.IGNORECASE
        ),
        "consignee": re.compile(
            r"CONSIGNEE(?:\s*\(NAME AND ADDRESS\))?\s*[:：]\s*(.+)",
            re.IGNORECASE,
        ),
        "issuer": re.compile(r"ISSUER\s*[:：]\s*(.+)", re.IGNORECASE),
        "recipient": re.compile(r"RECIPIENT\s*[:：]\s*(.+)", re.IGNORECASE),
    }
    for field, pattern in patterns.items():
        match = pattern.search(text)
        if match:
            result[field] = _clean_party(match.group(1))

    if not result["issuer"]:
        for line in text.splitlines():
            line = line.strip()
            if len(line) >= 4 and not re.search(
                r"NOTICE|INVOICE|DEBIT|CREDIT|B/L|DATE|NO\.", line, re.IGNORECASE
            ):
                result["issuer"] = _clean_party(line)
                break

    if not result["recipient"] and result["consignee"]:
        result["recipient"] = result["consignee"]
    return result


# ---------------------------------------------------------------------------
# 费用行
# ---------------------------------------------------------------------------


def _line_has_skip_keyword(line: str) -> bool:
    upper = line.upper()
    return any(keyword in upper for keyword in _SKIP_LINE_KEYWORDS)


def _charge_name_segment(line: str, end: int) -> str:
    upper = line.upper()
    best: tuple[int, str] | None = None
    for keyword in _CHARGE_KEYWORDS:
        index = upper.find(keyword)
        if index < 0:
            continue
        if best is None or index < best[0] or (
            index == best[0] and len(keyword) > len(best[1])
        ):
            best = (index, keyword)
    start = best[0] if best else 0
    segment = line[start:end].strip(" -:：|")
    if ":" in segment or "：" in segment:
        segment = re.split(r"[:：]", segment)[-1].strip()
    segment = re.sub(
        r"\b(USD|CNY|EUR|GBP|JPY|HKD|SGD)\b\s*$",
        "",
        segment,
        flags=re.IGNORECASE,
    )
    return segment.strip(" -:：|$¥￥")


def extract_text_charges(
    text: str | None,
    *,
    default_currency: str | None = None,
    default_payment_term: str | None = None,
) -> list[ExtractedCharge]:
    charges: list[ExtractedCharge] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if len(line) < 5:
            continue
        if _line_has_skip_keyword(line):
            continue
        if re.fullmatch(r"[A-Z0-9\-]{6,}", line):
            continue
        if re.match(r"^\d+[).]\s*", line):
            continue
        amounts = _DECIMAL_AMOUNT_RE.findall(line)
        if not amounts and "$" in line:
            # EML 里可能出现 "$50" 这种整数金额
            amounts = _AMOUNT_RE.findall(line)
        if not amounts:
            continue

        has_charge_keyword = any(
            keyword in line.upper() for keyword in _CHARGE_KEYWORDS
        )
        if not has_charge_keyword and len(amounts) < 2:
            continue

        first_amount = amounts[0]
        first_pos = line.find(first_amount)
        if first_pos < 0:
            continue
        name_part = _charge_name_segment(line, first_pos)
        if len(name_part) < 3:
            continue

        amount = parse_decimal(amounts[-1])
        if amount is None:
            continue
        unit_price = parse_decimal(first_amount) if len(amounts) >= 2 else None
        currency = default_currency
        currency_match = _CURRENCY_RE.search(line)
        if currency_match:
            currency = currency_match.group(1).upper()
            if currency == "RMB":
                currency = "CNY"
        elif "$" in line:
            currency = "USD"

        payment_term = default_payment_term
        pc_match = re.search(r"(?:^|\s)([PC])\s*$", line)
        if pc_match is None and len(amounts) >= 2:
            pc_match = re.search(r"(?<![A-Z/])\b([PC])\b", line)
        if pc_match:
            payment_term = pc_match.group(1).upper()

        charges.append(
            ExtractedCharge(
                raw_name=name_part,
                quantity=parse_decimal(amounts[1]) if len(amounts) >= 3 else None,
                unit_price=unit_price,
                amount=amount,
                currency=currency,
                payment_term=payment_term,
                evidence=line,
            )
        )
    return charges


def extract_table_charges(
    tables: list[list[list[str]]],
    *,
    sheet_mappings: list[dict] | None = None,
    default_currency: str | None = None,
    default_payment_term: str | None = None,
) -> list[ExtractedCharge]:
    charges: list[ExtractedCharge] = []
    for table_index, rows in enumerate(tables or []):
        if not rows:
            continue
        mapping: dict[str, int] = {}
        if sheet_mappings and table_index < len(sheet_mappings):
            mapping = sheet_mappings[table_index].get("column_mapping") or {}
            header_index = sheet_mappings[table_index].get("header_row", 0)
        else:
            header_index, mapping = detect_header(rows)
        if not mapping:
            continue

        def cell(row: list[str], field: str) -> str:
            index = mapping.get(field)
            if index is None or index >= len(row):
                return ""
            return (row[index] or "").strip()

        for row in rows[header_index + 1 :]:
            raw_name = cell(row, "charge_item")
            if not raw_name or "TOTAL" in raw_name.upper():
                continue
            amount = parse_decimal(cell(row, "amount"))
            if amount is None:
                continue
            charges.append(
                ExtractedCharge(
                    raw_name=raw_name,
                    quantity=parse_decimal(cell(row, "quantity")),
                    unit_price=parse_decimal(cell(row, "unit_price")),
                    amount=amount,
                    currency=cell(row, "currency") or default_currency,
                    payment_term=cell(row, "payment_term")
                    or default_payment_term,
                    evidence=" | ".join(cell for cell in row if cell),
                )
            )
    return charges


def extract_ocr_charges(ocr: dict | None) -> list[ExtractedCharge]:
    if not ocr:
        return []
    charges: list[ExtractedCharge] = []
    for item in ocr.get("charges") or []:
        raw_name = item.get("raw_name")
        amount = item.get("amount")
        if not raw_name or amount is None:
            continue
        charges.append(
            ExtractedCharge(
                raw_name=str(raw_name),
                amount=parse_decimal(amount),
                currency=item.get("currency"),
                payment_term=item.get("payment_term"),
                evidence=str(item.get("raw") or ""),
            )
        )
    return charges


class ExtractionRules:
    """薄封装，方便通过 RuleRegistry 注入。"""

    def infer_document_type(self, text: str | None, file_name: str | None = None):
        return infer_document_type(text, file_name)

    def detect_header(self, rows: list[list[str]]):
        return detect_header(rows)

    def extract_reference_no(self, text: str | None):
        return extract_reference_no(text)

    def extract_date_text(self, text: str | None):
        return extract_date_text(text)

    def extract_bl_numbers(self, text: str | None):
        return extract_bl_numbers(text)

    def extract_booking_no(self, text: str | None):
        return extract_booking_no(text)

    def extract_containers(self, text: str | None):
        return extract_containers(text)

    def extract_currency(self, text: str | None):
        return extract_currency(text)

    def extract_total(self, text: str | None):
        return extract_total(text)

    def extract_document_payment_term(self, text: str | None):
        return extract_document_payment_term(text)

    def extract_parties(self, text: str | None):
        return extract_parties(text)

    def extract_text_charges(self, text: str | None, **kwargs):
        return extract_text_charges(text, **kwargs)

    def extract_table_charges(self, tables, **kwargs):
        return extract_table_charges(tables, **kwargs)

    def extract_ocr_charges(self, ocr: dict | None):
        return extract_ocr_charges(ocr)
