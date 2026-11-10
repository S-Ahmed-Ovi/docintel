import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, getKey, setKey } from "./api.js";

const FIELDS = [
  ["invoice_number", "Invoice #", "text"], ["invoice_date", "Date", "date"], ["supplier_name", "Supplier", "text"],
  ["customer_name", "Customer", "text"], ["subtotal", "Subtotal", "number"], ["tax", "Tax", "number"],
  ["total", "Total", "number"], ["currency", "Currency", "text"],
];

const Badge = ({ s }) => <span className={`badge ${s}`}>{s.replace("_", " ")}</span>;

function Upload({ onDone }) {
  const [busy, setBusy] = useState(false); const [msgs, setMsgs] = useState([]); const [drag, setDrag] = useState(false);
  const input = useRef();
  const send = async (files) => {
    if (!files.length) return;
    setBusy(true);
    try {
      const r = await api.upload([...files]);
      setMsgs(r.results.map((x) => (x.accepted ? `✓ ${x.filename} queued` : `✗ ${x.filename}: ${x.error}`)));
      onDone();
    } catch (e) { setMsgs([`Upload failed: ${e.message}`]); } finally { setBusy(false); }
  };
  return (
    <div className={`drop ${drag ? "drag" : ""}`} onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
      onDragLeave={() => setDrag(false)} onDrop={(e) => { e.preventDefault(); setDrag(false); send(e.dataTransfer.files); }}>
      <p>Drag & drop PDF, JPG, PNG, TIFF, XLSX, XLS or CSV files</p>
      <button disabled={busy} onClick={() => input.current.click()}>{busy ? "Uploading…" : "Choose files"}</button>
      <input ref={input} type="file" multiple hidden accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff,.xlsx,.xls,.csv"
        onChange={(e) => { send(e.target.files); e.target.value = ""; }} />
      {msgs.map((m, i) => <div key={i} className={m.startsWith("✗") || m.startsWith("Upload") ? "err" : "ok"}>{m}</div>)}
    </div>
  );
}

function DocTable({ docs, selected, onSelect }) {
  return (
    <table>
      <thead><tr><th>#</th><th>File</th><th>Status</th><th>Progress</th><th>Invoices</th><th>Time</th></tr></thead>
      <tbody>
        {docs.map((d) => (
          <tr key={d.id} className={selected === d.id ? "sel" : ""} onClick={() => onSelect(d.id)}>
            <td>{d.id}</td><td title={d.error || ""}>{d.filename}</td><td><Badge s={d.status} /></td>
            <td><div className="bar"><div style={{ width: `${Math.round(d.progress * 100)}%` }} /></div>
              <small>{d.stage}{d.pages_total ? ` · ${d.pages_done}/${d.pages_total}` : ""}</small></td>
            <td>{d.invoice_count ?? "-"}</td><td>{d.processing_ms != null ? `${(d.processing_ms / 1000).toFixed(1)}s` : "-"}</td>
          </tr>
        ))}
        {!docs.length && <tr><td colSpan="6" className="muted">No documents.</td></tr>}
      </tbody>
    </table>
  );
}

function InvoiceEditor({ docId, inv, onSaved }) {
  const [form, setForm] = useState({}); const [items, setItems] = useState([]); const [err, setErr] = useState(""); const [saving, setSaving] = useState(false);
  useEffect(() => { setForm({ ...inv.data }); setItems((inv.data.line_items || []).map((x) => ({ ...x }))); setErr(""); }, [inv]);
  const lowConf = (f) => inv.confidence && inv.confidence[f] != null && inv.confidence[f] < 0.6;
  const issueFields = new Set(inv.issues.filter((i) => i.severity === "error").map((i) => i.field));
  const save = async () => {
    setSaving(true); setErr("");
    try {
      const body = { invoice_id: inv.id, corrected_by: "ui-user" };
      FIELDS.forEach(([k, , t]) => {
        const v = form[k]; const orig = inv.data[k];
        if ((v ?? "") === (orig ?? "")) return;
        body[k] = v === "" || v == null ? null : t === "number" ? Number(v) : v;
      });
      if (JSON.stringify(items) !== JSON.stringify(inv.data.line_items || []))
        body.line_items = items.map((i) => ({ ...i, quantity: num(i.quantity), unit_price: num(i.unit_price), line_total: num(i.line_total) }));
      if (Object.keys(body).length <= 2) { setErr("Nothing changed."); return; }
      await api.correct(docId, body); onSaved();
    } catch (e) { setErr(e.message); } finally { setSaving(false); }
  };
  const num = (v) => (v === "" || v == null ? null : Number(v));
  return (
    <div className="card">
      <h4>Invoice #{inv.id} <Badge s={inv.status} /> <small>pages {inv.page_start}-{inv.page_end} · extractor: {inv.extractor}</small></h4>
      <div className="grid">
        {FIELDS.map(([k, label, t]) => (
          <label key={k} className={`${lowConf(k) ? "low" : ""} ${issueFields.has(k) ? "bad" : ""}`}>{label}
            <input type={t === "number" ? "number" : t === "date" ? "date" : "text"} step="0.01" value={form[k] ?? ""}
              placeholder="(not found)" onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
            {inv.confidence?.[k] != null && <small>conf {Number(inv.confidence[k]).toFixed(2)}</small>}
          </label>
        ))}
      </div>
      <h5>Line items</h5>
      <table className="items"><thead><tr><th>Description</th><th>Qty</th><th>Unit</th><th>Total</th><th /></tr></thead><tbody>
        {items.map((it, i) => (
          <tr key={i}>{["description", "quantity", "unit_price", "line_total"].map((k) => (
            <td key={k}><input value={it[k] ?? ""} onChange={(e) => setItems(items.map((x, j) => (j === i ? { ...x, [k]: e.target.value } : x)))} /></td>))}
            <td><button className="link" onClick={() => setItems(items.filter((_, j) => j !== i))}>✕</button></td></tr>))}
      </tbody></table>
      <button className="link" onClick={() => setItems([...items, { description: "", quantity: "", unit_price: "", line_total: "" }])}>+ add line</button>
      <h5>Validation</h5>
      {inv.issues.length === 0 && <div className="ok">No issues.</div>}
      <ul className="issues">{inv.issues.map((i) => <li key={i.id} className={i.severity}><b>{i.code}</b>{i.field ? ` (${i.field})` : ""}: {i.message}</li>)}</ul>
      {err && <div className="err">{err}</div>}
      <button onClick={save} disabled={saving}>{saving ? "Saving…" : "Save correction & re-validate"}</button>
      {inv.corrections.length > 0 && (<details><summary>Correction history ({inv.corrections.length})</summary>
        <ul>{inv.corrections.map((c, i) => <li key={i}>{c.field}: {JSON.stringify(c.old)} → {JSON.stringify(c.new)} by {c.by}</li>)}</ul></details>)}
    </div>
  );
}

function Detail({ id, onChanged }) {
  const [res, setRes] = useState(null); const [doc, setDoc] = useState(null); const [text, setText] = useState(null); const [err, setErr] = useState("");
  const load = useCallback(async () => {
    try { const [r, d] = await Promise.all([api.result(id), api.doc(id)]); setRes(r); setDoc(d); setErr(""); } catch (e) { setErr(e.message); }
  }, [id]);
  useEffect(() => { setRes(null); setText(null); load(); }, [load]);
  useEffect(() => { if (res && ["queued", "processing"].includes(res.document.status)) { const t = setTimeout(load, 1500); return () => clearTimeout(t); } }, [res, load]);
  if (err) return <div className="err">{err}</div>;
  if (!res) return <p className="muted">Loading…</p>;
  const d = res.document;
  return (
    <div>
      <h3>{d.filename} <Badge s={d.status} /></h3>
      {d.error && <div className="err">Error: {d.error}</div>}
      <div className="row">
        <button onClick={async () => { await api.reprocess(id); load(); onChanged(); }}>{d.status === "failed" ? "Reprocess failed job" : "Reprocess"}</button>
        <button onClick={async () => setText((await api.text(id)).pages)}>View extracted text</button>
        <button className="danger" onClick={async () => { if (confirm("Delete this document?")) { await api.remove(id); onChanged(true); } }}>Delete</button>
      </div>
      {res.invoices.map((inv) => <InvoiceEditor key={inv.id} docId={id} inv={inv} onSaved={() => { load(); onChanged(); }} />)}
      {d.status !== "failed" && res.invoices.length === 0 && d.status !== "queued" && d.status !== "processing" && <p className="muted">No invoices extracted.</p>}
      {text && <details open><summary>Extracted text</summary>{text.map((p) => (
        <div key={p.page_no}><small>{p.label || `page ${p.page_no}`} · {p.method}{p.ocr_confidence != null ? ` · OCR conf ${(p.ocr_confidence * 100).toFixed(0)}%` : ""}</small><pre>{p.text}</pre></div>))}</details>}
      {doc && <details><summary>Processing history</summary><ul>{doc.events.map((e, i) =>
        <li key={i} className={e.level}>[{e.stage}] {e.message}{e.duration_ms != null ? ` (${e.duration_ms} ms)` : ""}</li>)}</ul></details>}
    </div>
  );
}

function Search({ onOpen }) {
  const [q, setQ] = useState(""); const [mode, setMode] = useState("hybrid"); const [f, setF] = useState({ supplier: "", currency: "", date_from: "", date_to: "" });
  const [rev, setRev] = useState(false); const [hits, setHits] = useState(null); const [err, setErr] = useState("");
  const go = async (e) => {
    e.preventDefault(); setErr("");
    try { setHits((await api.search({ q, mode, ...f, needs_review: rev || "" })).results); } catch (x) { setErr(x.message); }
  };
  return (
    <div>
      <form onSubmit={go} className="row wrap">
        <input required placeholder="Search invoices (number, supplier, text…)" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: 2 }} />
        <select value={mode} onChange={(e) => setMode(e.target.value)}><option value="hybrid">Hybrid</option><option value="keyword">Keyword</option><option value="semantic">Semantic</option></select>
        <input placeholder="Supplier" value={f.supplier} onChange={(e) => setF({ ...f, supplier: e.target.value })} />
        <input placeholder="Currency" size="4" value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value })} />
        <input type="date" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} />
        <input type="date" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} />
        <label className="inline"><input type="checkbox" checked={rev} onChange={(e) => setRev(e.target.checked)} /> needs review</label>
        <button>Search</button>
      </form>
      {err && <div className="err">{err}</div>}
      {hits && <table><thead><tr><th>Invoice #</th><th>Supplier</th><th>Date</th><th>Total</th><th>Status</th><th>Matched by</th></tr></thead><tbody>
        {hits.map((h) => (<tr key={h.invoice_id} onClick={() => onOpen(h.document_id)}><td>{h.data.invoice_number ?? "-"}</td><td>{h.data.supplier_name ?? "-"}</td>
          <td>{h.data.invoice_date ?? "-"}</td><td>{h.data.total ?? "-"} {h.data.currency ?? ""}</td><td><Badge s={h.status} /></td><td>{h.matched_by.join("+")}</td></tr>))}
        {!hits.length && <tr><td colSpan="6" className="muted">No results.</td></tr>}</tbody></table>}
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState("docs"); const [docs, setDocs] = useState([]); const [sel, setSel] = useState(null);
  const [filter, setFilter] = useState({ status: "", q: "" }); const [stats, setStats] = useState(null); const [err, setErr] = useState("");
  const refresh = useCallback(async () => {
    try { const [l, s] = await Promise.all([api.list({ ...filter, limit: 100 }), api.stats()]); setDocs(l.items); setStats(s); setErr(""); }
    catch (e) { setErr(e.message); }
  }, [filter]);
  useEffect(() => { refresh(); }, [refresh]);
  const active = docs.some((d) => ["queued", "processing"].includes(d.status));
  useEffect(() => { const t = setInterval(refresh, active ? 1500 : 8000); return () => clearInterval(t); }, [refresh, active]);
  return (
    <div className="app">
      <header><h1>Document Intelligence</h1>
        <nav><button className={tab === "docs" ? "on" : ""} onClick={() => setTab("docs")}>Documents</button>
          <button className={tab === "search" ? "on" : ""} onClick={() => setTab("search")}>Search</button></nav>
        <input className="key" type="password" defaultValue={getKey()} title="API key" onBlur={(e) => { setKey(e.target.value); refresh(); }} />
      </header>
      {stats && <div className="stats">{Object.entries(stats.by_status).map(([k, v]) => <span key={k}><Badge s={k} /> {v}</span>)}<span>avg {(stats.avg_processing_ms / 1000).toFixed(1)}s/doc</span></div>}
      {err && <div className="err">{err}</div>}
      {tab === "search" ? <Search onOpen={(id) => { setSel(id); setTab("docs"); }} /> : (
        <div className="cols">
          <section>
            <Upload onDone={refresh} />
            <div className="row"><select value={filter.status} onChange={(e) => setFilter({ ...filter, status: e.target.value })}>
              <option value="">All statuses</option>{["queued", "processing", "completed", "needs_review", "failed"].map((s) => <option key={s}>{s}</option>)}</select>
              <input placeholder="Filter text / filename" value={filter.q} onChange={(e) => setFilter({ ...filter, q: e.target.value })} /></div>
            <DocTable docs={docs} selected={sel} onSelect={setSel} />
          </section>
          <section>{sel ? <Detail key={sel} id={sel} onChanged={(del) => { if (del) setSel(null); refresh(); }} /> : <p className="muted">Select a document to inspect, correct and review it.</p>}</section>
        </div>)}
    </div>
  );
}
