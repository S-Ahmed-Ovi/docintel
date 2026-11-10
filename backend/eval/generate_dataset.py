"""Synthetic labeled dataset generator (ground truth known by construction).
    python -m eval.generate_dataset --out eval/dataset --seed 7
Produces PDFs (4 layouts), degraded scans (PNG/JPG/TIFF/scanned PDF), spreadsheets (XLSX/XLS/CSV), edge cases and
manifest.json containing the expected extraction + expected review flags for every file."""
import argparse
import io
import json
import os
import random
from datetime import date, timedelta

import pymupdf as fitz
import numpy as np
import cv2
from faker import Faker
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

PRODUCTS = ["Widget", "Gadget", "Consulting hour", "Cable set", "Server rack", "Licence key", "Maintenance plan",
            "Office chair", "Laptop stand", "Paper ream", "Toner cartridge", "Network switch", "Cloud storage",
            "Training session", "Shipping fee", "Monitor 27in", "Keyboard", "USB hub"]


def fmt(v, eu=False):
    s = f"{v:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".") if eu else s


def make_truth(rng, fk, n_items=None, cur=None):
    n = n_items or rng.randint(2, 6)
    items = []
    for name in rng.sample(PRODUCTS, n):
        q, u = rng.randint(1, 9), round(rng.uniform(5, 500), 2)
        items.append({"description": name, "quantity": float(q), "unit_price": u, "line_total": round(q * u, 2)})
    sub = round(sum(i["line_total"] for i in items), 2)
    rate = rng.choice([0.05, 0.08, 0.10, 0.15])
    tax = round(sub * rate, 2)
    d = date(2024, 1, 1) + timedelta(days=rng.randint(0, 600))
    return {"invoice_number": rng.choice([f"INV-{d.year}-{rng.randint(1,9999):04d}", f"A{rng.randint(10000,99999)}",
                                          f"{d.year}/{rng.randint(100,999)}"]),
            "invoice_date": d.isoformat(), "supplier_name": fk.company(), "customer_name": fk.company(),
            "subtotal": sub, "tax": tax, "total": round(sub + tax, 2), "currency": cur or rng.choice(["USD", "EUR", "GBP"]),
            "line_items": items, "_rate": int(rate * 100)}


SYM = {"USD": "$", "EUR": "€", "GBP": "£"}


def render_pdf(t, path, layout, c=None, finish=True, drop=()):
    """layout: A classic | B receipt (no header) | C european | D alt labels."""
    own = c is None
    c = c or canvas.Canvas(path, pagesize=A4)
    W, H = A4
    eu = layout == "C"
    f = lambda v: fmt(v, eu)
    cur = t["currency"]
    dt = date.fromisoformat(t["invoice_date"])
    datestr = {"A": dt.isoformat(), "B": dt.strftime("%d %b %Y"), "C": dt.strftime("%d.%m.%Y"), "D": dt.strftime("%B %d, %Y")}[layout]
    y = H - 60
    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, y, t["supplier_name"] if layout != "D" else "TAX INVOICE"); y -= 30
    c.setFont("Helvetica", 11)
    def line(txt, dy=16):
        nonlocal y
        c.drawString(50, y, txt); y -= dy
    if layout == "D":
        line(f"Seller: {t['supplier_name']}")
    if layout == "A":
        line("INVOICE", 20)
    if "invoice_number" not in drop:
        line({"A": "Invoice No: ", "B": "Receipt #: ", "C": "Invoice No. ", "D": "Invoice Number: "}[layout] + t["invoice_number"])
    if "invoice_date" not in drop:
        line({"A": "Invoice Date: ", "B": "Date: ", "C": "Invoice Date: ", "D": "Date of Issue: "}[layout] + datestr)
    y -= 8
    if layout == "C": line(f"Customer: {t['customer_name']}")
    elif layout == "D": line(f"Billed To: {t['customer_name']}")
    else:
        line("Bill To:"); line(t["customer_name"])
    if layout == "C": line(f"Vendor: {t['supplier_name']}")
    y -= 14
    sym = SYM[cur] if layout in ("A", "D") else ""
    if layout != "B":
        c.setFont("Helvetica-Bold", 11)
        for x, h in ((50, "Description"), (300, "Qty"), (360, "Unit Price"), (460, "Total")): c.drawString(x, y, h)
        y -= 18; c.setFont("Helvetica", 11)
    for li in t["line_items"]:
        if layout == "B":
            c.drawString(50, y, f"{li['description']} {int(li['quantity'])} {f(li['unit_price'])} {f(li['line_total'])}")
        else:
            c.drawString(50, y, li["description"]); c.drawString(300, y, str(int(li["quantity"])))
            c.drawString(360, y, f"{sym}{f(li['unit_price'])}"); c.drawString(460, y, f"{sym}{f(li['line_total'])}")
        y -= 16
    y -= 10
    if "subtotal" not in drop: c.drawString(360, y, f"Subtotal: {sym}{f(t['subtotal'])}"); y -= 16
    taxlbl = {"A": "Tax", "B": "Tax", "C": "VAT", "D": "VAT"}[layout]
    if "tax" not in drop: c.drawString(360, y, f"{taxlbl} ({t['_rate']}%): {sym}{f(t['tax'])}"); y -= 16
    c.setFont("Helvetica-Bold", 12)
    if "total" not in drop:
        c.drawString(360, y, ("Amount Due: " if layout == "D" else "Total: ") + f"{sym}{f(t['total'])}"); y -= 20
    c.setFont("Helvetica", 10)
    c.drawString(50, 60, f"Currency: {cur}   Thank you for your business.")
    if finish:
        c.showPage(); c.save()
    return c


def degrade(img: Image.Image, rng, level="mild") -> Image.Image:
    a = np.array(img.convert("L"))
    ang = rng.uniform(-2.5, 2.5)
    h, w = a.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
    a = cv2.warpAffine(a, m, (w, h), borderValue=255)
    k = 3 if level == "mild" else 5
    a = cv2.GaussianBlur(a, (k, k), 0)
    noise = np.random.default_rng(rng.randint(0, 10**6)).normal(0, 6 if level == "mild" else 14, a.shape)
    a = np.clip(a + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(a)


def pdf_to_img(path, dpi=150) -> Image.Image:
    d = fitz.open(path)
    pix = d[0].get_pixmap(dpi=dpi)
    d.close()
    return Image.open(io.BytesIO(pix.tobytes("png")))


def sheet_rows(truths):
    hdr = ["Invoice Number", "Invoice Date", "Supplier", "Customer", "Description", "Quantity", "Unit Price",
           "Line Total", "Subtotal", "Tax", "Total", "Currency"]
    rows = [hdr]
    for t in truths:
        for i, li in enumerate(t["line_items"]):
            rows.append([t["invoice_number"], t["invoice_date"], t["supplier_name"], t["customer_name"], li["description"],
                         li["quantity"], li["unit_price"], li["line_total"], t["subtotal"], t["tax"], t["total"], t["currency"]])
    return rows


def clean(t):
    return {k: v for k, v in t.items() if not k.startswith("_")}


def build(out, seed=7, n_pdf=24, n_scan=12, n_sheet=8):
    rng = random.Random(seed)
    fk = Faker(); Faker.seed(seed)
    os.makedirs(out, exist_ok=True)
    man = []

    def add(fn, kind, truths, expect_status="completed", flags=(), corrupt=False, group="digital"):
        man.append({"file": fn, "kind": kind, "group": group, "truths": [clean(t) for t in truths],
                    "expect_status": expect_status, "expect_flags": list(flags), "corrupt": corrupt})

    # 1) digital PDFs, all four layouts
    for i in range(n_pdf):
        lay = "ABCD"[i % 4]
        t = make_truth(rng, fk, cur="EUR" if lay == "C" else None)
        fn = f"digital_{i:02d}_{lay}.pdf"
        render_pdf(t, os.path.join(out, fn), lay)
        add(fn, "pdf", [t], group=f"digital_{lay}")
    # 2) scans: PNG/JPG/TIFF/scanned-PDF with degradation
    for i in range(n_scan):
        lay = "AD"[i % 2]
        t = make_truth(rng, fk)
        tmp = os.path.join(out, "_tmp.pdf"); render_pdf(t, tmp, lay)
        img = degrade(pdf_to_img(tmp), rng, "mild" if i % 3 else "heavy")
        ext = ["png", "jpg", "tiff", "pdf"][i % 4]
        fn = f"scan_{i:02d}_{lay}.{ext}"
        p = os.path.join(out, fn)
        if ext == "jpg": img.save(p, quality=60)
        elif ext == "pdf":
            doc = fitz.open(); pg = doc.new_page(width=595, height=842)
            buf = io.BytesIO(); img.save(buf, "PNG"); pg.insert_image(pg.rect, stream=buf.getvalue()); doc.save(p); doc.close()
        else: img.save(p)
        add(fn, "image" if ext != "pdf" else "pdf", [t], group="scan_heavy" if i % 3 == 0 else "scan_mild")
    os.remove(os.path.join(out, "_tmp.pdf"))
    # 3) spreadsheets
    import pandas as pd
    for i in range(n_sheet):
        ts = [make_truth(rng, fk) for _ in range(rng.randint(1, 3))]
        rows = sheet_rows(ts); df = pd.DataFrame(rows[1:], columns=rows[0])
        kind = ["xlsx", "csv", "xls", "xlsx_multi", "csv_semicolon", "csv_utf16"][i % 6]
        fn = f"sheet_{i:02d}.{kind.split('_')[0]}"
        p = os.path.join(out, fn)
        if kind == "xlsx": df.to_excel(p, index=False)
        elif kind == "xlsx_multi":
            with pd.ExcelWriter(p) as w:
                pd.DataFrame([["Quarterly export"], ["generated by ERP"]]).to_excel(w, sheet_name="Notes", header=False, index=False)
                df.to_excel(w, sheet_name="Invoices", index=False)
        elif kind == "xls":
            import xlwt
            wb = xlwt.Workbook(); ws = wb.add_sheet("Invoices")
            for r, row in enumerate(rows):
                for cc, v in enumerate(row): ws.write(r, cc, v)
            wb.save(p)
        elif kind == "csv": df.to_csv(p, index=False)
        elif kind == "csv_semicolon": df.to_csv(p, index=False, sep=";", encoding="cp1252")
        else: df.to_csv(p, index=False, encoding="utf-16")
        add(fn, "spreadsheet", ts, group="spreadsheet")
    # 4) multi-invoice PDF (3 invoices, 1 page each)
    ts = [make_truth(rng, fk) for _ in range(3)]
    c = None
    for j, t in enumerate(ts):
        c = render_pdf(t, os.path.join(out, "multi_invoice.pdf"), "A", c=c, finish=False)
        c.showPage()
    c.save(); add("multi_invoice.pdf", "pdf", ts, group="multi")
    # 5) validation edge cases
    for i, (name, drop) in enumerate([("missing_number", ("invoice_number",)), ("missing_total", ("total",)),
                                       ("missing_date", ("invoice_date",))]):
        t = make_truth(rng, fk); fn = f"edge_{name}.pdf"
        render_pdf(t, os.path.join(out, fn), "A", drop=drop)
        tt = dict(t)
        for d in drop: tt[d] = None
        add(fn, "pdf", [tt], "needs_review", ["MISSING_FIELD"], group="edge")
    t = make_truth(rng, fk); bad = dict(t); bad["total"] = round(t["total"] + 10, 2)
    render_pdf(bad, os.path.join(out, "edge_wrong_total.pdf"), "A")
    add("edge_wrong_total.pdf", "pdf", [bad], "needs_review", ["TOTAL_MISMATCH"], group="edge")
    t = make_truth(rng, fk); bad = dict(t); bad["subtotal"] = round(t["subtotal"] + 25, 2); bad["total"] = round(bad["subtotal"] + t["tax"], 2)
    render_pdf(bad, os.path.join(out, "edge_wrong_subtotal.pdf"), "A")
    add("edge_wrong_subtotal.pdf", "pdf", [bad], "needs_review", ["SUBTOTAL_MISMATCH"], group="edge")
    t = make_truth(rng, fk)
    render_pdf(t, os.path.join(out, "edge_dup_a.pdf"), "A"); add("edge_dup_a.pdf", "pdf", [t], "any", group="edge")
    render_pdf(t, os.path.join(out, "edge_dup_b.pdf"), "D"); add("edge_dup_b.pdf", "pdf", [t], "needs_review", ["DUPLICATE_INVOICE"], group="edge"); man[-1]["pair"] = "edge_dup_a.pdf"
    # PDF vs Excel cross-verification: same invoice number, spreadsheet total differs
    t = make_truth(rng, fk); render_pdf(t, os.path.join(out, "edge_cross_pdf.pdf"), "A"); add("edge_cross_pdf.pdf", "pdf", [t], "any", group="edge")
    t2 = json.loads(json.dumps(t)); t2["line_items"][0]["line_total"] = round(t2["line_items"][0]["line_total"] + 50, 2)
    t2["line_items"][0]["unit_price"] = round(t2["line_items"][0]["line_total"] / t2["line_items"][0]["quantity"], 4)
    t2["subtotal"] = round(sum(i["line_total"] for i in t2["line_items"]), 2); t2["total"] = round(t2["subtotal"] + t2["tax"], 2)
    rows = sheet_rows([t2]); pd.DataFrame(rows[1:], columns=rows[0]).to_excel(os.path.join(out, "edge_cross_sheet.xlsx"), index=False)
    add("edge_cross_sheet.xlsx", "spreadsheet", [t2], "needs_review", ["CROSS_SOURCE_MISMATCH"], group="edge"); man[-1]["pair"] = "edge_cross_pdf.pdf"
    # 6) corrupt files (expected to fail gracefully)
    good = open(os.path.join(out, "digital_00_A.pdf"), "rb").read()
    blobs = {"corrupt_truncated.pdf": good[: len(good) // 3], "corrupt_garbage.png": b"\x89PNG\r\n\x1a\n" + os.urandom(500),
             "corrupt_fake.xlsx": b"PK\x03\x04" + os.urandom(300), "corrupt_empty_sheet.csv": b"\n\n\n",
             "corrupt_blank.pdf": None}
    for fn, b in blobs.items():
        p = os.path.join(out, fn)
        if b is None:
            d = fitz.open(); d.new_page(); d.save(p); d.close()
        else:
            open(p, "wb").write(b)
        add(fn, "unknown", [], "failed", [], corrupt=True, group="corrupt")
    json.dump(man, open(os.path.join(out, "manifest.json"), "w"), indent=1)
    return man


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="eval/dataset"); ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    m = build(a.out, a.seed)
    print(f"generated {len(m)} files in {a.out}")
