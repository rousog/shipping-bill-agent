"""SQLite Repositories。

Phase 0 先实现：
- AssetRepository
- RunRepository
- SqliteBillRepository（含 L1–L4 候选查询）
- ReviewRepository
- OperationRepository
- AuditRepository
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

from ..models import (
    AssetRef,
    ChargeItem,
    DocumentInfo,
    DocumentType,
    ExistingBill,
    FinancialAttributes,
    MatchResult,
    OperationRecord,
    PartyInfo,
    PaymentTerm,
    PostingType,
    ReviewItem,
    ValidationIssue,
)
from ..utils.dates import parse_date, to_iso
from .connection import transaction


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: str | None) -> dict:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def _document_type(value: str | None) -> DocumentType | None:
    if not value:
        return None
    try:
        return DocumentType(value)
    except ValueError:
        return DocumentType.OTHER


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


class AssetRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert(self, asset: AssetRef) -> None:
        self.conn.execute(
            """
            INSERT INTO assets (
              asset_id, sha256, file_name, mime_type, size_bytes,
              storage_backend, storage_key, parent_asset_id, source_type,
              is_inline, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
              file_name=excluded.file_name,
              mime_type=excluded.mime_type,
              size_bytes=excluded.size_bytes,
              storage_backend=excluded.storage_backend,
              storage_key=excluded.storage_key,
              parent_asset_id=excluded.parent_asset_id,
              source_type=excluded.source_type,
              is_inline=excluded.is_inline,
              metadata_json=excluded.metadata_json
            """,
            (
                asset.asset_id,
                asset.sha256,
                asset.file_name,
                asset.mime_type,
                asset.size_bytes,
                asset.storage_backend,
                asset.storage_key,
                asset.parent_asset_id,
                asset.source_type.value if asset.source_type else None,
                int(asset.is_inline),
                _now(),
                _json(asset.metadata),
            ),
        )
        self.conn.commit()

    def get(self, asset_id: str) -> AssetRef | None:
        row = self.conn.execute(
            "SELECT * FROM assets WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        return self._row_to_asset(row) if row else None

    def get_by_sha256(self, sha256: str) -> AssetRef | None:
        row = self.conn.execute(
            "SELECT * FROM assets WHERE sha256 = ?", (sha256,)
        ).fetchone()
        return self._row_to_asset(row) if row else None

    @staticmethod
    def _row_to_asset(row: sqlite3.Row) -> AssetRef:
        return AssetRef(
            asset_id=row["asset_id"],
            sha256=row["sha256"],
            file_name=row["file_name"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            storage_backend=row["storage_backend"],
            storage_key=row["storage_key"],
            parent_asset_id=row["parent_asset_id"],
            source_type=row["source_type"],
            is_inline=bool(row["is_inline"]),
            metadata=_loads(row["metadata_json"]),
        )


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class RunRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        run_id: str,
        input_path: str,
        *,
        config_hash: str | None = None,
        alias_version: str | None = None,
        prompt_version: str | None = None,
        model: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO runs (
              run_id, input_path, started_at, config_hash, alias_version,
              prompt_version, model, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running')
            """,
            (run_id, input_path, _now(), config_hash, alias_version, prompt_version, model),
        )
        self.conn.commit()

    def finish(self, run_id: str, status: str = "done") -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at = ?, status = ? WHERE run_id = ?",
            (_now(), status, run_id),
        )
        self.conn.commit()


# ---------------------------------------------------------------------------
# Bills
# ---------------------------------------------------------------------------


class BillRepository(Protocol):
    def get(self, bill_id: str) -> ExistingBill | None: ...

    def list_candidates(self, keys: dict) -> list[str]: ...

    def candidate_levels(self, keys: dict) -> list[tuple[str, list[str]]]: ...

    def upsert(self, bill: ExistingBill) -> None: ...


class SqliteBillRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, bill_id: str) -> ExistingBill | None:
        row = self.conn.execute(
            """
            SELECT eb.bill_id, eb.version, eb.accounting_entity, eb.posting_type,
                   eb.status, eb.updated_at, d.*
            FROM existing_bills eb
            JOIN documents d ON d.document_id = eb.document_id
            WHERE eb.bill_id = ?
            """,
            (bill_id,),
        ).fetchone()
        if not row:
            return None

        document_id = row["document_id"]
        container_rows = self.conn.execute(
            "SELECT container_no FROM containers WHERE document_id = ?",
            (document_id,),
        ).fetchall()
        charge_rows = self.conn.execute(
            "SELECT * FROM charges WHERE document_id = ? ORDER BY line_no, charge_id",
            (document_id,),
        ).fetchall()

        document_info = DocumentInfo(
            document_type=_document_type(row["document_type"]),
            reference_no=row["reference_no"],
            document_date=parse_date(row["document_date"]),
            bl_no=row["bl_no"],
            hbl_no=row["hbl_no"],
            booking_no=row["booking_no"],
            invoice_no=row["invoice_no"],
            container_nos=[r["container_no"] for r in container_rows],
        )
        party = PartyInfo(
            issuer=row["issuer"],
            recipient=row["recipient"],
            shipper=row["shipper"],
            consignee=row["consignee"],
        )
        posting = None
        if row["posting_type"]:
            try:
                posting = PostingType(row["posting_type"])
            except ValueError:
                posting = None

        return ExistingBill(
            bill_id=row["bill_id"],
            version=row["version"],
            document_info=document_info,
            transaction_party=party,
            charges=[self._row_to_charge(r) for r in charge_rows],
            total_amount=Decimal(str(row["total_amount"]))
            if row["total_amount"] is not None
            else None,
            total_currency=row["total_currency"],
            financial_attributes=FinancialAttributes(
                accounting_entity=row["accounting_entity"],
                posting_type=posting,
            ),
            updated_at=row["updated_at"],
        )

    def upsert(self, bill: ExistingBill) -> None:
        document_id = f"DOC-{bill.bill_id}"
        now = _now()
        doc = bill.document_info
        party = bill.transaction_party

        total_amount = bill.total_amount
        if total_amount is None and bill.charges:
            total_amount = sum(
                (charge.amount or Decimal("0") for charge in bill.charges),
                Decimal("0"),
            )
        total_currency = bill.total_currency or next(
            (charge.currency for charge in bill.charges if charge.currency),
            None,
        )

        with transaction(self.conn):
            self.conn.execute(
                """
                INSERT INTO documents (
                  document_id, asset_id, document_type, reference_no, document_date,
                  issuer, recipient, shipper, consignee, bl_no, hbl_no, booking_no,
                  invoice_no, total_amount, total_currency, confidence, status,
                  raw_text, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                  document_type=excluded.document_type,
                  reference_no=excluded.reference_no,
                  document_date=excluded.document_date,
                  issuer=excluded.issuer,
                  recipient=excluded.recipient,
                  shipper=excluded.shipper,
                  consignee=excluded.consignee,
                  bl_no=excluded.bl_no,
                  hbl_no=excluded.hbl_no,
                  booking_no=excluded.booking_no,
                  invoice_no=excluded.invoice_no,
                  total_amount=excluded.total_amount,
                  total_currency=excluded.total_currency,
                  confidence=excluded.confidence,
                  status=excluded.status
                """,
                (
                    document_id,
                    None,  # seed bills 可以先没有 asset
                    doc.document_type.value if doc.document_type else None,
                    doc.reference_no,
                    to_iso(doc.document_date),
                    party.issuer,
                    party.recipient,
                    party.shipper,
                    party.consignee,
                    doc.bl_no,
                    doc.hbl_no,
                    doc.booking_no,
                    doc.invoice_no,
                    float(total_amount) if total_amount is not None else None,
                    total_currency,
                    1.0,
                    "active",
                    None,
                    now,
                ),
            )

            self.conn.execute(
                "DELETE FROM charges WHERE document_id = ?", (document_id,)
            )
            for index, charge in enumerate(bill.charges):
                self.conn.execute(
                    """
                    INSERT INTO charges (
                      charge_id, document_id, line_no, raw_name, std_name,
                      alias_version, quantity, unit_price, amount, currency,
                      payment_term, evidence, confidence, issues_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        charge.charge_id or f"{bill.bill_id}-CHG-{index + 1:03d}",
                        document_id,
                        charge.line_no or index + 1,
                        charge.raw_name,
                        charge.std_name,
                        charge.alias_version,
                        float(charge.quantity) if charge.quantity is not None else None,
                        float(charge.unit_price) if charge.unit_price is not None else None,
                        float(charge.amount) if charge.amount is not None else None,
                        charge.currency,
                        charge.payment_term.value if charge.payment_term else None,
                        charge.evidence,
                        charge.confidence,
                        _json([i.model_dump() for i in charge.issues]),
                        now,
                    ),
                )

            self.conn.execute(
                "DELETE FROM containers WHERE document_id = ?", (document_id,)
            )
            for container_no in doc.container_nos:
                self.conn.execute(
                    "INSERT INTO containers(document_id, container_no) VALUES (?, ?)",
                    (document_id, container_no),
                )

            self.conn.execute(
                """
                INSERT INTO existing_bills (
                  bill_id, version, document_id, accounting_entity,
                  posting_type, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?)
                ON CONFLICT(bill_id) DO UPDATE SET
                  version=excluded.version,
                  document_id=excluded.document_id,
                  accounting_entity=excluded.accounting_entity,
                  posting_type=excluded.posting_type,
                  status=excluded.status,
                  updated_at=excluded.updated_at
                """,
                (
                    bill.bill_id,
                    bill.version,
                    document_id,
                    bill.financial_attributes.accounting_entity,
                    bill.financial_attributes.posting_type.value
                    if bill.financial_attributes.posting_type
                    else None,
                    now,
                ),
            )

    @staticmethod
    def _candidate_levels() -> list[tuple[str, list[str], str]]:
        return [
            (
                "L1",
                ["reference_no", "std_name"],
                """
                SELECT DISTINCT eb.bill_id
                FROM existing_bills eb
                JOIN documents d ON d.document_id = eb.document_id
                JOIN charges c ON c.document_id = d.document_id
                WHERE d.reference_no = :reference_no
                  AND c.std_name = :std_name
                """,
            ),
            (
                "L2",
                ["bl_no", "std_name", "currency"],
                """
                SELECT DISTINCT eb.bill_id
                FROM existing_bills eb
                JOIN documents d ON d.document_id = eb.document_id
                JOIN charges c ON c.document_id = d.document_id
                WHERE d.bl_no = :bl_no
                  AND c.std_name = :std_name
                  AND c.currency = :currency
                """,
            ),
            (
                "L3",
                ["booking_no", "std_name", "currency"],
                """
                SELECT DISTINCT eb.bill_id
                FROM existing_bills eb
                JOIN documents d ON d.document_id = eb.document_id
                JOIN charges c ON c.document_id = d.document_id
                WHERE d.booking_no = :booking_no
                  AND c.std_name = :std_name
                  AND c.currency = :currency
                """,
            ),
            (
                "L4",
                [
                    "container_no",
                    "issuer",
                    "recipient",
                    "document_date",
                    "amount",
                ],
                """
                SELECT DISTINCT eb.bill_id
                FROM existing_bills eb
                JOIN documents d ON d.document_id = eb.document_id
                JOIN containers ct ON ct.document_id = d.document_id
                JOIN charges c ON c.document_id = d.document_id
                WHERE ct.container_no = :container_no
                  AND d.issuer = :issuer
                  AND d.recipient = :recipient
                  AND d.document_date = :document_date
                  AND ABS(c.amount - :amount) < 0.01
                """,
            ),
        ]

    def candidate_levels(self, keys: dict) -> list[tuple[str, list[str]]]:
        def _has(*names: str) -> bool:
            return all(keys.get(name) not in (None, "") for name in names)

        results: list[tuple[str, list[str]]] = []
        for level, required, sql in self._candidate_levels():
            if not _has(*required):
                continue
            params = {name: keys.get(name) for name in required}
            if "amount" in params and params["amount"] is not None:
                params["amount"] = float(params["amount"])
            rows = self.conn.execute(sql, params).fetchall()
            if rows:
                results.append((level, [row["bill_id"] for row in rows]))
        return results

    def list_candidates(self, keys: dict) -> list[str]:
        levels = self.candidate_levels(keys)
        return levels[0][1] if levels else []

    @staticmethod
    def _row_to_charge(row: sqlite3.Row) -> ChargeItem:
        raw_issues = _loads(row["issues_json"])
        issue_items = (
            raw_issues
            if isinstance(raw_issues, list)
            else raw_issues.get("issues", [])
        )
        issues = [ValidationIssue(**item) for item in issue_items]
        payment = None
        if row["payment_term"]:
            try:
                payment = PaymentTerm(row["payment_term"])
            except ValueError:
                payment = None
        return ChargeItem(
            charge_id=row["charge_id"],
            line_no=row["line_no"],
            raw_name=row["raw_name"],
            std_name=row["std_name"],
            alias_version=row["alias_version"],
            quantity=Decimal(str(row["quantity"])) if row["quantity"] is not None else None,
            unit_price=Decimal(str(row["unit_price"])) if row["unit_price"] is not None else None,
            amount=Decimal(str(row["amount"])) if row["amount"] is not None else None,
            currency=row["currency"],
            payment_term=payment,
            evidence=row["evidence"],
            confidence=row["confidence"] or 0.0,
            issues=issues,
        )


# ---------------------------------------------------------------------------
# Operations / Review / Audit
# ---------------------------------------------------------------------------


class OperationRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def save(self, run_id: str, record: OperationRecord) -> None:
        output_json = record.output.model_dump_json() if record.output else "{}"

        # PENDING_REVIEW 的输入费用行可能还没入账，避免 FK 失败
        charge_id = record.charge_id
        if charge_id:
            exists = self.conn.execute(
                "SELECT 1 FROM charges WHERE charge_id = ?", (charge_id,)
            ).fetchone()
            if not exists:
                charge_id = None

        self.conn.execute(
            """
            INSERT OR REPLACE INTO operations (
              operation_id, run_id, charge_id, matched_bill_id, operation,
              changed_fields_json, output_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.operation_id,
                run_id,
                charge_id,
                record.matched_bill_id,
                record.operation.value,
                _json(record.changed_fields),
                output_json,
                _now(),
            ),
        )
        self.conn.commit()


class ReviewRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def enqueue(self, run_id: str | None, item: ReviewItem) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO review_items (
              review_id, run_id, charge_id, reason, candidates_json,
              status, decision_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.review_id,
                run_id,
                item.charge_id,
                item.reason,
                _json(item.candidates),
                item.status,
                _json(item.decision),
                _now(),
            ),
        )
        self.conn.commit()

    def list_pending(self, run_id: str | None = None) -> list[ReviewItem]:
        if run_id:
            rows = self.conn.execute(
                """
                SELECT * FROM review_items
                WHERE status = 'pending' AND run_id = ?
                ORDER BY created_at
                """,
                (run_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM review_items WHERE status = 'pending' ORDER BY created_at"
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def resolve(self, review_id: str, status: str, decision: dict | None = None) -> None:
        self.conn.execute(
            """
            UPDATE review_items
            SET status = ?, decision_json = ?, resolved_at = ?
            WHERE review_id = ?
            """,
            (status, _json(decision or {}), _now(), review_id),
        )
        self.conn.commit()

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> ReviewItem:
        raw_candidates = _loads(row["candidates_json"])
        candidates = (
            raw_candidates
            if isinstance(raw_candidates, list)
            else raw_candidates.get("candidates", [])
        )
        return ReviewItem(
            review_id=row["review_id"],
            charge_id=row["charge_id"],
            reason=row["reason"],
            candidates=candidates,
            status=row["status"],
            decision=_loads(row["decision_json"]),
        )


class AuditRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def log(
        self,
        run_id: str | None,
        event_type: str,
        detail: dict | None = None,
        charge_id: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO audit_events (run_id, charge_id, event_type, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, charge_id, event_type, _json(detail or {}), _now()),
        )
        self.conn.commit()


__all__ = [
    "AssetRepository",
    "RunRepository",
    "BillRepository",
    "SqliteBillRepository",
    "OperationRepository",
    "ReviewRepository",
    "AuditRepository",
    "MatchResult",
]
