"""账单决策引擎：匹配 -> 合并 -> AR/AP -> 输出 -> 落库。"""

from __future__ import annotations

from dataclasses import dataclass

from .ar_ap import resolve_posting_type
from .config import Settings
from .db.repositories import BillRepository, OperationRepository, ReviewRepository
from .matching.engine import MatchingEngine
from .models import (
    ChargeItem,
    InternalBillRecord,
    Operation,
    OperationRecord,
    RawDocument,
    ReviewItem,
)
from .output import project_output


@dataclass
class DecisionResult:
    operations: list[OperationRecord]


class DecisionEngine:
    def __init__(
        self,
        bill_repository: BillRepository,
        operation_repository: OperationRepository,
        review_repository: ReviewRepository,
        settings: Settings,
    ):
        self.bill_repository = bill_repository
        self.operation_repository = operation_repository
        self.review_repository = review_repository
        self.settings = settings
        self.matcher = MatchingEngine(bill_repository)

    def decide(
        self,
        record: InternalBillRecord,
        doc: RawDocument,
        run_id: str,
    ) -> DecisionResult:
        posting, ar_issues = resolve_posting_type(record, self.settings)
        record.issues.extend(ar_issues)

        record_level_blocking = [
            issue
            for issue in record.issues
            if issue.charge_id is None
            and (issue.severity == "hard" or issue.review_required)
        ]
        matches = self.matcher.match_record(record)

        raw_decisions: list[dict] = []
        used_existing_charges: set[tuple[str | None, str | None]] = set()

        for match in matches:
            charge = match.charge
            charge_issues = [
                issue
                for issue in record.issues
                if issue.charge_id == charge.charge_id
            ]
            pending_reasons: list[str] = [
                issue.message for issue in record_level_blocking
            ]
            pending_reasons.extend(
                issue.message
                for issue in charge_issues
                if issue.severity == "hard" or issue.review_required
            )

            if not charge.std_name:
                pending_reasons.append("费用名未标准化，无法匹配")
            if charge.amount is None:
                pending_reasons.append("金额缺失")
            if record.confidence < self.settings.confidence_threshold:
                pending_reasons.append(f"单据置信度过低: {record.confidence}")

            operation = Operation.CREATE
            merged_charge: ChargeItem | None = None
            changed_fields = list(match.changed_fields)
            matched_bill = match.bill
            existing_charge = match.existing_charge

            if pending_reasons:
                operation = Operation.PENDING_REVIEW
            elif match.ambiguous:
                operation = Operation.PENDING_REVIEW
                pending_reasons.append(match.match.reason or "匹配歧义")
            elif match.match.bill_id is None:
                operation = Operation.CREATE
            elif existing_charge is None:
                operation = Operation.PENDING_REVIEW
                pending_reasons.append("命中了账单，但没有找到对应费用行")
            else:
                key = (match.bill.bill_id, existing_charge.charge_id)
                if key in used_existing_charges:
                    operation = Operation.PENDING_REVIEW
                    pending_reasons.append("同一已有费用行被多条输入命中")
                else:
                    used_existing_charges.add(key)
                    if changed_fields:
                        operation = Operation.UPDATE
                        merged_charge = MatchingEngine.merge_charge(
                            existing_charge, charge, changed_fields
                        )
                    else:
                        operation = Operation.NO_CHANGE

            raw_decisions.append(
                {
                    "match": match,
                    "charge": charge,
                    "operation": operation,
                    "merged_charge": merged_charge,
                    "existing_charge": existing_charge,
                    "changed_fields": changed_fields,
                    "matched_bill": matched_bill,
                    "issues": charge_issues + record_level_blocking,
                    "pending_reason": "; ".join(pending_reasons) or None,
                }
            )

        # CREATE：同一单据的 CREATE 费用合成一张新账单
        create_charges = [
            item["charge"]
            for item in raw_decisions
            if item["operation"] == Operation.CREATE
        ]
        created_bill = None
        if create_charges:
            created_bill = MatchingEngine.create_bill(record, create_charges)
            self.bill_repository.upsert(created_bill)

        # UPDATE：按账单分组，一次更新一个版本
        updates_by_bill: dict[str, list[tuple[ChargeItem, ChargeItem, list[str]]]] = {}
        for item in raw_decisions:
            if item["operation"] != Operation.UPDATE:
                continue
            bill = item["matched_bill"]
            if bill is None or item["existing_charge"] is None:
                continue
            updates_by_bill.setdefault(bill.bill_id, []).append(
                (
                    item["existing_charge"],
                    item["charge"],
                    item["changed_fields"],
                )
            )

        updated_bills = {}
        for bill_id, updates in updates_by_bill.items():
            bill = self.bill_repository.get(bill_id)
            if bill is None:
                continue
            updated = MatchingEngine.apply_updates(bill, updates)
            self.bill_repository.upsert(updated)
            updated_bills[bill_id] = updated

        # 输出 + 操作落库 + 人工队列
        operations: list[OperationRecord] = []
        for index, item in enumerate(raw_decisions, 1):
            operation = item["operation"]
            if operation == Operation.PENDING_REVIEW:
                charge = item["charge"]
            elif operation == Operation.UPDATE:
                charge = item["merged_charge"] or item["charge"]
            elif operation == Operation.NO_CHANGE:
                charge = item["existing_charge"] or item["charge"]
            else:
                charge = item["charge"]
            matched_bill = item["matched_bill"]

            if operation == Operation.CREATE:
                matched_bill = created_bill
            elif operation == Operation.UPDATE and matched_bill:
                matched_bill = updated_bills.get(matched_bill.bill_id, matched_bill)

            output = project_output(
                doc=doc,
                record=record,
                charge=charge,
                operation=operation,
                match=item["match"].match,
                matched_bill=matched_bill,
                changed_fields=item["changed_fields"],
                issues=item["issues"],
                extra_reasons=(
                    [item["pending_reason"]] if item["pending_reason"] else None
                ),
            )

            op_record = OperationRecord(
                operation_id=f"OP-{run_id}-{doc.doc_id}-{index:03d}",
                charge_id=charge.charge_id,
                matched_bill_id=(
                    matched_bill.bill_id if matched_bill is not None else None
                ),
                operation=operation,
                changed_fields=item["changed_fields"],
                output=output,
                reasons=output.audit_trail.ai_reasoning,
            )
            self.operation_repository.save(run_id, op_record)
            operations.append(op_record)

            if operation == Operation.PENDING_REVIEW:
                self.review_repository.enqueue(
                    run_id,
                    ReviewItem(
                        review_id=f"REV-{run_id}-{doc.doc_id}-{index:03d}",
                        charge_id=charge.charge_id,
                        reason=item["pending_reason"] or "需要人工复核",
                        candidates=item["match"].match.candidates,
                    ),
                )

        return DecisionResult(operations=operations)
