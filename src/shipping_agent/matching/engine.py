"""账单匹配与增量合并。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from ..db.repositories import BillRepository
from ..models import (
    ChargeItem,
    ExistingBill,
    InternalBillRecord,
    MatchResult,
)
from ..utils.dates import to_iso


@dataclass
class ChargeMatch:
    charge: ChargeItem
    match: MatchResult
    bill: ExistingBill | None = None
    existing_charge: ChargeItem | None = None
    changed_fields: list[str] = field(default_factory=list)
    ambiguous: bool = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_bill_id(record: InternalBillRecord) -> str:
    source = (
        record.document_info.reference_no
        or record.document_info.bl_no
        or record.record_id
    )
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", source).strip("-")
    return f"BILL-{safe or record.record_id}"


class MatchingEngine:
    def __init__(self, repository: BillRepository):
        self.repository = repository

    def match_record(self, record: InternalBillRecord) -> list[ChargeMatch]:
        return [self.match_charge(record, charge) for charge in record.charges]

    def match_charge(
        self,
        record: InternalBillRecord,
        charge: ChargeItem,
    ) -> ChargeMatch:
        keys = self._keys(record, charge)
        levels = self.repository.candidate_levels(keys)

        if not levels:
            return ChargeMatch(
                charge=charge,
                match=MatchResult(
                    charge_id=charge.charge_id,
                    reason="no candidate",
                ),
            )

        # 优先唯一命中；第一级多条时用下一级做 tie-break
        selected_level: str | None = None
        selected_ids: list[str] = []
        for level, ids in levels:
            if len(ids) == 1:
                selected_level, selected_ids = level, ids
                break
            if not selected_ids:
                selected_level, selected_ids = level, ids

        if selected_level is None or not selected_ids:
            return ChargeMatch(
                charge=charge,
                match=MatchResult(
                    charge_id=charge.charge_id,
                    reason="no candidate",
                ),
            )

        if len(selected_ids) > 1:
            return ChargeMatch(
                charge=charge,
                match=MatchResult(
                    charge_id=charge.charge_id,
                    level=selected_level,
                    candidates=selected_ids,
                    ambiguous=True,
                    reason="multiple bills matched",
                ),
                ambiguous=True,
            )

        bill_id = selected_ids[0]
        bill = self.repository.get(bill_id)
        if bill is None:
            return ChargeMatch(
                charge=charge,
                match=MatchResult(
                    charge_id=charge.charge_id,
                    level=selected_level,
                    candidates=[bill_id],
                    reason="matched bill not found",
                ),
            )

        existing_charge, ambiguous = self._resolve_existing_charge(bill, charge)
        if ambiguous:
            return ChargeMatch(
                charge=charge,
                match=MatchResult(
                    charge_id=charge.charge_id,
                    bill_id=bill_id,
                    level=selected_level,
                    candidates=[bill_id],
                    ambiguous=True,
                    reason="multiple charge lines matched",
                ),
                bill=bill,
                ambiguous=True,
            )

        changed_fields = (
            self.compare_charge(existing_charge, charge)
            if existing_charge is not None
            else []
        )
        return ChargeMatch(
            charge=charge,
            match=MatchResult(
                charge_id=charge.charge_id,
                bill_id=bill_id,
                level=selected_level,
                candidates=[bill_id],
                reason="unique match",
            ),
            bill=bill,
            existing_charge=existing_charge,
            changed_fields=changed_fields,
        )

    @staticmethod
    def _keys(record: InternalBillRecord, charge: ChargeItem) -> dict:
        info = record.document_info
        party = record.transaction_party
        return {
            "reference_no": info.reference_no,
            "std_name": charge.std_name or charge.raw_name,
            "bl_no": info.bl_no,
            "currency": charge.currency,
            "booking_no": info.booking_no,
            "container_no": info.container_nos[0] if info.container_nos else None,
            "issuer": party.issuer,
            "recipient": party.recipient,
            "document_date": to_iso(info.document_date),
            "amount": charge.amount,
        }

    @staticmethod
    def _resolve_existing_charge(
        bill: ExistingBill,
        incoming: ChargeItem,
    ) -> tuple[ChargeItem | None, bool]:
        name = incoming.std_name or incoming.raw_name
        candidates = [
            charge
            for charge in bill.charges
            if (charge.std_name or charge.raw_name) == name
            and (
                not incoming.currency
                or not charge.currency
                or charge.currency == incoming.currency
            )
        ]
        if not candidates:
            return None, False
        if len(candidates) == 1:
            return candidates[0], False

        if incoming.amount is not None:
            exact = [
                charge
                for charge in candidates
                if charge.amount is not None
                and abs(charge.amount - incoming.amount) < Decimal("0.01")
            ]
            if len(exact) == 1:
                return exact[0], False
        return None, True

    @staticmethod
    def compare_charge(
        existing: ChargeItem,
        incoming: ChargeItem,
    ) -> list[str]:
        changed: list[str] = []
        for field_name in ("amount", "currency", "payment_term"):
            incoming_value = getattr(incoming, field_name)
            if incoming_value is None:
                continue
            existing_value = getattr(existing, field_name)
            if incoming_value != existing_value:
                changed.append(field_name)

        # quantity / unit_price 只在双方都有值时才比较；
        # 已有账单没存这两个字段时，不应误判为 UPDATE。
        for field_name in ("quantity", "unit_price"):
            incoming_value = getattr(incoming, field_name)
            existing_value = getattr(existing, field_name)
            if (
                incoming_value is not None
                and existing_value is not None
                and incoming_value != existing_value
            ):
                changed.append(field_name)
        return changed

    @staticmethod
    def merge_charge(
        existing: ChargeItem,
        incoming: ChargeItem,
        changed_fields: list[str],
    ) -> ChargeItem:
        merged = existing.model_copy(deep=True)
        for field_name in changed_fields:
            setattr(merged, field_name, getattr(incoming, field_name))
        merged.confidence = incoming.confidence
        merged.evidence = incoming.evidence or merged.evidence
        merged.alias_version = incoming.alias_version or merged.alias_version
        return merged

    @staticmethod
    def create_bill(
        record: InternalBillRecord,
        charges: list[ChargeItem],
        *,
        bill_id: str | None = None,
    ) -> ExistingBill:
        total = sum(
            (charge.amount or Decimal("0") for charge in charges),
            Decimal("0"),
        )
        return ExistingBill(
            bill_id=bill_id or make_bill_id(record),
            version=1,
            document_info=record.document_info.model_copy(deep=True),
            transaction_party=record.transaction_party.model_copy(deep=True),
            charges=charges,
            total_amount=total,
            total_currency=record.total_currency
            or next(
                (charge.currency for charge in charges if charge.currency),
                None,
            ),
            financial_attributes=record.financial_attributes.model_copy(deep=True),
            updated_at=_now(),
        )

    @staticmethod
    def apply_updates(
        bill: ExistingBill,
        updates: list[tuple[ChargeItem, ChargeItem, list[str]]],
    ) -> ExistingBill:
        updated = bill.model_copy(deep=True)
        by_id = {charge.charge_id: charge for charge in updated.charges}
        for existing, incoming, changed_fields in updates:
            merged = MatchingEngine.merge_charge(
                existing, incoming, changed_fields
            )
            if existing.charge_id in by_id:
                by_id[existing.charge_id] = merged
            else:
                by_id[merged.charge_id or f"{bill.bill_id}-NEW"] = merged
        updated.charges = list(by_id.values())
        updated.version += 1
        updated.total_amount = sum(
            (charge.amount or Decimal("0") for charge in updated.charges),
            Decimal("0"),
        )
        updated.updated_at = _now()
        return updated
