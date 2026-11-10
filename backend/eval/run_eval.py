"""End-to-end evaluation through the real HTTP API + background workers.
    python -m eval.run_eval --dataset eval/dataset --report ../docs/EVALUATION_REPORT.md [--workers 4]
All numbers are measured on the run; nothing is hard-coded."""
import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from collections import defaultdict

ap = argparse.ArgumentParser()
ap.add_argument("--dataset", default="eval/dataset")
ap.add_argument("--report", default="eval/EVALUATION_REPORT.md")
ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--no-llm", action="store_true", help="force the rule-based extractor even if ANTHROPIC_API_KEY is set")
args = ap.parse_args()

tmp = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{tmp}/eval.db", STORAGE_DIR=f"{tmp}/files", API_KEY="eval-key",
                  EMBEDDED_WORKER="0", RATE_LIMIT_PER_MIN="100000")
if args.no_llm:
    os.environ["ANTHROPIC_API_KEY"] = ""; os.environ["GEMINI_API_KEY"] = ""
os.makedirs(f"{tmp}/files", exist_ok=True)
sys.path.insert(0, os.getcwd())

from fastapi.testclient import TestClient  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.pipeline.normalize import norm_invoice_no, norm_text  # noqa: E402
from app.worker import start_threads  # noqa: E402

FIELDS = ["invoice_number", "invoice_date", "supplier_name", "customer_name", "subtotal", "tax", "total", "currency"]


def eq(f, a, b):
    if a is None or b is None:
        return a is None and b is None
    if f in ("subtotal", "tax", "total"):
        return abs(float(a) - float(b)) < 0.011
    if f == "invoice_number":
        return norm_invoice_no(a) == norm_invoice_no(b)
    if f in ("supplier_name", "customer_name"):
        return norm_text(a) == norm_text(b)
    return str(a).upper() == str(b).upper()


def line_pr(truth_items, got_items):
    used, tp = set(), 0
    for t in truth_items:
        for j, g in enumerate(got_items):
            if j in used:
                continue
            if norm_text(t["description"]) == norm_text(g.get("description")) and g.get("line_total") is not None \
                    and abs(float(g["line_total"]) - t["line_total"]) < 0.011:
                used.add(j); tp += 1
                break
    return tp, len(got_items), len(truth_items)


def pct(x, n):
    return f"{100 * x / n:.1f}%" if n else "n/a"


def main():
    man = json.load(open(os.path.join(args.dataset, "manifest.json")))
    with TestClient(app) as client:
        stop, _ = start_threads(args.workers)
        H = {"X-API-Key": "eval-key"}
        t_start = time.perf_counter()
        ids = {}
        for m in man:   # sequential upload keeps dup/cross pairs ordered
            with open(os.path.join(args.dataset, m["file"]), "rb") as fh:
                r = client.post("/documents", headers=H, files=[("files", (m["file"], fh.read()))])
            res = r.json()["results"][0]
            m["upload"] = res
            if res["accepted"]:
                ids[m["file"]] = res["id"]
        deadline = time.time() + 1800
        while time.time() < deadline:
            st = client.get("/stats", headers=H).json()["by_status"]
            if not st.get("queued") and not st.get("processing"):
                break
            time.sleep(0.5)
        wall = time.perf_counter() - t_start
        stop.set()
        docs = {}
        for m in man:
            if m["file"] in ids:
                d = client.get(f"/documents/{ids[m['file']]}", headers=H).json()
                r = client.get(f"/documents/{ids[m['file']]}/result", headers=H).json()
                docs[m["file"]] = (d, r["invoices"])

    # ---- metrics
    field_ok, field_n = defaultdict(int), defaultdict(int)
    grp = defaultdict(lambda: [0, 0])      # group -> [fields ok, fields total]
    doc_exact, doc_n = 0, 0
    li_tp = li_got = li_truth = 0
    halluc = null_fields = 0
    times, per_page = [], []
    ok_docs = fail_docs = 0
    corrupt_total = corrupt_failed = 0
    flag_expected = flag_hit = 0
    clean_total = clean_false_flag = 0
    misses = []
    flag_codes = defaultdict(int)
    flagged_files, missed_flags = [], []
    for m in man:
        d, invs = docs.get(m["file"], ({"status": "rejected"}, []))
        if m["corrupt"]:
            corrupt_total += 1
            corrupt_failed += d["status"] == "failed"
            continue
        if d["status"] == "failed":
            fail_docs += 1
        else:
            ok_docs += 1
        if d.get("processing_ms") is not None:
            times.append(d["processing_ms"])
            per_page.append(d["processing_ms"] / max(d.get("pages_total") or 1, 1))
        codes = {i["code"] for iv in invs for i in iv["issues"]}
        if m.get("pair") in docs:   # concurrent workers: either document of a pair may be the one flagged
            codes |= {i["code"] for iv in docs[m["pair"]][1] for i in iv["issues"]}
        if m["expect_flags"]:
            flag_expected += 1
            hit = any(c in codes for c in m["expect_flags"])
            flag_hit += hit
            if not hit:
                missed_flags.append(f"{m['file']} (expected {m['expect_flags']}, got {sorted(codes)})")
        elif m["expect_status"] == "completed":
            clean_total += 1
            clean_false_flag += d["status"] == "needs_review"
            if d["status"] == "needs_review":
                flagged_files.append(m["file"])
                for iv in invs:
                    for i in iv["issues"]:
                        if i["severity"] == "error" or i["code"] in ("LOW_CONFIDENCE", "UNGROUNDED_FIELD"):
                            flag_codes[f"{i['code']}:{i['field']}"] += 1
        used = set()
        for ti, t in enumerate(m["truths"]):
            got = next((iv for iv in invs if iv["id"] not in used and iv["data"].get("invoice_number") and t["invoice_number"]
                        and norm_invoice_no(iv["data"]["invoice_number"]) == norm_invoice_no(t["invoice_number"])), None)
            if got is None:
                got = next((iv for iv in invs if iv["id"] not in used), None)
            doc_n += 1
            if got is None:
                for f in FIELDS:
                    field_n[f] += 1; grp[m["group"]][1] += 1
                li_truth += len(t["line_items"])
                misses.append((m["file"], "no invoice extracted"))
                continue
            used.add(got["id"])
            data = got["data"]
            all_ok = True
            for f in FIELDS:
                ok = eq(f, data.get(f), t[f])
                field_n[f] += 1; field_ok[f] += ok
                grp[m["group"]][1] += 1; grp[m["group"]][0] += ok
                if t[f] is None:
                    null_fields += 1; halluc += data.get(f) is not None
                if not ok:
                    all_ok = False; misses.append((m["file"], f"{f}: expected {t[f]!r} got {data.get(f)!r}"))
            tp, g, n = line_pr(t["line_items"], data.get("line_items") or [])
            li_tp += tp; li_got += g; li_truth += n
            all_ok &= tp == n == g
            doc_exact += all_ok
    n_proc = len(times)
    srt = sorted(times)
    p95 = srt[min(len(srt) - 1, int(0.95 * len(srt)))] if srt else 0
    total_fields = sum(field_n.values())
    from app.pipeline.extract_llm import available, llm_label  # noqa
    ext = "LLM: " + llm_label() if available() else "rule-based baseline (no LLM key in this run)"
    L = []
    L.append("# Evaluation report (auto-generated by `python -m eval.run_eval`)\n")
    L.append(f"- Extractor under test: **{ext}**; OCR: Tesseract; workers: {args.workers}; dataset: {len(man)} files "
             f"({sum(len(m['truths']) for m in man)} labeled invoices) generated by `eval/generate_dataset.py` (synthetic - see limitations).")
    L.append(f"- Wall-clock for the whole batch: **{wall:.1f}s** ({len(man) / wall:.2f} docs/s) on this machine.\n")
    L.append("## Headline metrics\n")
    L.append("| Metric | Value |\n|---|---|")
    L.append(f"| Field-level accuracy (8 header fields) | **{pct(sum(field_ok.values()), total_fields)}** ({sum(field_ok.values())}/{total_fields}) |")
    L.append(f"| Invoice exact match (all fields + all line items) | **{pct(doc_exact, doc_n)}** ({doc_exact}/{doc_n}) |")
    P = li_tp / li_got if li_got else 0; R = li_tp / li_truth if li_truth else 0
    L.append(f"| Line-item precision / recall | **{P:.1%} / {R:.1%}** |")
    L.append(f"| Hallucinated values (non-null where truth is null) | **{halluc}/{null_fields}** |")
    L.append(f"| Document success rate (non-corrupt files not `failed`) | **{pct(ok_docs, ok_docs + fail_docs)}** ({ok_docs}/{ok_docs + fail_docs}) |")
    L.append(f"| Document failure rate (non-corrupt files `failed`) | **{pct(fail_docs, ok_docs + fail_docs)}** |")
    L.append(f"| Corrupt files handled gracefully (status `failed`, no crash) | **{pct(corrupt_failed, corrupt_total)}** ({corrupt_failed}/{corrupt_total}) |")
    L.append(f"| Injected-error detection (expected flag raised) | **{pct(flag_hit, flag_expected)}** ({flag_hit}/{flag_expected}) |")
    L.append(f"| False review flags on clean documents | **{pct(clean_false_flag, clean_total)}** ({clean_false_flag}/{clean_total}) |")
    if times:
        L.append(f"| Processing time per document (median / mean / p95) | **{statistics.median(times)/1000:.2f}s / {statistics.mean(times)/1000:.2f}s / {p95/1000:.2f}s** |")
        L.append(f"| Processing time per page (median) | **{statistics.median(per_page)/1000:.2f}s** |")
    L.append("\n## Accuracy per field\n\n| Field | Accuracy |\n|---|---|")
    for f in FIELDS:
        L.append(f"| {f} | {pct(field_ok[f], field_n[f])} ({field_ok[f]}/{field_n[f]}) |")
    L.append("\n## Accuracy per document group\n\n| Group | Field accuracy |\n|---|---|")
    for g, (a, b) in sorted(grp.items()):
        L.append(f"| {g} | {pct(a, b)} ({a}/{b}) |")
    L.append("\n## Why clean documents were flagged for review\n")
    for k, v in sorted(flag_codes.items(), key=lambda kv: -kv[1]):
        L.append(f"- {k}: {v}")
    L.append("\nFlagged clean files: " + ", ".join(flagged_files))
    L.append("\nExpected flags NOT raised: " + ("; ".join(missed_flags) or "none"))
    L.append("\n## Misses (first 40)\n")
    for f, why in misses[:40]:
        L.append(f"- `{f}` - {why}")
    L.append("\n## Limitations\n\n- The dataset is synthetic with known ground truth; real-world layouts will be harder. "
             "Re-run on labeled real documents with the same script (`manifest.json` format) before trusting these numbers in production.\n"
             "- Scores for the rule-based baseline are *not* LLM scores. Set `GEMINI_API_KEY` (or `ANTHROPIC_API_KEY`) and re-run to measure the LLM path.\n"
             "- Timings are from a single machine with SQLite; PostgreSQL + multiple worker containers will differ.\n")
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    open(args.report, "w").write("\n".join(L))
    json.dump({"field_ok": field_ok, "field_n": field_n, "wall": wall, "times_ms": times}, open(args.report.replace(".md", ".json"), "w"))
    print("\n".join(L[:30]))


main()
