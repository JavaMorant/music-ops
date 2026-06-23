"use strict";
// Shared working-folder switcher + scan-lock for the static pages (pulse, dedupe).
// The review SPA implements the same behaviour against its own state.
(function () {
  let scanning = false;
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" }[c]));

  async function loadRoots() {
    const sel = document.getElementById("rootSel");
    if (!sel) return;
    try {
      const d = await (await fetch("/api/roots")).json();
      sel.innerHTML = d.roots.map((r) =>
        `<option value="${esc(r.path)}" ${r.path === d.current ? "selected" : ""}>`
        + `${r.kind === "usb" ? "🔌 " : r.kind === "folder" ? "📁 " : "▸ "}`
        + `${esc(r.label)}${r.rekordbox ? " · rekordbox" : ""}</option>`).join("");
      const cur = d.roots.find((r) => r.path === d.current);
      const hint = document.getElementById("rbHint");
      if (hint) hint.hidden = !(cur && cur.rekordbox);
    } catch (e) { /* leave the placeholder option */ }
  }

  async function switchRoot(path) {
    if (scanning) return;            // never re-point mid-scan
    try {
      const r = await fetch("/api/root", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }) });
      const j = await r.json();
      if (!r.ok) { alert(j.detail || "could not switch folder"); loadRoots(); return; }
      location.reload();             // re-scan everything for the new root
    } catch (e) { alert(e.message); loadRoots(); }
  }

  window.Workspace = {
    init() {
      const sel = document.getElementById("rootSel");
      if (sel) sel.addEventListener("change", (e) => switchRoot(e.target.value));
      loadRoots();
    },
    setScanning(on) {
      scanning = !!on;
      document.body.classList.toggle("scanning", scanning);  // locks all of <main>
      const sel = document.getElementById("rootSel");
      if (sel) sel.disabled = scanning;
      document.querySelectorAll("[data-lock]").forEach((el) => { el.disabled = scanning; });
    },
    get scanning() { return scanning; },
  };
})();
