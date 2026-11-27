// Shared helpers for every page.
const AIG = (() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (n) => (Number(n) || 0).toFixed(2);
  const $ = (sel, root = document) => root.querySelector(sel);

  async function api(path, opts = {}) {
    const res = await fetch("/api" + path, { credentials: "same-origin", ...opts });
    if (res.status === 401) { location.href = "/login"; throw new Error("Please sign in"); }
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch (_) {}
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    const type = res.headers.get("content-type") || "";
    return type.includes("application/json") ? res.json() : res;
  }
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  function toast(msg, ms = 3500) {
    const t = document.createElement("div");
    t.className = "toast"; t.textContent = msg; document.body.appendChild(t);
    setTimeout(() => t.remove(), ms);
  }

  function header(active) {
    const links = [["/", "Snap receipt"], ["/expenses.html", "Expenses"], ["/summary.html", "Tax summary"], ["/settings.html", "Settings"]];
    const h = document.createElement("header");
    h.innerHTML = `<strong id="biz">AIG expenses</strong>` +
      links.map(([href, label]) => `<a href="${href}"${href === active ? ' style="font-weight:600"' : ""}>${label}</a>`).join("") +
      `<a href="/logout" class="muted small" id="who">Sign out</a>`;
    document.body.prepend(h);
    api("/me").then((u) => { if (u.email !== "local") $("#who").textContent = `Sign out ${u.name}`; else $("#who").remove(); }).catch(() => {});
  }

  // Phone photos are big: shrink to 2400px JPEG in the browser so uploads are quick on mobile data.
  async function shrink(file, max = 2400) {
    if (!file.type.startsWith("image/") || file.type === "image/gif") return file;
    try {
      const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
      const scale = Math.min(1, max / Math.max(bmp.width, bmp.height));
      if (scale === 1 && file.type === "image/jpeg" && file.size < 1500000) return file;
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(bmp.width * scale); canvas.height = Math.round(bmp.height * scale);
      canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.85));
      if (!blob) return file;
      return new File([blob], (file.name || "receipt").replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" });
    } catch (_) { return file; }
  }

  async function uploadOne(file) {
    const fd = new FormData();
    const f = await shrink(file);
    fd.append("files", f, f.name || "receipt.jpg");
    const res = await api("/upload", { method: "POST", body: fd });
    return res.results[0] || { status: "error", error: "Nothing uploaded" };
  }

  const params = new URLSearchParams(location.search);
  return { esc, money, $, api, post, toast, header, shrink, uploadOne, params };
})();
