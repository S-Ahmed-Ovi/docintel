import io
import random
import pymupdf as fitz
import pytest
from PIL import Image
from app.pipeline.errors import CorruptFileError, UnsupportedFileError
from app.pipeline.readers import read_image, read_pdf
from app.config import settings
from eval.generate_dataset import degrade, pdf_to_img


def test_digital_pdf_uses_text_layer(pdf_path, truth):
    pages = read_pdf(pdf_path)
    assert pages[0].method == "text" and truth["invoice_number"] in pages[0].text


def test_multi_page_pdf_progress(tmp_path, pdf_path):
    d = fitz.open()
    for _ in range(3): d.insert_pdf(fitz.open(pdf_path))
    p = tmp_path / "m.pdf"; d.save(str(p))
    seen = []
    pages = read_pdf(str(p), lambda a, b: seen.append((a, b)))
    assert len(pages) == 3 and seen[-1] == (3, 3)


def test_scanned_pdf_falls_back_to_ocr(tmp_path, pdf_path, truth):
    img = pdf_to_img(pdf_path)
    d = fitz.open(); pg = d.new_page(width=595, height=842)
    buf = io.BytesIO(); img.save(buf, "PNG"); pg.insert_image(pg.rect, stream=buf.getvalue())
    p = tmp_path / "scan.pdf"; d.save(str(p))
    pages = read_pdf(str(p))
    assert pages[0].method == "ocr" and pages[0].confidence > 0.5
    assert truth["invoice_number"].replace("-", "") in pages[0].text.replace("-", "").replace(" ", "")


def test_degraded_image_ocr(tmp_path, pdf_path, truth):
    img = degrade(pdf_to_img(pdf_path), random.Random(1), "mild")
    p = tmp_path / "s.jpg"; img.save(p, quality=50)
    text = read_image(str(p))[0].text
    assert truth["supplier_name"].split()[0].lower() in text.lower()


def test_multipage_tiff(tmp_path, pdf_path):
    a = pdf_to_img(pdf_path).convert("L")
    p = tmp_path / "m.tiff"; a.save(p, save_all=True, append_images=[a])
    assert len(read_image(str(p))) == 2


def test_corrupt_inputs(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 truncated")
    with pytest.raises(CorruptFileError):
        read_pdf(str(tmp_path / "a.pdf"))
    (tmp_path / "b.png").write_bytes(b"\x89PNG\r\n\x1a\nxxxx")
    with pytest.raises(CorruptFileError):
        read_image(str(tmp_path / "b.png"))


def test_page_limit(tmp_path, pdf_path, monkeypatch):
    monkeypatch.setattr(settings, "max_pdf_pages", 0)
    with pytest.raises(UnsupportedFileError):
        read_pdf(pdf_path)
