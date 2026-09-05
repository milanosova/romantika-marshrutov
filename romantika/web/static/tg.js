// Shared Mini App plumbing: who we are for the API, theme, back button, helpers.
// Loaded synchronously after the vendored telegram-web-app.js, before the page scripts.
(function () {
  const RM = (window.RM = window.RM || {});
  const tg = window.Telegram && window.Telegram.WebApp;

  // initData is what the API trusts. Telegram passes it in the URL hash (tgWebAppData); the
  // bridge script copies it to sessionStorage for reloads. `?init=` is the dev/test way in.
  function fromHash() {
    try {
      const hash = location.hash.replace(/^#/, "");
      if (!hash) return "";
      return new URLSearchParams(hash).get("tgWebAppData") || "";
    } catch (e) { return ""; }
  }
  function fromStorage() {
    try {
      const raw = sessionStorage.getItem("__telegram__initParams");
      if (!raw) return "";
      return JSON.parse(raw).tgWebAppData || "";
    } catch (e) { return ""; }
  }
  RM.initData = function () {
    if (tg && tg.initData) return tg.initData;
    return fromHash() || fromStorage() || new URLSearchParams(location.search).get("init") || "";
  };
  RM.inTelegram = !!(tg && (tg.initData || fromHash()));
  RM.tg = tg || null;

  RM.api = async function (path, options) {
    const opts = Object.assign({ headers: {} }, options || {});
    opts.headers = Object.assign({ "X-Telegram-Init-Data": RM.initData() }, opts.headers);
    if (opts.body && typeof opts.body !== "string" && !(opts.body instanceof FormData)) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    opts.credentials = "same-origin";
    const response = await fetch(path, opts);
    return RM._finish(response.status, response.ok, response.statusText, response.status === 204 ? "" : await response.text());
  };

  // Uploads go through XHR for the progress bar; same headers, same error shape.
  RM.upload = function (path, formData, onProgress, method) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open(method || "POST", path);
      xhr.setRequestHeader("X-Telegram-Init-Data", RM.initData());
      xhr.withCredentials = true;
      if (xhr.upload && onProgress) xhr.upload.addEventListener("progress", (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); });
      xhr.onload = () => { try { resolve(RM._finish(xhr.status, xhr.status >= 200 && xhr.status < 300, xhr.statusText, xhr.responseText)); } catch (e) { reject(e); } };
      xhr.onerror = () => reject(new Error("Сеть не ответила — попробуй ещё раз"));
      xhr.send(formData);
    });
  };

  RM._finish = function (status, ok, statusText, text) {
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = { detail: text }; }
    if (!ok) {
      const detail = data && data.detail;
      const err = new Error(typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : statusText || ("HTTP " + status));
      err.status = status;
      throw err;
    }
    return data;
  };

  // The cookie lets <img src="/media/..."> load without custom headers.
  RM.openSession = async function () {
    const init = RM.initData();
    if (!init) return null;
    try { return await RM.api("/api/session", { method: "POST", body: { init_data: init } }); } catch (e) { return null; }
  };

  RM.escape = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  };
  // Bot texts are server-made HTML with <b>/<i>/<code> only; anything else is dropped.
  RM.html = function (s) {
    return String(s == null ? "" : s)
      .replace(/<(?!\/?(?:b|i|code|u|s)>)[^>]*>/g, "")
      .replace(/\n/g, "<br>");
  };
  RM.fmtDate = function (iso) {
    const d = new Date(iso);
    return isNaN(d) ? String(iso || "") : `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}`;
  };
  RM.fmtDateTime = function (iso) {
    const d = new Date(iso);
    if (isNaN(d)) return String(iso || "");
    return RM.fmtDate(iso) + " " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  };
  RM.plural = function (n, one, two, many) {
    n = Math.abs(n) % 100;
    if (n >= 11 && n <= 14) return many;
    const r = n % 10;
    return r === 1 ? one : r >= 2 && r <= 4 ? two : many;
  };

  RM.levelName = { tourist: "Турист", traveler: "Путешественник", resident: "Резидент", "": "Ещё в пути" };
  RM.stateMark = { stamped: "✅", current: "▸", frozen: "❄️", missed: "◦", before_join: "◦", locked: "🔒" };
  RM.freezeReason = { word: "за своё слово в словарике", max: "за первый выполненный максимум", comment: "за комментарий в канале", meetup: "за приход на встречу", friend: "за приведённого друга", manual: "от Милы" };
  // The bot's dictionary wins when the API sends it (`texts.level_names`, `texts.freeze_reasons`).
  RM.adoptTexts = function (texts) {
    if (!texts) return;
    if (texts.level_names) Object.assign(RM.levelName, texts.level_names);
    if (texts.freeze_reasons) Object.assign(RM.freezeReason, texts.freeze_reasons);
  };
  RM.levelLabel = function (level) { return RM.levelName[level || ""] || RM.levelName[""]; };
  RM.spinner = function () { return '<div class="loading"><div class="spinner"></div></div>'; };
  RM.intentName = { take: "Берусь", try: "Попробую", skip: "В этот раз мимо" };

  RM.toast = function (text, ms) {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = text;
    el.hidden = false;
    clearTimeout(el._t);
    el._t = setTimeout(() => (el.hidden = true), ms || 2600);
  };
  RM.haptic = function (kind) {
    try {
      if (!tg || !tg.HapticFeedback) return;
      if (kind === "success" || kind === "error" || kind === "warning") tg.HapticFeedback.notificationOccurred(kind);
      else tg.HapticFeedback.impactOccurred(kind || "light");
    } catch (e) { /* older clients */ }
  };
  RM.alert = function (text) {
    try { if (tg && tg.showAlert && tg.isVersionAtLeast && tg.isVersionAtLeast("6.2")) return tg.showAlert(text); } catch (e) { /* fall through */ }
    RM.toast(text, 4000);
  };
  // Telegram's own dialog inside the client, the browser's outside; resolves to true/false.
  RM.confirm = function (text) {
    return new Promise((resolve) => {
      try { if (tg && tg.showConfirm && tg.isVersionAtLeast && tg.isVersionAtLeast("6.2")) return tg.showConfirm(text, (ok) => resolve(!!ok)); } catch (e) { /* fall through */ }
      resolve(window.confirm(text));
    });
  };
  // One id per attempt to send: the API answers a retry with the report it already made.
  RM.uid = function () {
    try { if (crypto.randomUUID) return crypto.randomUUID(); } catch (e) { /* older WebView */ }
    return Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  };

  // Back button: pages with history (the calendar opened from the app) go back with Telegram's
  // own button; the app itself registers a handler for its sheets via RM.onBack.
  RM._back = null;
  RM.onBack = function (handler) {
    RM._back = handler;
    RM._syncBack();
  };
  RM._syncBack = function () {
    if (!tg || !tg.BackButton) return;
    try {
      const show = !!RM._back || (history.length > 1 && document.body.dataset.back === "history");
      if (show) tg.BackButton.show(); else tg.BackButton.hide();
    } catch (e) { /* older clients */ }
  };

  // Legacy pages still use tab buttons + .tab sections.
  RM.tabs = function (container, onSelect) {
    const buttons = container.querySelectorAll("button[data-tab]");
    buttons.forEach((b) => b.addEventListener("click", () => {
      buttons.forEach((x) => x.classList.toggle("active", x === b));
      document.querySelectorAll(".tab").forEach((s) => (s.hidden = s.id !== b.dataset.tab));
      if (onSelect) onSelect(b.dataset.tab);
    }));
  };

  if (tg) {
    try {
      tg.ready();
      tg.expand();
      if (tg.disableVerticalSwipes) tg.disableVerticalSwipes();
      if (tg.colorScheme === "dark") document.documentElement.classList.add("dark");
      tg.onEvent && tg.onEvent("themeChanged", () => document.documentElement.classList.toggle("dark", tg.colorScheme === "dark"));
      const bg = getComputedStyle(document.documentElement).getPropertyValue("--tg-theme-secondary-bg-color").trim();
      if (bg && tg.setHeaderColor) { tg.setHeaderColor(bg); tg.setBackgroundColor(bg); }
      if (tg.BackButton) tg.BackButton.onClick(() => { if (RM._back) RM._back(); else history.back(); });
    } catch (e) { /* older clients */ }
    document.documentElement.classList.add("in-telegram");
    document.addEventListener("DOMContentLoaded", RM._syncBack);
    document.addEventListener("click", (event) => {
      const a = event.target.closest && event.target.closest('a[href^="https://t.me/"]');
      if (a && tg.openTelegramLink) { event.preventDefault(); tg.openTelegramLink(a.href); }
    });
  }
})();
