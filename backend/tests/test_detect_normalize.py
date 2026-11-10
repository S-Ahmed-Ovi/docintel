from decimal import Decimal
from app.pipeline.detect import detect_kind
from app.pipeline.normalize import parse_amount, parse_dates, to_iso_date, norm_supplier


def test_magic_bytes_beat_extension(tmp_path):
    p = tmp_path / "x.pdf"; p.write_bytes(b"MZ\x90\x00 not a pdf")
    assert detect_kind(str(p), "x.pdf")[0] == "unknown"           # exe renamed to .pdf
    p.write_bytes(b"%PDF-1.4 ...")
    assert detect_kind(str(p), "whatever.bin")[0] == "pdf"
    p.write_bytes(b"\x89PNG\r\n\x1a\n....")
    assert detect_kind(str(p), "a.jpg")[0] == "image"


def test_csv_and_binary_rejection(tmp_path):
    p = tmp_path / "a.csv"; p.write_bytes(b"a,b\n1,2\n")
    assert detect_kind(str(p), "a.csv")[0] == "spreadsheet"
    p.write_bytes(b"\x00\x01\x02\x03")
    assert detect_kind(str(p), "a.csv")[0] == "unknown"


def test_parse_amount_formats():
    assert parse_amount("$1,234.56") == Decimal("1234.56")
    assert parse_amount("1.234,56") == Decimal("1234.56")
    assert parse_amount("12,50") == Decimal("12.50")
    assert parse_amount("(45.00)") == Decimal("-45.00")
    assert parse_amount("abc") is None and parse_amount(None) is None


def test_dates():
    assert to_iso_date("05 Mar 2024") == "2024-03-05"
    assert to_iso_date("March 5, 2024") == "2024-03-05"
    assert to_iso_date("29.05.2024") == "2024-05-29"
    assert to_iso_date("2024-13-45") is None
    assert parse_dates("03/04/2024")[0][1] is True      # ambiguous flagged


def test_supplier_normalisation():
    assert norm_supplier("ACME Ltd.") == norm_supplier("acme limited")
