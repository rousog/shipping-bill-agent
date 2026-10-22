from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shipping_agent.config import load_settings

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"

SAMPLES_AVAILABLE = SAMPLES.exists() and any(SAMPLES.iterdir())
pytestmark = pytest.mark.skipif(not SAMPLES_AVAILABLE, reason="confidential samples not present")


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("ASSET_ROOT", str(tmp_path / "assets"))
    monkeypatch.setenv("OCR_PUBLIC_BASE_URL", "")
    monkeypatch.setenv("ASSET_PUBLIC_BASE_URL", "")
    load_settings.cache_clear()

    import shipping_agent.api as api

    monkeypatch.setattr(api, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api, "OUTPUT_DIR", tmp_path / "output")

    with TestClient(api.app) as test_client:
        yield test_client

    load_settings.cache_clear()


def test_safe_filename_recovers_gbk_mojibake() -> None:
    from shipping_agent.api import _safe_filename

    assert _safe_filename("PDF Ê¾ÀýÕËµ¥2.pdf") == "PDF 示例账单2.pdf"
    assert _safe_filename("正常文件名.pdf") == "正常文件名.pdf"


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "llm_configured" in data
    assert data["alias_version"]


def test_review_filter_by_run_id(client: TestClient) -> None:
    response = client.get("/review", params={"run_id": "NOT-EXIST"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 0
    assert payload["items"] == []


def test_swagger_uses_local_assets(client: TestClient) -> None:
    response = client.get("/docs")
    assert response.status_code == 200
    assert "/static/swagger-ui/swagger-ui-bundle.js" in response.text

    js = client.get("/static/swagger-ui/swagger-ui-bundle.js")
    assert js.status_code == 200
    assert len(js.content) > 100_000

    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    payload = spec.json()
    assert payload["openapi"].startswith("3.0.")
    assert "/extract" in payload["paths"]

    file_schema = payload["components"]["schemas"][
        "Body_decide_document_decide_post"
    ]["properties"]["file"]
    assert file_schema["type"] == "string"
    assert file_schema["format"] == "binary"
    assert "contentMediaType" not in file_schema


def test_parse_pdf(client: TestClient) -> None:
    with (SAMPLES / "PDF 示例账单2.pdf").open("rb") as fh:
        response = client.post(
            "/parse",
            files={"file": ("bill.pdf", fh, "application/pdf")},
            data={"use_ocr": "false"},
        )
    assert response.status_code == 200
    document = response.json()["document"]
    assert document["source_type"] == "pdf"
    assert document["text_chars"] > 0


def test_extract_pdf(client: TestClient) -> None:
    with (SAMPLES / "PDF 示例账单2.pdf").open("rb") as fh:
        response = client.post(
            "/extract",
            files={"file": ("bill.pdf", fh, "application/pdf")},
            data={"use_ocr": "false", "use_llm": "false"},
        )
    assert response.status_code == 200
    documents = response.json()["documents"]
    assert len(documents) == 1
    record = documents[0]["record"]
    assert len(record["charges"]) == 3
    assert record["total_amount"] == "1410.40"


def test_decide_pdf(client: TestClient) -> None:
    with (SAMPLES / "PDF 示例账单2.pdf").open("rb") as fh:
        response = client.post(
            "/decide",
            files={"file": ("bill.pdf", fh, "application/pdf")},
            data={"use_ocr": "false", "use_llm": "false"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert "operations" not in payload
    assert payload["operation_counts"] == {"CREATE": 3}
    assert len(payload["outputs"]) == 3
    assert all(record["operation"] == "CREATE" for record in payload["outputs"])
    assert set(payload["outputs"][0]) == {
        "operation",
        "document_info",
        "transaction_party",
        "charge_details",
        "financial_attributes",
        "audit_trail",
    }


def test_graph_run_and_resume(client: TestClient) -> None:
    with (SAMPLES / "PDF示例账单1.pdf").open("rb") as fh:
        response = client.post(
            "/graph/run",
            files={"file": ("bill.pdf", fh, "application/pdf")},
            data={
                "use_ocr": "false",
                "use_llm": "false",
                "auto_approve_pending": "false",
            },
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["paused"] is True
    thread_id = payload["thread_id"]

    response = client.post(
        "/graph/resume",
        json={"thread_id": thread_id, "auto_approve": True},
    )
    assert response.status_code == 200
    resumed = response.json()
    assert resumed["paused"] is False
    assert len(resumed["outputs"]) == 1

    output_response = client.get(f"/output/{thread_id}")
    assert output_response.status_code == 200
    assert isinstance(output_response.json(), list)
