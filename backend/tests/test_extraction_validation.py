import pytest
from app.pipeline import extract_llm
from app.pipeline.extract_rules import extract_from_text
from app.pipeline.grounding import ground
from app.pipeline.process import extract_segment
from app.pipeline.readers import read_pdf
from app.pipeline.segment import segment_pages
from app.pipeline.validate import validate_data
from eval.generate_dataset import render_pdf


@pytest.mark.parametrize("layout", "ABCD")
def test_rules_extract_all_layouts(tmp_path, truth, layout):
    t = dict(truth); t["currency"] = "EUR" if layout == "C" else "USD"
    p = tmp_path / "x.pdf"; render_pdf(t, str(p), layout)
    d, c = extract_from_text(read_pdf(str(p))[0].text)
    for f in ("invoice_number", "invoice_date", "supplier_name", "customer_name", "subtotal", "tax", "total", "currency"):
        assert d[f] == t[f], f
    assert [i["line_total"] for i in d["line_items"]] == [i["line_total"] for i in t["line_items"]]


def test_missing_fields_stay_null(tmp_path, truth):
    p = tmp_path / "x.pdf"; render_pdf(truth, str(p), "A", drop=("invoice_number", "total"))
    d, _ = extract_from_text(read_pdf(str(p))[0].text)
    assert d["invoice_number"] is None and d["total"] is None      # never invented


def test_grounding_discards_hallucinations():
    text = "Acme Ltd\nInvoice No: INV-1\nTotal: 100.00 USD"
    data = {"invoice_number": "INV-999", "total": 100.0, "tax": 20.0, "supplier_name": "Globex", "currency": "EUR",
            "invoice_date": "2024-01-01", "line_items": [{"description": "Imaginary", "line_total": 5}]}
    conf = {k: 0.9 for k in data}
    issues = ground(data, conf, text)
    assert data["invoice_number"] is None and data["tax"] is None and data["supplier_name"] is None
    assert data["currency"] is None and data["invoice_date"] is None and data["line_items"] == []
    assert data["total"] == 100.0
    assert {i["field"] for i in issues} >= {"invoice_number", "tax", "supplier_name", "currency", "invoice_date"}


def test_llm_path_and_fallback(monkeypatch):
    monkeypatch.setattr(extract_llm.settings, "gemini_api_key", "k")
    monkeypatch.setattr(extract_llm, "extract_with_llm", lambda t: ({"invoice_number": "X1"}, {"invoice_number": 0.9}))
    ev = []
    d, c, name = extract_segment("irrelevant", ev)
    assert name == "llm" and d["invoice_number"] == "X1"

    def boom(t): raise RuntimeError("rate limited")
    monkeypatch.setattr(extract_llm, "extract_with_llm", boom)
    d, c, name = extract_segment("Invoice No: AB123\nTotal: 5.00", ev)
    assert name == "rules" and ev and "fallback" in ev[0][2]


def test_llm_json_parsing():
    obj = extract_llm._parse_json('```json\n{"invoice_number": "A1", "confidence": {}}\n```')
    assert obj["invoice_number"] == "A1"


def test_segmentation_multi_invoice(tmp_path, truth):
    import random
    from faker import Faker
    from eval.generate_dataset import make_truth
    from reportlab.pdfgen import canvas
    ts = [truth, make_truth(random.Random(5), Faker(), 2)]
    c = None
    for t in ts:
        c = render_pdf(t, str(tmp_path / "m.pdf"), "A", c=c, finish=False); c.showPage()
    c.save()
    segs = segment_pages(read_pdf(str(tmp_path / "m.pdf")))
    assert len(segs) == 2


def codes(d): return {i["code"] for i in validate_data(d)}


BASE = {"invoice_number": "A1", "invoice_date": "2024-01-01", "supplier_name": "S", "customer_name": "C",
        "subtotal": 100.0, "tax": 10.0, "total": 110.0, "currency": "USD",
        "line_items": [{"description": "a", "quantity": 2, "unit_price": 50, "line_total": 100}]}


def test_clean_record_has_no_errors():
    assert not [i for i in validate_data(BASE) if i["severity"] == "error"]


@pytest.mark.parametrize("patch,code", [
    ({"invoice_number": None}, "MISSING_FIELD"), ({"total": 120.0}, "TOTAL_MISMATCH"),
    ({"subtotal": 90.0, "total": 100.0}, "SUBTOTAL_MISMATCH"), ({"total": -5.0}, "INVALID_NUMBER"),
    ({"total": "abc"}, "INVALID_NUMBER"), ({"currency": "ZZZ"}, "INVALID_CURRENCY"),
    ({"invoice_date": "2024-02-31"}, "INVALID_DATE"), ({"invoice_date": "1950-01-01"}, "INVALID_DATE"),
    ({"line_items": [{"description": "a", "quantity": 2, "unit_price": 50, "line_total": 90}]}, "LINE_MISMATCH"),
])
def test_validation_rules(patch, code):
    assert code in codes({**BASE, **patch})


def test_rounding_tolerance():
    assert "TOTAL_MISMATCH" not in codes({**BASE, "total": 110.01})


def test_gemini_rest_call_and_parsing(monkeypatch):
    import httpx
    monkeypatch.setattr(extract_llm.settings, "gemini_api_key", "k")
    assert extract_llm.provider() == "gemini"
    seen = {}

    def handler(request: httpx.Request):
        seen["key"] = request.headers["x-goog-api-key"]; seen["url"] = str(request.url)
        body = {"candidates": [{"content": {"parts": [{"text": '{"invoice_number":"G-1","total":5.0,"confidence":{"invoice_number":0.9}}'}]}}]}
        return httpx.Response(200, json=body)
    real = httpx.post
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Client(transport=httpx.MockTransport(handler)).post(url, **kw))
    d, c = extract_llm.extract_with_llm("Invoice No: G-1 Total 5.00")
    assert d["invoice_number"] == "G-1" and c["invoice_number"] == 0.9 and d["supplier_name"] is None
    assert seen["key"] == "k" and "generateContent" in seen["url"]


def test_provider_selection(monkeypatch):
    s = extract_llm.settings
    monkeypatch.setattr(s, "gemini_api_key", ""); monkeypatch.setattr(s, "anthropic_api_key", "")
    assert extract_llm.provider() is None
    monkeypatch.setattr(s, "anthropic_api_key", "a"); assert extract_llm.provider() == "anthropic"
    monkeypatch.setattr(s, "gemini_api_key", "g"); assert extract_llm.provider() == "gemini"
    monkeypatch.setattr(s, "llm_provider", "anthropic"); assert extract_llm.provider() == "anthropic"
