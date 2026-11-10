import io
import os
from app.config import settings
from conftest import H, upload
from eval.generate_dataset import render_pdf


def test_auth_required(client):
    assert client.get("/documents").status_code == 401
    assert client.get("/documents", headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get("/health").status_code == 200


def test_upload_process_result(client, pdf_path, truth):
    r = upload(client, "inv.pdf", open(pdf_path, "rb").read())
    assert r["accepted"]
    doc = client.get(f"/documents/{r['id']}", headers=H).json()
    assert doc["status"] in ("completed", "needs_review") and doc["progress"] == 1.0
    assert any(e["stage"] == "reading" for e in doc["events"])
    res = client.get(f"/documents/{r['id']}/result", headers=H).json()
    data = res["invoices"][0]["data"]
    assert data["invoice_number"] == truth["invoice_number"] and data["total"] == truth["total"]
    assert res["invoices"][0]["extractor"] == "rules"


def test_multi_upload_with_rejections(client, pdf_path):
    files = [("files", ("a.pdf", open(pdf_path, "rb").read())), ("files", ("evil.exe", b"MZ\x90\x00")),
             ("files", ("empty.pdf", b""))]
    res = client.post("/documents", headers=H, files=files).json()["results"]
    assert [r["accepted"] for r in res] == [True, False, False]
    assert "unsupported" in res[1]["error"] and "empty" in res[2]["error"]


def test_filename_sanitised(client, pdf_path):
    r = upload(client, "../../etc/passwd.pdf", open(pdf_path, "rb").read())
    d = client.get(f"/documents/{r['id']}", headers=H).json()
    assert "/" not in d["filename"] and ".." not in d["filename"].replace("passwd.pdf", "")


def test_size_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 0)
    res = client.post("/documents", headers=H, files=[("files", ("a.pdf", b"%PDF-1.4" + b"0" * 100))]).json()["results"][0]
    assert not res["accepted"] and "limit" in res["error"]


def test_manual_correction_revalidates_and_audits(client, tmp_path, truth):
    bad = dict(truth); bad["total"] = round(truth["total"] + 10, 2)
    p = tmp_path / "b.pdf"; render_pdf(bad, str(p), "A")
    r = upload(client, "b.pdf", p.read_bytes())
    res = client.get(f"/documents/{r['id']}/result", headers=H).json()
    assert res["document"]["status"] == "needs_review"
    assert "TOTAL_MISMATCH" in {i["code"] for i in res["invoices"][0]["issues"]}
    pr = client.patch(f"/documents/{r['id']}/result", headers=H, json={"total": truth["total"], "corrected_by": "alice"})
    assert pr.status_code == 200
    inv = pr.json()
    assert inv["status"] == "corrected" and not [i for i in inv["issues"] if i["severity"] == "error"]
    assert inv["corrections"][0]["old"] == bad["total"] and inv["corrections"][0]["by"] == "alice"
    assert inv["ai_data"]["total"] == bad["total"]          # original AI output preserved
    assert client.get(f"/documents/{r['id']}", headers=H).json()["status"] == "completed"


def test_correction_validation_errors(client, pdf_path):
    r = upload(client, "a.pdf", open(pdf_path, "rb").read())
    assert client.patch(f"/documents/{r['id']}/result", headers=H, json={"invoice_date": "05/03/2024"}).status_code == 422
    assert client.patch(f"/documents/{r['id']}/result", headers=H, json={}).status_code == 422
    assert client.patch("/documents/9999/result", headers=H, json={"total": 1}).status_code == 404


def test_duplicate_detection(client, pdf_path):
    data = open(pdf_path, "rb").read()
    upload(client, "a.pdf", data)
    b = upload(client, "b.pdf", data)
    codes = {i["code"] for i in client.get(f"/documents/{b['id']}/result", headers=H).json()["invoices"][0]["issues"]}
    assert {"DUPLICATE_INVOICE", "DUPLICATE_FILE"} <= codes


def test_pdf_vs_excel_cross_verification(client, tmp_path, truth):
    import pandas as pd
    from eval.generate_dataset import sheet_rows
    p = tmp_path / "a.pdf"; render_pdf(truth, str(p), "A"); upload(client, "a.pdf", p.read_bytes())
    t2 = dict(truth); t2["line_items"] = [dict(i) for i in truth["line_items"]]
    t2["total"] = round(truth["total"] + 99, 2)
    rows = sheet_rows([t2]); x = tmp_path / "a.xlsx"; pd.DataFrame(rows[1:], columns=rows[0]).to_excel(x, index=False)
    r = upload(client, "a.xlsx", x.read_bytes())
    codes = {i["code"] for i in client.get(f"/documents/{r['id']}/result", headers=H).json()["invoices"][0]["issues"]}
    assert "CROSS_SOURCE_MISMATCH" in codes


def test_list_filters_and_search(client, tmp_path, truth):
    import random
    from faker import Faker
    from eval.generate_dataset import make_truth
    t2 = make_truth(random.Random(11), Faker(), 2, "EUR")
    for i, t in enumerate((truth, t2)):
        p = tmp_path / f"{i}.pdf"; render_pdf(t, str(p), "A"); upload(client, f"{i}.pdf", p.read_bytes())
    lst = client.get("/documents", headers=H, params={"supplier": truth["supplier_name"].split()[0]}).json()
    assert lst["total"] >= 1
    kw = client.get("/search", headers=H, params={"q": truth["invoice_number"], "mode": "keyword"}).json()["results"]
    assert kw and kw[0]["data"]["invoice_number"] == truth["invoice_number"]
    sem = client.get("/search", headers=H, params={"q": truth["supplier_name"], "mode": "semantic"}).json()["results"]
    assert sem and sem[0]["data"]["supplier_name"] == truth["supplier_name"]
    hy = client.get("/search", headers=H, params={"q": truth["customer_name"], "currency": "USD"}).json()["results"]
    assert all(h["data"]["currency"] == "USD" for h in hy) and hy
    assert client.get("/search", headers=H, params={"q": "x", "mode": "bogus"}).status_code == 422


def test_stats_and_delete(client, pdf_path):
    r = upload(client, "a.pdf", open(pdf_path, "rb").read())
    assert client.get("/stats", headers=H).json()["total"] == 1
    assert client.delete(f"/documents/{r['id']}", headers=H).status_code == 200
    assert client.get(f"/documents/{r['id']}", headers=H).status_code == 404
