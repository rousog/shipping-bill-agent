# 架构说明

## 1. 目标

把多格式航运单据转成结构化账单变更指令：

```text
EML / PDF / 图片 / Excel / Word
  → 解析
  → 抽取
  → 标准化 / 校验
  → 匹配已有账单
  → CREATE / UPDATE / NO_CHANGE / PENDING_REVIEW
  → 严格 JSON
```

## 2. 分层

```text
┌──────────────────────────────────────────────┐
│ 输出层：output.json（题目 schema）            │
├──────────────────────────────────────────────┤
│ 决策层：LangGraph 工作流 + 纯 Python 规则     │
├──────────────────────────────────────────────┤
│ 抽取层：规则 + LangChain LLM 结构化输出       │
├──────────────────────────────────────────────┤
│ 解析层：EML / PDF·图片 / Excel / Word         │
├──────────────────────────────────────────────┤
│ 资产层：AssetStore（本地 / OSS）              │
├──────────────────────────────────────────────┤
│ 持久层：SQLite（内部账本）                    │
└──────────────────────────────────────────────┘
```

## 3. 关键决策

### 3.1 workflow agent，而不是 ReAct agent

- LangGraph 负责控制流，路由确定性；
- LLM 只在抽取节点做语义理解；
- 财务场景要可审计、可复现，不需要模型自由规划。

### 3.2 LLM 提议 AR/AP，代码裁决

- 抽取 prompt 要求 LLM 输出 `posting_type_candidate` 和理由；
- `ar_ap` 节点用记账主体、issuer、recipient 规则复核；
- 二者一致 → 采用；
- 不一致 → `PENDING_REVIEW`；
- 主体不明 → 落库 + 人工复核。

### 3.3 三层存储

- `output.json`：给财务系统的契约；
- SQLite：内部账本（原始费用名、B/L、箱号、置信度、操作历史）；
- AssetStore：原始文件 + OCR 响应 + 抽取结果。

### 3.4 Credit Note

- 金额输出负数；
- 同时保留 `document_type=Credit Note`；
- 财务可据此核验方向。

### 3.5 EML 主体不明

- 先写 SQLite；
- 进 `review_items`；
- 人工接受 → 继续输出；
- 人工拒绝 → 只留库，不输出；
- 任何情况都不丢记录。

## 4. 目录

```text
src/shipping_agent/
├─ graph/           # LangGraph 状态、节点、路由、checkpoint
├─ loaders/         # 五个 loader
├─ ocr/             # OCR client + URL 暴露
├─ extractors/      # rules / llm / merge
├─ normalization/   # charge / currency / payment term
├─ validation/      # hard/soft rules + confidence
├─ matching/        # L1–L4 + merge
├─ db/              # schema / connection / repositories
├─ assets.py        # LocalAssetStore / OSS 接口
├─ asset_service.py # AssetStore + SQLite assets 同步
├─ models.py        # Pydantic 模型
├─ audit.py         # audit_trail 模板（Phase 3）
├─ output.py        # 输出投影（Phase 3）
├─ api.py           # FastAPI + Swagger
└─ cli.py
```

## 5. 当前状态

Phase 0：骨架、模型、SQLite、AssetStore、最小 LangGraph 已完成。
Phase 1：EML（递归 + banner 过滤）、PDF/图片（文本 + OCR）、Excel（表头识别）、Word（docx + doc 转换）、OCR client、AssetService 已完成。
Phase 2：规则注册表（YAML 词典 + 版本）、ExtractionContext、规则抽取、LangChain Chain、双通道合并、标准化、硬/软校验已完成。
Phase 3：L1–L4 匹配、金额 tie-break、增量合并/版本、AR/AP、严格 OutputRecord、决策持久化已完成。
Phase 4：完整 LangGraph（ingest → extract → decide → review → output）、`interrupt/resume`、SQLite checkpoint 已完成。
Phase 5：收尾测试、文档和交付。
