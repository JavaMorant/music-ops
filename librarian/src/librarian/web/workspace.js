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
      .ws-modal{position:fixed;inset:0;background:rgba(5,6,8,.72);-webkit-backdrop-filter:blur(4px);
        backdrop-filter:blur(4px);display:flex;align-items:center;justify-content:center;z-index:1000;padding:20px}
      .ws-card{background:#111318;border:1px solid #20242c;border-radius:14px;padding:24px;
        max-width:460px;width:100%;box-shadow:inset 0 1px 0 rgba(255,255,255,.04),0 24px 64px rgba(0,0,0,.55);
        font:14px/1.55 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
      .ws-h{margin:0 0 4px;font-size:17px;color:#e9ebf1;font-weight:650;letter-spacing:.01em}
      .ws-sub{margin:0 0 16px;font-size:13px;color:#99a0ac}
      .ws-list{display:flex;flex-direction:column;gap:6px;max-height:46vh;overflow:auto;margin-bottom:18px}
      .ws-opt{display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid #20242c;
        border-radius:9px;cursor:pointer;color:#e9ebf1;font-size:13px;
        transition:background .14s ease,border-color .14s ease}
      .ws-opt:hover{background:#181b21}
      .ws-opt:has(input:checked){border-color:#7da7ff;background:rgba(125,167,255,.08)}
      .ws-opt input{accent-color:#7da7ff}
      .ws-ico{width:18px;text-align:center}
      .ws-lab{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .ws-rb{font-size:10px;font-weight:700;color:#56d38f;border:1px solid rgba(86,211,143,.4);
        border-radius:5px;padding:1px 6px;white-space:nowrap;letter-spacing:.04em}
      .ws-actions{display:flex;justify-content:space-between;align-items:center;gap:10px}
      .ws-skip{background:none;border:0;color:#99a0ac;font-size:12px;cursor:pointer}
      .ws-skip:hover{color:#e9ebf1}
      .ws-go{background:#7da7ff;color:#071019;border:0;border-radius:7px;padding:9px 18px;
        font-weight:600;cursor:pointer;font-size:13px;
        box-shadow:inset 0 1px 0 rgba(255,255,255,.2),0 1px 2px rgba(0,0,0,.3)}
      .ws-go:hover{filter:brightness(1.08)}
      @media (prefers-reduced-motion:reduce){.ws-opt,.ws-go{transition:none}}`;
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
