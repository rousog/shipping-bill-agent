PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
  version    INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  run_id         TEXT PRIMARY KEY,
  input_path     TEXT,
  started_at     TEXT,
  finished_at    TEXT,
  config_hash    TEXT,
  alias_version  TEXT,
  prompt_version TEXT,
  model          TEXT,
  status         TEXT
);

CREATE TABLE IF NOT EXISTS assets (
  asset_id        TEXT PRIMARY KEY,
  sha256          TEXT NOT NULL UNIQUE,
  file_name       TEXT NOT NULL,
  mime_type       TEXT,
  size_bytes      INTEGER,
  storage_backend TEXT NOT NULL,
  storage_key     TEXT NOT NULL,
  parent_asset_id TEXT REFERENCES assets(asset_id),
  source_type     TEXT,
  is_inline       INTEGER DEFAULT 0,
  created_at      TEXT NOT NULL,
  metadata_json   TEXT
);

CREATE TABLE IF NOT EXISTS documents (
  document_id    TEXT PRIMARY KEY,
  asset_id       TEXT REFERENCES assets(asset_id),
  document_type  TEXT,
  reference_no   TEXT,
  document_date  TEXT,
  issuer         TEXT,
  recipient      TEXT,
  shipper        TEXT,
  consignee      TEXT,
  bl_no          TEXT,
  hbl_no         TEXT,
  booking_no     TEXT,
  invoice_no     TEXT,
  total_amount   REAL,
  total_currency TEXT,
  confidence     REAL,
  status         TEXT,
  raw_text       TEXT,
  created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS containers (
  container_id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id  TEXT NOT NULL REFERENCES documents(document_id),
  container_no TEXT NOT NULL,
  seal_no      TEXT,
  size_type    TEXT
);

CREATE TABLE IF NOT EXISTS charges (
  charge_id     TEXT PRIMARY KEY,
  document_id   TEXT NOT NULL REFERENCES documents(document_id),
  line_no       INTEGER,
  raw_name      TEXT,
  std_name      TEXT,
  alias_version TEXT,
  quantity      REAL,
  unit_price    REAL,
  amount        REAL,
  currency      TEXT,
  payment_term  TEXT,
  evidence      TEXT,
  confidence    REAL,
  issues_json   TEXT,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS existing_bills (
  bill_id           TEXT PRIMARY KEY,
  version           INTEGER NOT NULL DEFAULT 1,
  document_id       TEXT NOT NULL REFERENCES documents(document_id),
  accounting_entity TEXT,
  posting_type      TEXT,
  status            TEXT,
  updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operations (
  operation_id        TEXT PRIMARY KEY,
  run_id              TEXT NOT NULL,
  charge_id           TEXT REFERENCES charges(charge_id),
  matched_bill_id     TEXT REFERENCES existing_bills(bill_id),
  operation           TEXT NOT NULL,
  changed_fields_json TEXT,
  output_json         TEXT NOT NULL,
  created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_issues (
  issue_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  charge_id  TEXT REFERENCES charges(charge_id),
  code       TEXT NOT NULL,
  severity   TEXT NOT NULL,
  message    TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_items (
  review_id      TEXT PRIMARY KEY,
  run_id         TEXT,
  charge_id      TEXT,
  reason         TEXT NOT NULL,
  candidates_json TEXT,
  status         TEXT NOT NULL DEFAULT 'pending',
  decision_json  TEXT,
  created_at     TEXT NOT NULL,
  resolved_at    TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
  event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      TEXT,
  charge_id   TEXT,
  event_type  TEXT NOT NULL,
  detail_json TEXT,
  created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_ref      ON documents(reference_no);
CREATE INDEX IF NOT EXISTS idx_documents_bl       ON documents(bl_no);
CREATE INDEX IF NOT EXISTS idx_documents_booking  ON documents(booking_no);
CREATE INDEX IF NOT EXISTS idx_charges_std        ON charges(std_name, currency);
CREATE INDEX IF NOT EXISTS idx_containers_no      ON containers(container_no);
CREATE INDEX IF NOT EXISTS idx_operations_run     ON operations(run_id);
CREATE INDEX IF NOT EXISTS idx_review_status      ON review_items(status);
