# 存储与资产库

## 1. 三层

| 层 | 位置 | 内容 |
|---|---|---|
| 输出契约 | `data/output/output.json` | 严格按题目 schema |
| 内部账本 | `data/agent.db` | 单据、费用、匹配、操作、复核 |
| 资产库 | `data/assets/` 或 OSS | 原始文件、OCR 响应、抽取结果 |

## 2. SQLite 表

- `runs`
- `assets`
- `documents`
- `containers`
- `charges`
- `existing_bills`
- `operations`
- `validation_issues`
- `review_items`
- `audit_events`
- `schema_version`

核心字段：

- `charges.raw_name`：原始费用名
- `charges.std_name`：标准化费用名
- `charges.alias_version`：费用字典版本
- `documents.bl_no` / `booking_no`：匹配键
- `containers.container_no`：L4 匹配
- `operations.output_json`：该条输出快照
- `review_items`：人工复核队列

## 3. 资产库

```text
data/assets/{sha[:2]}/{sha}/
├─ original.pdf / original.xlsx / original.eml / ...
├─ meta.json
├─ ocr.json
├─ extraction.json
└─ output.json
```

OSS 目录建议：

```text
s3://bucket/tenant=pltp/dt=2026-10-21/{sha256}/...
```

## 4. OCR URL

- OCR 只接受公网 URL；
- 本地开发：静态 HTTP + 隧道；
- 生产：私有 OSS + 签名 URL；
- 普通百度网盘分享页不是直链；
- `ASSET_PUBLIC_BASE_URL` / `OCR_PUBLIC_BASE_URL` 未配置时，`LocalAssetStore.url()` 明确报错，不静默失败。

## 5. 匹配 SQL

L1：`reference_no + std_name`
L2：`bl_no + std_name + currency`
L3：`booking_no + std_name + currency`
L4：`container_no + issuer + recipient + document_date + amount`

见 `src/shipping_agent/db/repositories.py` 的 `list_candidates()`。
