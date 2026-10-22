"""FastAPI 服务 + Swagger。

启动：
    uvicorn shipping_agent.api:app --reload --port 8000
Swagger:
    http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .asset_service import build_asset_service
from .config import load_settings
from .db.connection import init_db
from .db.repositories import (
    OperationRepository,
    ReviewRepository,
    SqliteBillRepository,
)
from .decide import DecisionEngine
from .extractors.context import build_extraction_context
from .extractors.llm import LLMExtractor, build_llm_extractor
from .extractors.merge import merge_extraction
from .extractors.record import build_record
from .extractors.rules import extract_with_rules
from .graph.checkpoints import sqlite_checkpointer
from .graph.context import GraphContext
from .graph.pipeline import build_pipeline
from .loaders.router import route_path
from .models import ExtractionResult, InternalBillRecord, RawDocument
from .ocr.client import OCRClient
from .rules.registry import load_rules
from .validation.rules import validate_record

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = Path(__file__).resolve().parent / "db" / "schema.sql"
UPLOAD_DIR = ROOT / "data" / "uploads"
OUTPUT_DIR = ROOT / "data" / "output"


def _swagger_ui_dir() -> Path:
    """本地自带的 Swagger UI 资源，避免 jsdelivr 被墙导致 /docs 空白。"""
    import swagger_ui_bundle

    vendor = Path(swagger_ui_bundle.__file__).parent / "vendor"
    candidates = sorted(vendor.glob("swagger-ui-*"))
    if not candidates:
        raise RuntimeError("swagger-ui-bundle 资源目录不存在")
    return candidates[-1]


SWAGGER_UI_DIR = _swagger_ui_dir()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = load_settings()
    settings.ensure_directories()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = init_db(settings.db_path, SCHEMA)
    conn.close()
    yield


app = FastAPI(
    title="Shipping Bill Agent API",
    version=__version__,
    description="多格式航运账单解析、抽取、匹配与账单决策",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)

app.mount(
    "/static/swagger-ui",
    StaticFiles(directory=str(SWAGGER_UI_DIR)),
    name="swagger-ui",
)


def _patch_file_schemas(node) -> None:
    """把 OpenAPI 3.1 的 contentMediaType 改成 3.0 的 format: binary。

    否则 Swagger UI 4 会把 UploadFile 渲染成文本框，而不是文件选择框。
    """
    if isinstance(node, dict):
        if node.get("contentMediaType") == "application/octet-stream":
            node.pop("contentMediaType", None)
            node.pop("contentEncoding", None)
            node["type"] = "string"
            node["format"] = "binary"
        for value in node.values():
            _patch_file_schemas(value)
    elif isinstance(node, list):
        for item in node:
            _patch_file_schemas(item)


def custom_openapi():
    """Swagger UI 4.x 不支持 OpenAPI 3.1，这里降到 3.0.3。"""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        openapi_version="3.0.3",
    )
    _patch_file_schemas(schema)
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi  # type: ignore[method-assign]


@app.get("/docs", include_in_schema=False)
def swagger_docs() -> HTMLResponse:
    html = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Shipping Bill Agent API - Swagger UI</title>
  <link rel="stylesheet" href="/static/swagger-ui/swagger-ui.css" />
  <link rel="icon" href="/static/swagger-ui/favicon-32x32.png" />
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="/static/swagger-ui/swagger-ui-bundle.js"></script>
  <script src="/static/swagger-ui/swagger-ui-standalone-preset.js"></script>
  <script>
    window.ui = SwaggerUIBundle({
      url: "/openapi.json",
      dom_id: "#swagger-ui",
      deepLinking: true,
      presets: [
        SwaggerUIBundle.presets.apis,
        SwaggerUIStandalonePreset
      ],
      layout: "BaseLayout"
    });
  </script>
</body>
</html>"""
    return HTMLResponse(html)


class ResumeRequest(BaseModel):
    thread_id: str
    decisions: dict[str, dict] = {}
    auto_approve: bool = False


def _safe_filename(name: str | None) -> str:
    value = (name or "upload").replace("\xa0", " ").strip()

    # multipart 的 filename 有时被按 latin-1 解码：
    # 中文 UTF-8 字节 -> latin-1 字符串（GBK 场景）或 UTF-8 场景。
    try:
        raw = value.encode("latin-1")
    except UnicodeEncodeError:
        raw = None
    if raw is not None:
        for encoding in ("utf-8", "gb18030"):
            try:
                value = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue

    value = re.sub(r'[\\/:*?"<>|]+', "_", value)
    return value or "upload"


async def _save_upload(file: UploadFile) -> Path:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空")
    path = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{_safe_filename(file.filename)}"
    path.write_bytes(data)
    return path


def _iter_documents(doc: RawDocument):
    yield doc
    for child in doc.attachments:
        yield from _iter_documents(child)


def _doc_summary(doc: RawDocument) -> dict:
    return {
        "doc_id": doc.doc_id,
        "source_type": doc.source_type.value,
        "file_name": doc.metadata.get("file_name"),
        "text_chars": len(doc.text or ""),
        "table_groups": len(doc.tables),
        "ocr_used": doc.metadata.get("ocr_used", False),
        "ocr_error": doc.metadata.get("ocr_error"),
        "notes": doc.parse_notes,
        "attachments": [_doc_summary(child) for child in doc.attachments],
    }


def _extract_records(
    path: Path,
    *,
    use_ocr: bool,
    use_llm: bool,
    max_depth: int,
) -> list[dict]:
    settings = load_settings()
    settings.ensure_directories()
    conn = init_db(settings.db_path, SCHEMA)
    try:
        service = build_asset_service(settings, conn)
        rules = load_rules(settings.project_root / "config")
        ocr_client = OCRClient.from_settings(settings) if use_ocr else None
        llm_extractor = (
            build_llm_extractor(settings)
            if use_llm
            else LLMExtractor(None)
        )
        root_doc = route_path(
            path,
            service,
            max_depth=max_depth,
            ocr_client=ocr_client,
            use_ocr=use_ocr,
        )

        results: list[dict] = []
        for index, doc in enumerate(_iter_documents(root_doc)):
            extraction_context = build_extraction_context(doc, rules)
            rule_result = extract_with_rules(doc, rules)

            llm_result = ExtractionResult()
            llm_error = None
            if llm_extractor.available:
                try:
                    llm_result = llm_extractor.extract(extraction_context)
                except Exception as exc:
                    llm_error = f"{type(exc).__name__}: {exc}"

            llm_used = llm_extractor.available and llm_error is None
            merged = merge_extraction(
                rule_result, llm_result, llm_used=llm_used
            )
            record = build_record(
                doc,
                merged.result,
                rules,
                settings,
                index=index,
                llm_used=llm_used,
                llm_confidence=merged.llm_confidence,
                extraction_conflicts=merged.conflicts,
            )
            validate_record(record, rules, settings, doc)

            results.append(
                {
                    "doc_id": doc.doc_id,
                    "source_type": doc.source_type.value,
                    "file_name": doc.metadata.get("file_name"),
                    "record": record.model_dump(mode="json"),
                    "conflicts": merged.conflicts,
                    "llm_used": llm_used,
                    "llm_error": llm_error,
                }
            )
        return results
    finally:
        conn.close()


def _graph_context(
    *,
    thread_id: str,
    use_ocr: bool,
    use_llm: bool,
    max_depth: int,
) -> tuple[GraphContext, Any]:
    settings = load_settings()
    settings.ensure_directories()
    conn = init_db(settings.db_path, SCHEMA)
    service = build_asset_service(settings, conn)
    rules = load_rules(settings.project_root / "config")
    ocr_client = OCRClient.from_settings(settings) if use_ocr else None
    llm_extractor = (
        build_llm_extractor(settings) if use_llm else LLMExtractor(None)
    )
    engine = DecisionEngine(
        bill_repository=SqliteBillRepository(conn),
        operation_repository=OperationRepository(conn),
        review_repository=ReviewRepository(conn),
        settings=settings,
    )
    context = GraphContext(
        settings=settings,
        rules=rules,
        asset_service=service,
        ocr_client=ocr_client,
        llm_extractor=llm_extractor,
        engine=engine,
        conn=conn,
        output_path=OUTPUT_DIR / f"{thread_id}.json",
        summary_path=OUTPUT_DIR / f"{thread_id}_summary.json",
        use_ocr=use_ocr,
        max_depth=max_depth,
    )
    return context, conn


@app.get("/health")
def health() -> dict:
    settings = load_settings()
    rules = load_rules(settings.project_root / "config")
    return {
        "status": "ok",
        "version": __version__,
        "llm_configured": settings.is_llm_configured,
        "llm_model": settings.llm_model,
        "llm_base_url": settings.llm_base_url,
        "accounting_entity": settings.accounting_entity,
        "alias_version": rules.alias_version,
    }


@app.post("/parse")
async def parse_document(
    file: UploadFile = File(...),
    use_ocr: bool = Form(False),
    max_depth: int = Form(3),
) -> dict:
    path = await _save_upload(file)
    settings = load_settings()
    settings.ensure_directories()
    conn = init_db(settings.db_path, SCHEMA)
    try:
        service = build_asset_service(settings, conn)
        ocr_client = OCRClient.from_settings(settings) if use_ocr else None
        doc = route_path(
            path,
            service,
            max_depth=max_depth,
            ocr_client=ocr_client,
            use_ocr=use_ocr,
        )
        return {"document": _doc_summary(doc)}
    finally:
        conn.close()


@app.post("/extract")
async def extract_document(
    file: UploadFile = File(...),
    use_ocr: bool = Form(False),
    use_llm: bool = Form(True),
    max_depth: int = Form(3),
) -> dict:
    path = await _save_upload(file)
    return {
        "documents": _extract_records(
            path,
            use_ocr=use_ocr,
            use_llm=use_llm,
            max_depth=max_depth,
        )
    }


@app.post("/decide")
async def decide_document(
    file: UploadFile = File(...),
    use_ocr: bool = Form(False),
    use_llm: bool = Form(True),
    max_depth: int = Form(3),
    include_operations: bool = Form(False),
) -> dict:
    path = await _save_upload(file)
    settings = load_settings()
    settings.ensure_directories()
    conn = init_db(settings.db_path, SCHEMA)
    try:
        service = build_asset_service(settings, conn)
        rules = load_rules(settings.project_root / "config")
        ocr_client = OCRClient.from_settings(settings) if use_ocr else None
        llm_extractor = (
            build_llm_extractor(settings)
            if use_llm
            else LLMExtractor(None)
        )
        engine = DecisionEngine(
            bill_repository=SqliteBillRepository(conn),
            operation_repository=OperationRepository(conn),
            review_repository=ReviewRepository(conn),
            settings=settings,
        )
        run_id = f"API-{uuid.uuid4().hex[:8]}"
        root_doc = route_path(
            path,
            service,
            max_depth=max_depth,
            ocr_client=ocr_client,
            use_ocr=use_ocr,
        )
        operations: list[dict] = []
        outputs: list[dict] = []
        for index, doc in enumerate(_iter_documents(root_doc)):
            extraction_context = build_extraction_context(doc, rules)
            rule_result = extract_with_rules(doc, rules)
            llm_result = ExtractionResult()
            llm_error = None
            if llm_extractor.available:
                try:
                    llm_result = llm_extractor.extract(extraction_context)
                except Exception as exc:
                    llm_error = f"{type(exc).__name__}: {exc}"
            llm_used = llm_extractor.available and llm_error is None
            merged = merge_extraction(
                rule_result, llm_result, llm_used=llm_used
            )
            record = build_record(
                doc,
                merged.result,
                rules,
                settings,
                index=index,
                llm_used=llm_used,
                llm_confidence=merged.llm_confidence,
                extraction_conflicts=merged.conflicts,
            )
            validate_record(record, rules, settings, doc)
            decision = engine.decide(record, doc, run_id)
            for operation in decision.operations:
                payload = (
                    operation.output.model_dump(mode="json")
                    if operation.output
                    else None
                )
                operations.append(
                    {
                        "operation_id": operation.operation_id,
                        "operation": operation.operation.value,
                        "matched_bill_id": operation.matched_bill_id,
                        "changed_fields": operation.changed_fields,
                    }
                )
                if payload is not None:
                    outputs.append(payload)
        counts = Counter(op["operation"] for op in operations)
        result = {
            "run_id": run_id,
            "operation_counts": dict(counts),
            "outputs": outputs,
        }
        if include_operations:
            result["operations"] = operations
        return result
    finally:
        conn.close()


@app.post("/graph/run")
async def graph_run(
    file: UploadFile = File(...),
    use_ocr: bool = Form(False),
    use_llm: bool = Form(True),
    auto_approve_pending: bool = Form(False),
    max_depth: int = Form(3),
    include_operations: bool = Form(False),
) -> dict:
    path = await _save_upload(file)
    thread_id = f"API-{uuid.uuid4().hex[:8]}"
    context, conn = _graph_context(
        thread_id=thread_id,
        use_ocr=use_ocr,
        use_llm=use_llm,
        max_depth=max_depth,
    )
    state = {
        "run_id": thread_id,
        "thread_id": thread_id,
        "input_paths": [str(path)],
        "auto_approve_pending": auto_approve_pending,
        "trace": [],
        "errors": [],
    }
    try:
        with sqlite_checkpointer(context.settings.db_path) as saver:
            graph = build_pipeline(context, checkpointer=saver)
            result = graph.invoke(
                state, config={"configurable": {"thread_id": thread_id}}
            )
        interrupted = bool(result.get("__interrupt__"))
        pending_items = context.engine.review_repository.list_pending(
            run_id=thread_id
        )
        response = {
            "thread_id": thread_id,
            "paused": interrupted,
            "pending_count": len(pending_items),
            "outputs": result.get("outputs", []),
            "output_path": str(context.output_path),
        }
        if interrupted:
            response["pending"] = [
                item.model_dump(mode="json") for item in pending_items
            ]
        if include_operations:
            response["operations"] = result.get("operations", [])
        return response
    finally:
        conn.close()


@app.post("/graph/resume")
def graph_resume(request: ResumeRequest) -> dict:
    context, conn = _graph_context(
        thread_id=request.thread_id,
        use_ocr=False,
        use_llm=True,
        max_depth=3,
    )
    try:
        if request.auto_approve:
            decisions = {
                item.charge_id: {"action": "accept"}
                for item in context.engine.review_repository.list_pending(
                    run_id=request.thread_id
                )
                if item.charge_id
            }
            resume_value = {"decisions": decisions}
        else:
            resume_value = {"decisions": request.decisions}

        from langgraph.types import Command

        with sqlite_checkpointer(context.settings.db_path) as saver:
            graph = build_pipeline(context, checkpointer=saver)
            result = graph.invoke(
                Command(resume=resume_value),
                config={"configurable": {"thread_id": request.thread_id}},
            )
        return {
            "thread_id": request.thread_id,
            "paused": bool(result.get("__interrupt__")),
            "outputs": result.get("outputs", []),
            "output_path": str(context.output_path),
        }
    finally:
        conn.close()


@app.get("/review")
def list_review(run_id: str | None = None) -> dict:
    settings = load_settings()
    settings.ensure_directories()
    conn = init_db(settings.db_path, SCHEMA)
    try:
        items = ReviewRepository(conn).list_pending(run_id=run_id)
        return {
            "run_id": run_id,
            "items": [item.model_dump(mode="json") for item in items],
            "count": len(items),
        }
    finally:
        conn.close()


@app.get("/output/{thread_id}")
def get_output(thread_id: str):
    path = OUTPUT_DIR / f"{thread_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="output not found")
    return FileResponse(path, media_type="application/json", filename=path.name)
