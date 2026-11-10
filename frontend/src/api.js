const BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";
let key = localStorage.getItem("apiKey") || import.meta.env.VITE_API_KEY || "dev-key";
export const setKey = (k) => { key = k; localStorage.setItem("apiKey", k); };
export const getKey = () => key;

async function req(path, opts = {}) {
  const r = await fetch(BASE + path, { ...opts, headers: { "X-API-Key": key, ...(opts.headers || {}) } });
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(`${r.status}: ${msg}`);
  }
  return r.json();
}
const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== "" && v != null)).toString();

export const api = {
  upload: (files) => { const fd = new FormData(); files.forEach((f) => fd.append("files", f)); return req("/documents", { method: "POST", body: fd }); },
  list: (p) => req("/documents?" + qs(p)),
  doc: (id) => req(`/documents/${id}`),
  result: (id) => req(`/documents/${id}/result`),
  text: (id) => req(`/documents/${id}/text`),
  correct: (id, body) => req(`/documents/${id}/result`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  reprocess: (id) => req(`/documents/${id}/reprocess?force=true`, { method: "POST" }),
  remove: (id) => req(`/documents/${id}`, { method: "DELETE" }),
  search: (p) => req("/search?" + qs(p)),
  stats: () => req("/stats"),
};
