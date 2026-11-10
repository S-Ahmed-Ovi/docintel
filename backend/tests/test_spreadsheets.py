import pandas as pd
import pytest
from app.pipeline.errors import CorruptFileError
from app.pipeline.extract_rules import extract_from_sheet
from app.pipeline.readers import read_spreadsheet
from eval.generate_dataset import sheet_rows


def _rows(truth):
    return sheet_rows([truth])


def test_xlsx_multi_sheet(tmp_path, truth):
    p = tmp_path / "m.xlsx"
    rows = _rows(truth)
    with pd.ExcelWriter(p) as w:
        pd.DataFrame([["notes"]]).to_excel(w, sheet_name="Notes", header=False, index=False)
        pd.DataFrame(rows[1:], columns=rows[0]).to_excel(w, sheet_name="Invoices", index=False)
    sheets = read_spreadsheet(str(p), "m.xlsx")
    assert [s.label for s in sheets] == ["Notes", "Invoices"]
    res = [r for s in sheets for r in extract_from_sheet(s.rows)]
    assert len(res) == 1
    d, _ = res[0]
    assert d["invoice_number"] == truth["invoice_number"] and d["total"] == truth["total"]
    assert len(d["line_items"]) == len(truth["line_items"])


@pytest.mark.parametrize("enc,sep", [("utf-8", ","), ("cp1252", ";"), ("utf-16", ",")])
def test_csv_encodings_and_delimiters(tmp_path, truth, enc, sep):
    rows = _rows(truth); p = tmp_path / "a.csv"
    pd.DataFrame(rows[1:], columns=rows[0]).to_csv(p, index=False, sep=sep, encoding=enc)
    sheets = read_spreadsheet(str(p), "a.csv")
    d, _ = extract_from_sheet(sheets[0].rows)[0]
    assert d["supplier_name"] == truth["supplier_name"] and d["currency"] == truth["currency"]


def test_xls(tmp_path, truth):
    import xlwt
    wb = xlwt.Workbook(); ws = wb.add_sheet("S")
    for r, row in enumerate(_rows(truth)):
        for c, v in enumerate(row): ws.write(r, c, v)
    p = tmp_path / "a.xls"; wb.save(str(p))
    d, _ = extract_from_sheet(read_spreadsheet(str(p), "a.xls")[0].rows)[0]
    assert d["invoice_date"] == truth["invoice_date"]


def test_multiple_invoices_grouped_without_mixing(tmp_path, truth):
    import random
    from faker import Faker
    from eval.generate_dataset import make_truth
    t2 = make_truth(random.Random(9), Faker(), 2)
    rows = sheet_rows([truth, t2]); p = tmp_path / "a.csv"
    pd.DataFrame(rows[1:], columns=rows[0]).to_csv(p, index=False)
    res = extract_from_sheet(read_spreadsheet(str(p), "a.csv")[0].rows)
    assert [r[0]["invoice_number"] for r in res] == [truth["invoice_number"], t2["invoice_number"]]
    assert res[1][0]["total"] == t2["total"]


def test_empty_and_corrupt(tmp_path):
    p = tmp_path / "e.csv"; p.write_bytes(b"\n\n")
    with pytest.raises(CorruptFileError):
        read_spreadsheet(str(p), "e.csv")
    q = tmp_path / "bad.xlsx"; q.write_bytes(b"PK\x03\x04garbage")
    with pytest.raises(CorruptFileError):
        read_spreadsheet(str(q), "bad.xlsx")


def test_unknown_layout_returns_no_table():
    assert extract_from_sheet([["hello", "world"], ["1", "2"]]) == []
