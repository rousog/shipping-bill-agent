"""命令行入口。

Phase 0 支持：
  python -m shipping_agent.cli init-db
  python -m shipping_agent.cli version
  python -m shipping_agent.cli run --input <file> [--input <file> ...]
  python -m shipping_agent.cli review --list
"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from . import __version__
from .asset_service import build_asset_service
from .config import load_settings
from .db.connection import connect, init_db
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
from .graph.build import build_graph
from .graph.checkpoints import sqlite_checkpointer
from .graph.context import GraphContext
from .graph.pipeline import build_pipeline
from .loaders.router import route_path
from .models import ExtractionResult
from .ocr.client import OCRClient
from .rules.registry import load_rules
from .validation.rules import validate_record

SCHEMA_PATH = Path(__file__).resolve().parent / "db" / "schema.sql"


def cmd_version(_: argparse.Namespace) -> int:
    print(f"shipping-bill-agent {__version__}")
    return 0


def cmd_init_db(_: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_directories()
    conn = init_db(settings.db_path, SCHEMA_PATH)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    conn.close()
    print(f"DB initialized: {settings.db_path}")
    print("Tables: " + ", ".join(row["name"] for row in tables))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_directories()

    paths = [str(Path(p).resolve()) for p in args.input]
    run_id = args.run_id or f"RUN-{uuid.uuid4().hex[:12]}"

    state = {
        "run_id": run_id,
        "thread_id": run_id,
        "input_paths": paths,
        "pending_files": paths,
        "processed_files": [],
        "assets": [],
        "documents": [],
        "records": [],
        "issues": [],
        "matches": [],
        "operations": [],
        "outputs": [],
        "review_queue": [],
        "review_decisions": [],
        "retry_counts": {},
        "errors": [],
        "trace": [],
    }

    if args.checkpoint:
        with sqlite_checkpointer(settings.db_path) as saver:
            graph = build_graph(checkpointer=saver)
            result = graph.invoke(
                state, config={"configurable": {"thread_id": run_id}}
            )
    else:
        graph = build_graph()
        result = graph.invoke(state)

    summary = {
        "run_id": run_id,
        "processed_files": result.get("processed_files", []),
        "outputs": len(result.get("outputs", [])),
        "review_queue": len(result.get("review_queue", [])),
        "errors": result.get("errors", []),
        "trace": result.get("trace", []),
    }

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"Summary written: {out_path}")
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


def _doc_summary(doc) -> dict:
    return {
        "doc_id": doc.doc_id,
        "source_type": doc.source_type.value,
        "text_chars": len(doc.text or ""),
        "table_groups": len(doc.tables),
        "ocr_used": doc.metadata.get("ocr_used", False),
        "ocr_error": doc.metadata.get("ocr_error"),
        "notes": doc.parse_notes,
        "metadata": {k: v for k, v in doc.metadata.items() if k != "ocr"},
        "attachments": [_doc_summary(child) for child in doc.attachments],
    }


def cmd_parse(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_directories()
    conn = init_db(settings.db_path, SCHEMA_PATH)
    service = build_asset_service(settings, conn)

    ocr_client = None
    if not args.no_ocr:
        ocr_client = OCRClient.from_settings(settings)
        if not (settings.asset_public_base_url or settings.ocr_public_base_url):
            print(
                "提示：未配置 ASSET_PUBLIC_BASE_URL / OCR_PUBLIC_BASE_URL，"
                "OCR 会失败，但本地文本抽取仍可继续。可用 --no-ocr 关闭 OCR。"
            )

    results: list[dict] = []
    for input_path in args.input:
        try:
            doc = route_path(
                input_path,
                service,
                max_depth=args.max_depth,
                ocr_client=ocr_client,
                use_ocr=not args.no_ocr,
            )
            results.append({"input": input_path, "document": _doc_summary(doc)})
        except Exception as exc:
            results.append(
                {"input": input_path, "error": f"{type(exc).__name__}: {exc}"}
            )

    conn.close()
    payload = {"documents": results}
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"Parse summary written: {out_path}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


def _iter_documents(doc):
    yield doc
    for child in doc.attachments:
        yield from _iter_documents(child)


def _build_graph_context(args, settings, conn) -> GraphContext:
    service = build_asset_service(settings, conn)
    rules = load_rules(settings.project_root / "config")
    ocr_client = None if args.no_ocr else OCRClient.from_settings(settings)
    llm_extractor = (
        LLMExtractor(None) if args.no_llm else build_llm_extractor(settings)
    )
    engine = DecisionEngine(
        bill_repository=SqliteBillRepository(conn),
        operation_repository=OperationRepository(conn),
        review_repository=ReviewRepository(conn),
        settings=settings,
    )
    output_path = (
        Path(args.out)
        if args.out
        else settings.project_root / "data" / "output" / "output.json"
    )
    summary_path = (
        Path(args.summary_out)
        if args.summary_out
        else settings.project_root / "data" / "output" / "graph_summary.json"
    )
    return GraphContext(
        settings=settings,
        rules=rules,
        asset_service=service,
        ocr_client=ocr_client,
        llm_extractor=llm_extractor,
        engine=engine,
        conn=conn,
        output_path=output_path,
        summary_path=summary_path,
        use_ocr=not args.no_ocr,
        max_depth=args.max_depth,
    )


def _print_graph_result(result: dict, thread_id: str, output_path: Path) -> None:
    interrupt_payload = result.get("__interrupt__")
    if interrupt_payload:
        if isinstance(interrupt_payload, list):
            printable = [
                getattr(item, "value", item) for item in interrupt_payload
            ]
        else:
            printable = interrupt_payload
        print("Graph paused for human review.")
        print(f"thread_id: {thread_id}")
        print(json.dumps(printable, ensure_ascii=False, indent=2, default=str))
        print(
            f"Resume: python -m shipping_agent.cli resume "
            f"--thread-id {thread_id} --decisions-file decisions.json"
        )
    else:
        summary = {
            "thread_id": thread_id,
            "documents": len(result.get("documents", [])),
            "records": len(result.get("records", [])),
            "operations": len(result.get("operations", [])),
            "outputs": len(result.get("outputs", [])),
            "errors": result.get("errors", []),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        print(f"Output: {output_path}")


def cmd_graph(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_directories()
    conn = init_db(settings.db_path, SCHEMA_PATH)
    context = _build_graph_context(args, settings, conn)

    thread_id = args.thread_id or f"RUN-{uuid.uuid4().hex[:12]}"
    state = {
        "run_id": thread_id,
        "thread_id": thread_id,
        "input_paths": [str(Path(p).resolve()) for p in args.input],
        "auto_approve_pending": args.auto_approve_pending,
        "trace": [],
        "errors": [],
    }

    with sqlite_checkpointer(settings.db_path) as saver:
        graph = build_pipeline(context, checkpointer=saver)
        result = graph.invoke(
            state, config={"configurable": {"thread_id": thread_id}}
        )

    _print_graph_result(result, thread_id, context.output_path)
    conn.close()
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_directories()
    conn = init_db(settings.db_path, SCHEMA_PATH)
    context = _build_graph_context(args, settings, conn)

    if args.auto_approve:
        pending = context.engine.review_repository.list_pending(
            run_id=args.thread_id
        )
        decisions = {
            item.charge_id: {"action": "accept"}
            for item in pending
            if item.charge_id
        }
        resume_value = {"decisions": decisions}
    elif args.decisions_file:
        resume_value = json.loads(
            Path(args.decisions_file).read_text(encoding="utf-8")
        )
    else:
        print("需要 --decisions-file 或 --auto-approve")
        conn.close()
        return 2

    from langgraph.types import Command

    with sqlite_checkpointer(settings.db_path) as saver:
        graph = build_pipeline(context, checkpointer=saver)
        result = graph.invoke(
            Command(resume=resume_value),
            config={"configurable": {"thread_id": args.thread_id}},
        )

    _print_graph_result(result, args.thread_id, context.output_path)
    conn.close()
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    """解析 + 规则抽取 + LLM 抽取 + 合并 + 标准化 + 校验。"""
    settings = load_settings()
    settings.ensure_directories()
    conn = init_db(settings.db_path, SCHEMA_PATH)
    service = build_asset_service(settings, conn)
    rules = load_rules(settings.project_root / "config")

    ocr_client = None
    if not args.no_ocr:
        ocr_client = OCRClient.from_settings(settings)
    llm_extractor = (
        LLMExtractor(None) if args.no_llm else build_llm_extractor(settings)
    )

    results: list[dict] = []
    for input_path in args.input:
        try:
            root_doc = route_path(
                input_path,
                service,
                max_depth=args.max_depth,
                ocr_client=ocr_client,
                use_ocr=not args.no_ocr,
            )
        except Exception as exc:
            results.append(
                {"input": input_path, "error": f"{type(exc).__name__}: {exc}"}
            )
            continue

        documents: list[dict] = []
        for index, doc in enumerate(_iter_documents(root_doc)):
            context = build_extraction_context(doc, rules)
            rule_result = extract_with_rules(doc, rules)

            llm_result = ExtractionResult()
            llm_error = None
            if llm_extractor.available:
                try:
                    llm_result = llm_extractor.extract(context)
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

            documents.append(
                {
                    "doc_id": doc.doc_id,
                    "source_type": doc.source_type.value,
                    "file_name": doc.metadata.get("file_name"),
                    "record": record.model_dump(mode="json"),
                    "conflicts": merged.conflicts,
                    "llm_used": llm_extractor.available and llm_error is None,
                    "llm_error": llm_error,
                }
            )
        results.append({"input": input_path, "documents": documents})

    conn.close()
    payload = {
        "alias_version": rules.alias_version,
        "llm_available": llm_extractor.available,
        "results": results,
    }
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"Extraction result written: {out_path}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    """解析 + 抽取 + 匹配 + 合并 + AR/AP + 输出。"""
    settings = load_settings()
    settings.ensure_directories()
    conn = init_db(settings.db_path, SCHEMA_PATH)
    service = build_asset_service(settings, conn)
    rules = load_rules(settings.project_root / "config")

    ocr_client = None
    if not args.no_ocr:
        ocr_client = OCRClient.from_settings(settings)
    llm_extractor = (
        LLMExtractor(None) if args.no_llm else build_llm_extractor(settings)
    )

    engine = DecisionEngine(
        bill_repository=SqliteBillRepository(conn),
        operation_repository=OperationRepository(conn),
        review_repository=ReviewRepository(conn),
        settings=settings,
    )
    run_id = args.run_id or f"RUN-{uuid.uuid4().hex[:12]}"

    all_operations = []
    all_outputs: list[dict] = []
    for input_path in args.input:
        try:
            root_doc = route_path(
                input_path,
                service,
                max_depth=args.max_depth,
                ocr_client=ocr_client,
                use_ocr=not args.no_ocr,
            )
        except Exception as exc:
            all_operations.append(
                {
                    "input": input_path,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        for index, doc in enumerate(_iter_documents(root_doc)):
            context = build_extraction_context(doc, rules)
            rule_result = extract_with_rules(doc, rules)

            llm_result = ExtractionResult()
            llm_error = None
            if llm_extractor.available:
                try:
                    llm_result = llm_extractor.extract(context)
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

            for op in decision.operations:
                if op.output is not None:
                    all_outputs.append(op.output.model_dump(mode="json"))

            all_operations.append(
                {
                    "input": input_path,
                    "doc_id": doc.doc_id,
                    "source_type": doc.source_type.value,
                    "conflicts": merged.conflicts,
                    "llm_used": llm_extractor.available and llm_error is None,
                    "llm_error": llm_error,
                    "operations": [
                        {
                            "operation": op.operation.value,
                            "matched_bill_id": op.matched_bill_id,
                            "changed_fields": op.changed_fields,
                        }
                        for op in decision.operations
                    ],
                }
            )

    conn.close()
    summary = {
        "run_id": run_id,
        "alias_version": rules.alias_version,
        "llm_available": llm_extractor.available,
        "output_count": len(all_outputs),
        "results": all_operations,
    }

    # 题目要求：最终输出是合法 JSON 数组
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(all_outputs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Strict output array written: {out_path}")

    if args.summary_out:
        summary_path = Path(args.summary_out)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"Decision summary written: {summary_path}")

    if not args.out and not args.summary_out:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_ocr(args: argparse.Namespace) -> int:
    """直接测试 OCR，不经过 LLM、不经过工作流。"""
    settings = load_settings()
    settings.ensure_directories()
    client = OCRClient.from_settings(settings)

    if args.url:
        raw = client.process_file_url(args.url)
        result = client.parse_response(raw)
        source = args.url
    else:
        conn = init_db(settings.db_path, SCHEMA_PATH)
        service = build_asset_service(settings, conn)
        asset = service.put(args.input)
        result = client.process_asset(asset.asset_id, service)
        conn.close()
        source = args.input

    payload = result.model_dump(mode="json", exclude={"raw"})
    payload["source"] = source
    if args.raw:
        payload["raw"] = result.raw

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"OCR result written: {out_path}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_directories()
    conn = connect(settings.db_path)
    items = ReviewRepository(conn).list_pending()
    conn.close()
    if not items:
        print("No pending review items.")
        return 0
    for item in items:
        print(
            json.dumps(
                item.model_dump(mode="json"), ensure_ascii=False, indent=2
            )
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shipping-agent",
        description="航运智能账单助手（Phase 0 skeleton）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="显示版本")
    sub.add_parser("init-db", help="初始化 SQLite")

    run_parser = sub.add_parser("run", help="运行工作流（Phase 0 只是最小图）")
    run_parser.add_argument(
        "--input", action="append", required=True, help="输入文件，可重复"
    )
    run_parser.add_argument("--out", help="摘要输出路径")
    run_parser.add_argument("--run-id", help="指定 run_id")
    run_parser.add_argument(
        "--checkpoint", action="store_true", help="启用 SQLite checkpoint"
    )

    parse_parser = sub.add_parser("parse", help="解析文档（Phase 1）")
    parse_parser.add_argument(
        "--input", action="append", required=True, help="输入文件，可重复"
    )
    parse_parser.add_argument(
        "--no-ocr", action="store_true", help="不调用 OCR，仅用本地解析"
    )
    parse_parser.add_argument(
        "--max-depth", type=int, default=3, help="EML 附件递归最大深度"
    )
    parse_parser.add_argument("--out", help="解析摘要输出路径")

    extract_parser = sub.add_parser(
        "extract", help="解析 + 规则/LLM 抽取 + 标准化 + 校验"
    )
    extract_parser.add_argument(
        "--input", action="append", required=True, help="输入文件，可重复"
    )
    extract_parser.add_argument(
        "--no-ocr", action="store_true", help="不调用 OCR"
    )
    extract_parser.add_argument(
        "--no-llm", action="store_true", help="不调用 LLM，仅用规则"
    )
    extract_parser.add_argument(
        "--max-depth", type=int, default=3, help="EML 附件递归最大深度"
    )
    extract_parser.add_argument("--out", help="抽取结果输出路径")

    decide_parser = sub.add_parser(
        "decide", help="解析 + 抽取 + 匹配 + 合并 + AR/AP + 输出"
    )
    decide_parser.add_argument(
        "--input", action="append", required=True, help="输入文件，可重复"
    )
    decide_parser.add_argument(
        "--no-ocr", action="store_true", help="不调用 OCR"
    )
    decide_parser.add_argument(
        "--no-llm", action="store_true", help="不调用 LLM，仅用规则"
    )
    decide_parser.add_argument(
        "--max-depth", type=int, default=3, help="EML 附件递归最大深度"
    )
    decide_parser.add_argument("--run-id", help="指定 run_id")
    decide_parser.add_argument(
        "--out",
        help="严格输出 JSON 数组路径（题目 schema）",
    )
    decide_parser.add_argument(
        "--summary-out",
        help="调试摘要输出路径（含 run_id、匹配信息等）",
    )

    graph_parser = sub.add_parser(
        "graph", help="完整 LangGraph 工作流（含人工复核）"
    )
    graph_parser.add_argument(
        "--input", action="append", required=True, help="输入文件，可重复"
    )
    graph_parser.add_argument(
        "--no-ocr", action="store_true", help="不调用 OCR"
    )
    graph_parser.add_argument(
        "--no-llm", action="store_true", help="不调用 LLM，仅用规则"
    )
    graph_parser.add_argument(
        "--max-depth", type=int, default=3, help="EML 附件递归最大深度"
    )
    graph_parser.add_argument("--thread-id", help="thread_id（用于暂停/恢复）")
    graph_parser.add_argument(
        "--auto-approve-pending",
        action="store_true",
        help="自动接受所有 PENDING_REVIEW（演示用，不暂停）",
    )
    graph_parser.add_argument("--out", help="严格输出 JSON 数组路径")
    graph_parser.add_argument("--summary-out", help="运行摘要路径")

    resume_parser = sub.add_parser(
        "resume", help="恢复被 interrupt 暂停的工作流"
    )
    resume_parser.add_argument("--thread-id", required=True)
    resume_parser.add_argument(
        "--decisions-file",
        help='JSON 文件，格式：{"decisions": {"<charge_id>": {"action": "accept"}}}',
    )
    resume_parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="自动接受所有待复核项",
    )
    resume_parser.add_argument("--no-ocr", action="store_true")
    resume_parser.add_argument("--no-llm", action="store_true")
    resume_parser.add_argument("--max-depth", type=int, default=3)
    resume_parser.add_argument("--out", help="严格输出 JSON 数组路径")
    resume_parser.add_argument("--summary-out", help="运行摘要路径")

    ocr_parser = sub.add_parser("ocr", help="直接调用 OCR（不经过 LLM）")
    ocr_group = ocr_parser.add_mutually_exclusive_group(required=True)
    ocr_group.add_argument("--input", help="本地文件")
    ocr_group.add_argument("--url", help="OCR 服务可访问的文件 URL")
    ocr_parser.add_argument(
        "--raw", action="store_true", help="输出完整原始响应"
    )
    ocr_parser.add_argument("--out", help="OCR 结果输出路径")

    review_parser = sub.add_parser("review", help="人工复核队列")
    review_parser.add_argument(
        "--list", action="store_true", default=True, help="列出待复核项"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "version": cmd_version,
        "init-db": cmd_init_db,
        "run": cmd_run,
        "parse": cmd_parse,
        "extract": cmd_extract,
        "decide": cmd_decide,
        "graph": cmd_graph,
        "resume": cmd_resume,
        "ocr": cmd_ocr,
        "review": cmd_review,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
