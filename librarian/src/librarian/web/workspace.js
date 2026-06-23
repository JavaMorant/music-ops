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

  function injectStyle() {
    if (document.getElementById("ws-style")) return;
    const css = `
      .ws-modal{position:fixed;inset:0;background:rgba(8,9,12,.74);backdrop-filter:blur(3px);
        display:flex;align-items:center;justify-content:center;z-index:1000;padding:20px}
      .ws-card{background:#16181d;border:1px solid #262a31;border-radius:16px;padding:24px;
        max-width:460px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,.5);
        font:14px/1.5 ui-sans-serif,-apple-system,Segoe UI,Roboto,sans-serif}
      .ws-h{margin:0 0 4px;font-size:18px;color:#e7e9ee;font-weight:600}
      .ws-sub{margin:0 0 16px;font-size:13px;color:#8b909b}
      .ws-list{display:flex;flex-direction:column;gap:6px;max-height:46vh;overflow:auto;margin-bottom:18px}
      .ws-opt{display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid #262a31;
        border-radius:10px;cursor:pointer;color:#e7e9ee;font-size:13px}
      .ws-opt:hover{background:#1c1f25}
      .ws-opt:has(input:checked){border-color:#7aa2ff;background:rgba(122,162,255,.08)}
      .ws-opt input{accent-color:#7aa2ff}
      .ws-ico{width:18px;text-align:center}
      .ws-lab{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .ws-rb{font-size:10px;font-weight:700;color:#5fd08a;border:1px solid rgba(95,208,138,.4);
        border-radius:5px;padding:1px 6px;white-space:nowrap}
      .ws-actions{display:flex;justify-content:space-between;align-items:center;gap:10px}
      .ws-skip{background:none;border:0;color:#8b909b;font-size:12px;cursor:pointer}
      .ws-go{background:#7aa2ff;color:#0b0d12;border:0;border-radius:8px;padding:9px 18px;
        font-weight:600;cursor:pointer;font-size:13px}`;
    const s = document.createElement("style");
    s.id = "ws-style"; s.textContent = css;
    document.head.appendChild(s);
  }

  async function promptOnFirstVisit() {
    if (sessionStorage.getItem("ws-chosen")) return;   // once per browser session
    let data;
    try { data = await (await fetch("/api/roots")).json(); } catch (e) { return; }
    if (!data || !data.roots || !data.roots.length) return;
    injectStyle();
    const overlay = document.createElement("div");
    overlay.className = "ws-modal";
    overlay.innerHTML =
      `<div class="ws-card">
        <div class="ws-h">Where is librarian working?</div>
        <div class="ws-sub">Pick a folder or a plugged-in USB. You can change this anytime from the header.</div>
        <div class="ws-list">${data.roots.map((r) =>
          `<label class="ws-opt"><input type="radio" name="ws-root" value="${esc(r.path)}" ${r.path === data.current ? "checked" : ""}>`
          + `<span class="ws-ico">${r.kind === "usb" ? "🔌" : r.kind === "folder" ? "📁" : "▸"}</span>`
          + `<span class="ws-lab">${esc(r.label)}</span>`
          + `${r.rekordbox ? '<span class="ws-rb">rekordbox</span>' : ""}</label>`).join("")}</div>
        <div class="ws-actions">
          <button class="ws-skip" id="ws-skip">keep current</button>
          <button class="ws-go" id="ws-go">Use this folder</button>
        </div>
      </div>`;
    const close = () => { sessionStorage.setItem("ws-chosen", "1"); overlay.remove(); };
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });  // backdrop
    document.body.appendChild(overlay);
    overlay.querySelector("#ws-skip").onclick = close;
    overlay.querySelector("#ws-go").onclick = async () => {
      const chosen = overlay.querySelector("input[name=ws-root]:checked");
      const path = chosen ? chosen.value : data.current;
      sessionStorage.setItem("ws-chosen", "1");        // set BEFORE reload so it won't re-prompt
      if (path === data.current) { overlay.remove(); return; }
      try {
        const r = await fetch("/api/root", { method: "POST",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path }) });
        if (!r.ok) { const j = await r.json(); alert(j.detail || "could not switch folder"); sessionStorage.removeItem("ws-chosen"); return; }
        location.reload();
      } catch (e) { alert(e.message); sessionStorage.removeItem("ws-chosen"); }
    };
  }

  window.Workspace = {
    promptOnFirstVisit,
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

  promptOnFirstVisit();   // prompt for the working folder on first load this session
})();
