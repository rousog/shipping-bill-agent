from __future__ import annotations

from pathlib import Path

from shipping_agent.ocr.client import OCRClient


class FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "success": True,
            "data": {
                "list": [
                    {
                        "fileType": "DEBIT_NOTE",
                        "mblNumber": "MBL-001",
                        "chargeList": [
                            {
                                "chargeName": "HANDLING CHARGE",
                                "amount": "65.00",
                                "currency": "usd",
                                "paymentTerm": "CC",
                            }
                        ],
                        "ctrList": [{"containerNo": "TIIU8975975"}],
                        "suspicious": "N",
                    }
                ]
            },
        }


class FakeSession:
    def __init__(self, fail_times: int = 0):
        self.fail_times = fail_times
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("temporary failure")
        return FakeResponse()


def test_parse_response_maps_domain_fields() -> None:
    client = OCRClient("http://example/process")
    result = client.parse_response(FakeResponse().json())

    assert result.file_type == "DEBIT_NOTE"
    assert result.mbl_number == "MBL-001"
    assert result.charges[0].raw_name == "HANDLING CHARGE"
    assert str(result.charges[0].amount) == "65.00"
    assert result.charges[0].currency == "USD"
    assert result.containers == ["TIIU8975975"]


def test_parse_real_zim_shape() -> None:
    raw = {
        "success": True,
        "data": {
            "list": [
                {
                    "fileType": "PAI-ZIM-NOTICE",
                    "invoiceNumber": "QRF1618731",
                    "mblNumber": "ZIMUNGB1312144",
                    "chargeList": [
                        {
                            "chargeItem": "WHARFAGE ADMINISTRATION FEE",
                            "amount": "5.00",
                            "chargeCtr": "",
                        }
                    ],
                    "ctrList": [{"ctrNumber": "JXLU4594463"}],
                    "requiresReview": "Y",
                }
            ]
        },
    }
    result = OCRClient("http://example/process").parse_response(raw)

    assert result.file_type == "PAI-ZIM-NOTICE"
    assert result.charges[0].raw_name == "WHARFAGE ADMINISTRATION FEE"
    assert str(result.charges[0].amount) == "5.00"
    assert result.containers == ["JXLU4594463"]
    assert result.requires_review is True


def test_process_file_url_calls_api() -> None:
    session = FakeSession()
    client = OCRClient(
        "http://example/process",
        session=session,
        sleep=lambda _: None,
    )
    raw = client.process_file_url("http://file")
    assert raw["success"] is True
    assert session.calls == 1


def test_process_asset_uses_cache(tmp_path: Path) -> None:
    session = FakeSession()
    client = OCRClient(
        "http://example/process",
        session=session,
        cache_dir=tmp_path / "cache",
        sleep=lambda _: None,
    )

    class FakeStore:
        def url(self, asset_id: str, expires_in: int = 3600) -> str:
            return f"http://file/{asset_id}"

    store = FakeStore()
    first = client.process_asset("asset-1", store)
    second = client.process_asset("asset-1", store)

    assert first.file_type == second.file_type == "DEBIT_NOTE"
    assert session.calls == 1
    assert (tmp_path / "cache" / "asset-1.json").exists()
