// Admin Mini App for Mila: the week at a glance, people (stamps, freezes, achievements, wishes,
// reports, a message), week texts, facts, reminders, the change log. Every action here is the
// same service call the bot panel makes; the API refuses what the rules forbid (past weeks).
(function () {
  const RM = window.RM;
  const esc = RM.escape, fmt = RM.fmtDate;
  const $ = (id) => document.getElementById(id);
  const screen = $("screen"), tabbar = $("tabbar");
  const FIELDS = [["title", "Название", "input"], ["intro", "Вступление", "textarea"], ["task_min", "Минимум", "textarea"], ["task_max", "Максимум", "textarea"], ["word", "Слово недели", "input"], ["word_ru", "Произношение", "input"], ["word_meaning", "Значение слова", "textarea"]];
  const FREEZE_REASONS = [["comment", "💬 За комментарий в канале"], ["meetup", "🤝 За приход на встречу"], ["friend", "🧭 За приведённого друга"], ["manual", "❄️ Просто от меня"]];
  const SOURCE = { bot: "из бота", app: "из приложения", out_of_week: "между неделями", not_report: "«это не отчёт»" };
  const PEOPLE_FILTERS = [["all", "Все"], ["nostamp", "Без штампа на неделе"], ["silent", "Взялись и молчат"]];
  const state = { tab: "week", me: null, weeks: [], catalogue: [], participants: [], week: null, filter: "all", unanswered: 0, personChanged: false, onSheetClose: null };

  boot();

  async function boot() {
    await RM.openSession();
    try {
      state.me = await RM.api("/api/me");
    } catch (e) {
      return fatal(e.status === 401 ? "Открой админку из бота: нажми «⚙️ Мила» → «🛠 Открыть админку»." : e.message);
    }
    if (!state.me.is_admin) return fatal("Эта страница только для Милы.");
    try {
      state.weeks = await RM.api("/api/admin/weeks");
      state.catalogue = await RM.api("/api/admin/achievement-types");
    } catch (e) {
      return fatal(e.status === 404 ? "Активного сезона нет. Напиши Диме — он включит новый сезон." : e.message);
    }
    tabbar.hidden = false;
    tabbar.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => go(b.dataset.tab)));
    $("sheet-close").addEventListener("click", closeSheet);
    $("sheet").addEventListener("click", (e) => { if (e.target === $("sheet")) closeSheet(); });
    refreshBadge();
    go("week");
  }

  async function refreshBadge() {
    try { const r = await RM.api("/api/admin/letters"); setBadge(r.unanswered); } catch (e) { /* the tab still opens */ }
  }
  function setBadge(n) {
    state.unanswered = n;
    const b = tabbar.querySelector('button[data-tab="letters"] .badge');
    if (b) { b.textContent = n; b.hidden = !n; }
  }

  function fatal(text) { screen.innerHTML = `<div class="empty"><div class="big">🔒</div><h2>Не открылось</h2><p class="muted">${esc(text)}</p></div>`; }
  function loading() { return RM.spinner(); }
  function go(tab) {
    state.tab = tab;
    const lit = tab === "facts" ? "more" : tab; // facts live under «Ещё»
    tabbar.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.tab === lit));
    window.scrollTo(0, 0);
    closeSheet();
    const render = { week: renderWeek, people: renderPeople, letters: renderLetters, content: renderContent, facts: renderFacts, more: renderMore }[tab];
    guarded(render, () => go(tab));
  }
  // A request that failed inside a screen: the message and a retry, in place.
  function failed(box, e, retry) {
    box.innerHTML = `<div class="empty"><div class="big">😕</div><h2>Не загрузилось</h2><p class="muted">${esc(e && e.message ? e.message : String(e))}</p><button class="btn soft small" id="retry">Попробовать ещё раз</button></div>`;
    $("retry").addEventListener("click", retry);
  }
  // A screen that throws says so and offers a retry instead of leaving a spinner forever.
  function guarded(render, retry) {
    return Promise.resolve().then(render).catch((e) => {
      console.error(e);
      screen.innerHTML = `<div class="empty"><div class="big">😕</div><h2>Не загрузилось</h2><p class="muted">${esc(e && e.message ? e.message : String(e))}</p><button class="btn soft small" id="retry">Попробовать ещё раз</button></div>`;
      $("retry").addEventListener("click", retry);
    });
  }
  const currentWeek = () => state.weeks.find((w) => w.state === "current");
  const weekOptions = (selected) => state.weeks.map((w) => `<option value="${w.number}" ${w.number === selected ? "selected" : ""}>${w.number}. ${esc(w.title)}${w.state === "current" ? " · идёт" : w.state === "locked" ? " · 🔒" : ""}</option>`).join("");
  const plain = (s) => String(s || "").replace(/<[^>]+>/g, "");

  // --- Неделя: сводка ------------------------------------------------------------------

  async function renderWeek(number) {
    const cur = currentWeek();
    const pick = number || (state.week && state.week.week_number) || (cur ? cur.number : state.weeks[0] && state.weeks[0].number);
    screen.innerHTML = `<header class="screen-head"><p class="eyebrow">Сводка недели</p><h1>Неделя</h1></header><label>Какая неделя<select id="week-pick">${weekOptions(pick)}</select></label><div id="week-body">${loading()}</div>`;
    $("week-pick").addEventListener("change", () => { const n = +$("week-pick").value; guarded(() => renderWeek(n), () => renderWeek(n)); });
    let s;
    try { s = await RM.api("/api/admin/summary?week=" + pick); } catch (e) { failed($("week-body"), e, () => renderWeek(pick)); return; }
    state.week = s;
    $("week-body").innerHTML = `
      <div class="tiles" style="margin-top:12px">
        <div class="tile"><div class="big">${s.submitted.length} <span class="muted">/ ${s.members_total}</span></div><div class="label">сдали из тех, кто в боте</div></div>
        <div class="tile"><div class="big">${s.took.length}</div><div class="label">нажали «берусь» или «попробую»</div></div>
        <div class="tile"><div class="big">${s.core_current} <span class="muted">/ ${s.core_best}</span></div><div class="label">в ядре сейчас / были за сезон · две недели подряд</div></div>
        <div class="tile"><div class="big">${s.reports_total}</div><div class="label">${RM.plural(s.reports_total, "отчёт", "отчёта", "отчётов")} за неделю</div></div>
      </div>
      <div class="card"><h2>${s.week_number}. ${esc(s.week_title)}</h2>
        <h3>Сдали (${s.submitted.length})</h3>${s.submitted.length ? `<div class="chips">${s.submitted.map((x) => `<span class="chip ${x.level === "max" ? "star" : "ok"}">${x.level === "max" ? "⭐" : "✅"} ${esc(x.name)}</span>`).join("")}</div>` : `<p class="muted">Пока никто.</p>`}
        <h3>Взялись, но не прислали (${s.took_not_submitted.length})</h3>${s.took_not_submitted_names.length ? `<div class="chips">${s.took_not_submitted_names.map((n) => `<span class="chip">${esc(n)}</span>`).join("")}</div>${s.week_ended ? `<p class="note" style="margin-top:8px">Неделя прошла — напоминать уже не о чем.</p>` : `<button class="btn soft small" id="remind" style="margin-top:10px">⏰ Напомнить им сейчас</button>`}` : `<p class="muted">Таких нет.</p>`}
      </div>
      ${s.draft_post ? `<div class="card"><div class="row between"><h2 style="margin:0">Черновик «Привала»</h2><button class="btn soft small" id="copy">Скопировать</button></div><p class="muted small">Неделя ${s.week_number} · ${esc(s.week_title)} · выложить в воскресенье в 20:00</p><pre class="draft" id="draft" style="margin-top:6px">${esc(s.draft_post)}</pre><p class="note">В квадратных скобках — что дописать руками. ${(s.draft_notes || []).length ? `Не для поста: ${s.draft_notes.map(esc).join(" · ")}` : ""}</p></div>` : `<div class="card"><h2 style="margin:0">Черновик «Привала»</h2><p class="muted" style="margin:6px 0 0">${(s.draft_notes || []).map(esc).join(" · ") || "Появится, когда неделя начнётся."}</p></div>`}`;
    if ($("remind")) $("remind").addEventListener("click", () => remindNow(s.week_number, s.week_title));
    if ($("copy")) $("copy").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(s.draft_post); RM.toast("Скопировала"); }
      catch (e) { const r = document.createRange(); r.selectNodeContents($("draft")); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); RM.toast("Выдели и скопируй текст"); }
    });
  }

  async function remindNow(weekNumber, weekTitle) {
    const about = weekNumber ? `за неделю ${weekNumber} «${weekTitle}»` : "за текущую неделю";
    if (!(await RM.confirm(`Напомнить всем, кто взялся ${about} и ещё не прислал отчёт?`))) return;
    try { await RM.api("/api/admin/remind", { method: "POST", body: { week_number: weekNumber || null } }); RM.toast("Отправляю — бот напишет тебе, сколько ушло"); }
    catch (e) { RM.toast(e.status === 409 ? "Эта неделя уже прошла — напоминать не о чем" : e.message, 4000); }
  }

  // --- Люди ----------------------------------------------------------------------------

  async function renderPeople() {
    const cur = currentWeek();
    screen.innerHTML = `<header class="screen-head"><p class="eyebrow">Участники</p><h1>Люди</h1></header><input id="people-q" placeholder="Найти по имени или нику">
      ${cur ? `<div class="tabs filters" id="people-filters">${PEOPLE_FILTERS.map(([v, l]) => `<button data-f="${v}" class="${state.filter === v ? "active" : ""}">${l}</button>`).join("")}</div>` : ""}
      <div id="people-list">${loading()}</div>`;
    try { state.participants = await RM.api("/api/admin/participants"); } catch (e) { failed($("people-list"), e, renderPeople); return; }
    if (!cur) state.filter = "all"; // the week filters make no sense between weeks, and there is no UI to reset them
    if (!state.participants.length) { $("people-list").innerHTML = `<div class="empty"><div class="big">👋</div><h2>Пока никого</h2><p class="muted">Люди появятся здесь после первого /start в боте.</p></div>`; return; }
    const weekMark = (p) => {
      if (!cur) return "";
      const stamp = p.week_level === "max" ? "⭐" : p.week_level === "min" ? "✅" : "";
      const intent = p.week_intent === "take" ? "берусь" : p.week_intent === "try" ? "попробую" : p.week_intent === "skip" ? "мимо" : "";
      const reports = p.week_reports ? `${p.week_reports} ${RM.plural(p.week_reports, "отчёт", "отчёта", "отчётов")}, штамп снят` : "";
      return `<div class="sub">неделя ${cur.number}: ${stamp ? stamp + " есть штамп" : reports ? reports : intent ? intent + " · пока без отчёта" : "без ответа"}</div>`;
    };
    const draw = () => {
      const q = $("people-q").value.trim().toLowerCase();
      const rows = state.participants.filter((p) => (!q || name(p).toLowerCase().includes(q)) && passes(p));
      const total = state.participants.length;
      $("people-list").innerHTML = rows.length ? `<p class="muted small">${rows.length === total ? `${total} ${RM.plural(total, "человек", "человека", "человек")}` : `${rows.length} из ${total}`} · нажми, чтобы открыть</p><ul class="list">${rows.map((p) => `<li data-id="${p.id}" style="cursor:pointer"><span class="body"><div class="person-row"><div class="avatar">${esc(initials(p))}</div><div><div class="name">${esc(name(p))}</div><div class="sub">${esc(RM.levelLabel(p.level))} · заморозок ${p.freezes_left}/${p.freezes_total}</div>${weekMark(p)}</div><div class="stats">${p.stamps} ${RM.plural(p.stamps, "штамп", "штампа", "штампов")}${p.stamps_max ? ` · ⭐ ${p.stamps_max}` : ""}<br>цепочка ${p.current_streak} / ${p.best_streak}</div></div></span></li>`).join("")}</ul>` : `<p class="muted">${q ? "Никого не нашла." : state.filter === "silent" ? "Все, кто взялся, уже прислали отчёт." : "У всех есть штамп — редкая неделя."}</p>`;
      $("people-list").querySelectorAll("li[data-id]").forEach((li) => li.addEventListener("click", () => openPerson(+li.dataset.id)));
    };
    const passes = (p) => state.filter === "nostamp" ? !p.week_level : state.filter === "silent" ? (p.week_intent === "take" || p.week_intent === "try") && !p.week_level : true;
    $("people-q").addEventListener("input", draw);
    if ($("people-filters")) $("people-filters").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      state.filter = b.dataset.f;
      $("people-filters").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
      draw();
    }));
    draw();
  }
  function name(p) { return ([p.first_name, p.last_name].filter(Boolean).join(" ") + (p.username ? " (@" + p.username + ")" : "")) || String(p.id); }
  function initials(p) { return ((p.first_name || "")[0] || (p.username || "")[0] || "?").toUpperCase(); }

  async function openPerson(id) {
    openSheet("Участник", loading(), null, () => { if (state.personChanged && state.tab === "people") renderPeople(); state.personChanged = false; });
    let d;
    try { d = await RM.api(`/api/admin/participants/${id}`); } catch (e) { $("sheet-body").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
    $("sheet-title").textContent = name(d.user);
    const p = d.passport;
    $("sheet-body").innerHTML = `
      <div class="tiles">
        <div class="tile"><div class="big">${p.stamps} <span class="muted">/ ${p.weeks_total}</span></div><div class="label">штампов${p.stamps_max ? ` · ⭐ ${p.stamps_max}` : ""}</div></div>
        <div class="tile"><div class="big">${esc(RM.levelLabel(p.level))}</div><div class="label">статус · цепочка ${p.current_streak}/${p.best_streak}</div></div>
      </div>
      <div class="card"><h3 style="margin-top:0">Штампы по неделям</h3><div class="stampbar">${d.weeks.map((w) => `<button data-week="${w.number}" class="${w.level || ""}" title="${esc(w.title)}">${w.number} ${w.state === "stamped" ? (w.level === "max" ? "⭐" : "✅") : (RM.stateMark[w.state] || "·")}</button>`).join("")}</div>
        <div id="stamp-pick" hidden></div>
        <p class="note">Нажми на неделю и выбери штамп. Ручной штамп важнее автоматического: отчёты его не перебивают.</p></div>
      <div class="card"><h3 style="margin-top:0">Заморозка · осталось ${p.freezes_left} из ${p.freezes_total}</h3>
        ${p.freeze_reasons.length ? `<p class="muted small">Заработано: ${p.freeze_reasons.map((r) => esc(RM.freezeReason[r] || r)).join(", ")}</p>` : ""}
        <div class="stack"><select id="freeze-reason">${FREEZE_REASONS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select><input id="freeze-note" placeholder="Заметка (необязательно)"><button class="btn small" id="freeze-btn">❄️ Дать заморозку</button></div></div>
      <div class="card"><h3 style="margin-top:0">Ачивка</h3>
        ${d.achievements.length ? `<div class="chips" style="margin-bottom:8px">${d.achievements.map((a) => `<span class="chip star">${esc(a)}</span>`).join("")}</div>` : ""}
        <div class="stack"><select id="badge-code">${state.catalogue.map((c) => `<option value="${esc(c.code)}">${esc(c.label)} — ${esc(c.description)}</option>`).join("")}</select><input id="badge-free" placeholder="…или своя, текстом"><button class="btn small" id="badge-btn">🏅 Выдать</button></div></div>
      <div class="card"><h3 style="margin-top:0">Пожелание в журнал</h3><p class="note">Несколько слов лично от тебя — попадут в PDF-журнал в конце сезона.</p><textarea id="wish">${esc(d.wish || "")}</textarea><button class="btn small" id="wish-btn" style="margin-top:8px">Сохранить</button></div>
      <div class="card"><h3 style="margin-top:0">Написать в бота</h3><p class="note">Придёт в чат с ботом с пометкой «Сообщение от Милы». Ответить реплаем на отчёт в своём чате — то же самое.</p><textarea id="msg"></textarea><button class="btn small" id="msg-btn" style="margin-top:8px">Отправить</button></div>
      <h3>Отчёты (${d.reports.length})</h3>
      ${d.reports.map((r) => `<article class="report"><div class="meta">${r.week_number ? "Неделя " + r.week_number : "вне недели"} · ${RM.fmtDateTime(r.created_at)} · ${r.level === "max" ? "⭐" : "✅"} ${esc(r.kind)}${r.edited_at ? ` · <span class="edited">изменено ${RM.fmtDateTime(r.edited_at)}</span>` : ""}</div>${r.text ? `<div class="text">${esc(r.text)}</div>` : ""}${r.media.length ? `<div class="gallery">${r.media.map((m) => m.mime && m.mime.startsWith("image/") && m.downloaded ? `<a href="${m.url}" target="_blank"><img src="${m.url}" alt="" loading="lazy"></a>` : (m.downloaded ? `<a class="btn ghost small" href="${m.url}" target="_blank">файл</a>` : `<span class="muted small">файл ещё не скачан</span>`)).join("")}</div>` : ""}</article>`).join("") || '<p class="muted">Отчётов нет.</p>'}
      ${d.words.length ? `<h3>Слова</h3><div class="chips">${d.words.map((w) => `<span class="chip">${esc(w.word)}${w.meaning ? " — " + esc(w.meaning) : ""}</span>`).join("")}</div>` : ""}`;
    const body = $("sheet-body");
    body.querySelectorAll(".stampbar button").forEach((b) => b.addEventListener("click", () => {
      const week = d.weeks.find((w) => String(w.number) === b.dataset.week);
      if (week.state === "locked") return RM.toast(`Неделя ${week.number} ещё не началась — штамп за неё поставить нельзя`, 3500);
      const current = week.state === "stamped" ? week.level : null;
      body.querySelectorAll(".stampbar button").forEach((x) => x.classList.toggle("picked", x === b));
      const pick = $("stamp-pick");
      pick.hidden = false;
      pick.innerHTML = `<p class="muted small" style="margin:8px 0 4px">Неделя ${week.number} · ${esc(week.title)} · сейчас ${current === "max" ? "⭐ максимум" : current === "min" ? "✅ минимум" : "без штампа"}</p>
        <div class="segment" style="margin-top:0">${[["", "Нет"], ["min", "✅ Минимум"], ["max", "⭐ Максимум"]].map(([v, l]) => `<button data-level="${v}" class="${(current || "") === v ? "active" : ""}">${l}</button>`).join("")}</div>`;
      pick.querySelectorAll("button").forEach((x) => x.addEventListener("click", async () => {
        const level = x.dataset.level || null;
        if (level === current) return;
        if (!level && !(await RM.confirm(`${name(d.user)} — снять штамп за неделю ${week.number}? Цепочка и статус пересчитаются.`))) return;
        try { await RM.api(`/api/admin/participants/${id}/stamps/${week.number}`, { method: "PUT", body: { level } }); RM.haptic("success"); state.personChanged = true; RM.toast(level ? "Штамп: " + (level === "max" ? "максимум ⭐" : "минимум ✅") : "Штамп снят"); openPerson(id); } catch (e) { RM.toast(e.status === 409 ? "Эта неделя ещё не началась" : e.message); }
      }));
    }));
    $("freeze-btn").addEventListener("click", async () => {
      try { const r = await RM.api(`/api/admin/participants/${id}/freezes`, { method: "POST", body: { reason: $("freeze-reason").value, note: $("freeze-note").value.trim() || null } }); state.personChanged = state.personChanged || r.granted; RM.toast(r.granted ? `Выдала, всего ${r.freezes_total}. Человеку придёт сообщение в бота` : "Уже потолок — пять"); openPerson(id); } catch (e) { RM.toast(e.message); }
    });
    $("badge-btn").addEventListener("click", async () => {
      const code = $("badge-free").value.trim() || $("badge-code").value;
      try { const r = await RM.api(`/api/admin/participants/${id}/achievements`, { method: "POST", body: { code_or_text: code } }); RM.toast(r.created ? "Выдала: " + r.label + ". Человеку придёт сообщение в бота" : "Такая уже есть"); openPerson(id); } catch (e) { RM.toast(e.message); }
    });
    $("wish-btn").addEventListener("click", async () => {
      const text = $("wish").value.trim();
      if (!text) return RM.toast("Пустое пожелание не сохраню");
      try { await RM.api(`/api/admin/participants/${id}/wish`, { method: "PUT", body: { text } }); RM.toast("Записала"); } catch (e) { RM.toast(e.message); }
    });
    $("msg-btn").addEventListener("click", async () => {
      const text = $("msg").value.trim();
      if (!text) return RM.toast("Напиши текст");
      try { await RM.api(`/api/admin/participants/${id}/message`, { method: "POST", body: { text } }); $("msg").value = ""; RM.toast("Отправляю через бота"); } catch (e) { RM.toast(e.message); }
    });
  }

  // --- Письма: всё, что пришло не отчётом ----------------------------------------------

  async function renderLetters() {
    screen.innerHTML = `<header class="screen-head"><p class="eyebrow">Входящие</p><h1>Письма</h1><p class="muted">«Написать Миле» из бота и приложения, сообщения между неделями и то, что человек пометил «это не отчёт». Ответ уходит в бота от твоего имени.</p></header><div id="letters-list">${loading()}</div>`;
    let r;
    try { r = await RM.api("/api/admin/letters"); } catch (e) { $("letters-list").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
    setBadge(r.unanswered);
    if (!r.letters.length) { $("letters-list").innerHTML = `<div class="empty"><div class="big">✉️</div><h2>Писем пока нет</h2><p class="muted">Здесь появится всё, что участники пришлют не как отчёт.</p></div>`; return; }
    $("letters-list").innerHTML = r.letters.map((l) => `<article class="letter ${l.replied_at ? "answered" : ""}" id="letter-${l.id}">
        <div class="meta"><b>${esc(l.author)}</b> · ${RM.fmtDateTime(l.created_at)} · ${SOURCE[l.source] || esc(l.source)}</div>
        <div class="text">${esc(l.text) || (l.media && l.media.length ? "" : '<span class="muted">без текста</span>')}</div>
        ${l.media && l.media.length ? `<div class="gallery">${l.media.map((m, i) => m.mime && m.mime.startsWith("image/") && m.downloaded ? `<a href="${m.url}" target="_blank"><img src="${m.url}" alt="Фото ${i + 1} из сообщения" loading="lazy"></a>` : (m.downloaded ? `<a class="btn ghost small" href="${m.url}" target="_blank">файл</a>` : `<span class="muted small">файл ещё не скачан</span>`)).join("")}</div>` : ""}
        ${l.replied_at ? `<div class="reply"><div class="meta">Ты ответила · ${RM.fmtDateTime(l.replied_at)}</div>${esc(l.reply_text || "")}</div>` : `<div class="row" style="margin-top:8px"><button class="btn soft small" data-reply="${l.id}">Ответить</button><a class="btn link small" href="#" data-person="${l.user_id}">Открыть участника</a></div><div class="replybox" hidden><textarea placeholder="Уйдёт в бота с пометкой «Мила ответила на твоё сообщение»"></textarea><button class="btn small" data-send="${l.id}" style="margin-top:8px">Отправить</button></div>`}
      </article>`).join("");
    const list = $("letters-list");
    list.querySelectorAll("[data-reply]").forEach((b) => b.addEventListener("click", () => { const box = b.closest(".letter").querySelector(".replybox"); box.hidden = !box.hidden; if (!box.hidden) box.querySelector("textarea").focus(); }));
    list.querySelectorAll("[data-person]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); openPerson(+a.dataset.person); }));
    list.querySelectorAll("[data-send]").forEach((b) => b.addEventListener("click", async () => {
      const text = b.closest(".replybox").querySelector("textarea").value.trim();
      if (!text) return RM.toast("Напиши ответ");
      b.disabled = true;
      try { await RM.api(`/api/admin/letters/${b.dataset.send}/reply`, { method: "POST", body: { text } }); RM.haptic("success"); RM.toast("Отправляю через бота"); renderLetters(); }
      catch (e) { b.disabled = false; RM.toast(e.message); }
    }));
  }

  // --- Задания: тексты недель ---------------------------------------------------------

  function renderContent() {
    screen.innerHTML = `<header class="screen-head"><p class="eyebrow">Тексты недель</p><h1>Задания</h1><p class="muted">Нажми на неделю. Правки видны в боте и в приложении сразу; прошедшие недели не редактируются — люди их уже прожили.</p></header>
      <ul class="list">${state.weeks.map((w) => `<li data-week="${w.id}" style="cursor:pointer"><span class="mark">${w.state === "current" ? "▶" : w.state === "locked" ? "🔒" : "✓"}</span><span class="body"><div class="title">${w.number}. ${esc(w.title)}</div><div class="sub">${fmt(w.starts_on)} — ${fmt(w.ends_on)} · ${w.state === "current" ? "идёт сейчас" : w.state === "locked" ? "ещё закрыта" : "прошла"}${w.word ? ` · ${esc(w.word)}` : ""}</div></span></li>`).join("")}</ul>`;
    screen.querySelectorAll("li[data-week]").forEach((li) => li.addEventListener("click", () => openWeekEditor(+li.dataset.week)));
  }

  function openWeekEditor(id) {
    const w = state.weeks.find((x) => x.id === id);
    const past = w.state === "stamped";
    openSheet(`Неделя ${w.number}`, `<p class="muted small">${fmt(w.starts_on)} — ${fmt(w.ends_on)}${past ? " · прошла, только чтение" : ""}</p>
      <form class="stack" id="week-form">${FIELDS.map(([f, label, kind]) => `<label>${label}${kind === "textarea" ? `<textarea name="${f}" ${past ? "readonly" : ""}>${esc(w[f])}</textarea>` : `<input name="${f}" value="${esc(w[f])}" ${past ? "readonly" : ""}>`}</label>`).join("")}
      ${past ? "" : `<button class="btn block" type="submit">Сохранить</button><p class="note">Каждое сохранение записывается в «Изменения»: что было и что стало.</p>`}</form>`, () => {
      $("week-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const body = {};
        FIELDS.forEach(([f]) => { const v = $("week-form").elements[f].value; if (v !== w[f]) body[f] = v; });
        if (!Object.keys(body).length) return RM.toast("Ничего не изменилось");
        try { Object.assign(w, await RM.api(`/api/admin/weeks/${w.id}`, { method: "PUT", body })); RM.haptic("success"); RM.toast("Сохранила"); closeSheet(); renderContent(); }
        catch (err) { RM.toast("Не сохранилось: " + err.message); }
      });
    });
  }

  // --- Факты ---------------------------------------------------------------------------

  async function renderFacts() {
    screen.innerHTML = `<header class="screen-head"><p class="eyebrow">Что мы узнали</p><h1>Факты</h1></header><div class="card"><div class="row"><input id="fact-text" placeholder="Новый факт про страну" style="flex:1"><button class="btn small" id="fact-add">Записать</button></div><p class="note">Факты участников подписаны их именем, твои — «Мила». Всё это попадёт в журналы сезона.</p></div><div id="facts-list">${loading()}</div>`;
    let facts;
    try { facts = await RM.api("/api/admin/facts"); } catch (e) { $("facts-list").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
    $("facts-list").innerHTML = facts.length ? `<ul class="list">${facts.map((f, i) => `<li><span class="mark">${i + 1}.</span><span class="body"><div>${esc(f.text)}</div><div class="sub">${f.author_name ? esc(f.author_name) : "Мила"} · ${fmt(f.created_at)}</div></span><button class="btn ghost small" data-del="${f.id}">убрать</button></li>`).join("")}</ul>` : `<p class="muted">Фактов пока нет.</p>`;
    $("fact-add").addEventListener("click", async () => { const text = $("fact-text").value.trim(); if (!text) return; try { await RM.api("/api/admin/facts", { method: "POST", body: { text } }); renderFacts(); } catch (e) { RM.toast(e.message); } });
    $("facts-list").querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => { if (!(await RM.confirm("Убрать факт из списка? Он скрывается, не удаляется."))) return; try { await RM.api(`/api/admin/facts/${b.dataset.del}`, { method: "DELETE" }); renderFacts(); } catch (e) { RM.toast(e.message); } }));
  }

  // --- Ещё: напоминания, изменения, как это работает ---------------------------------

  async function renderMore() {
    screen.innerHTML = `<header class="screen-head"><p class="eyebrow">Романтика маршрутов · админка</p><h1>Ещё</h1></header>
      <div class="card"><div class="row between"><h2 style="margin:0">Автонапоминания</h2><label class="toggle"><input type="checkbox" id="rem-toggle"> <span id="rem-state">…</span></label></div>
        <p class="note">Четверг 19:00 («впереди выходные») и воскресенье 12:00 («сегодня до 18:00»). Уходят только тем, кто нажал «Берусь» или «Попробую» и ещё не прислал отчёт. Нажавшим «В этот раз мимо» — ничего.</p>
        <button class="btn soft small" id="remind-now">⏰ Напомнить сейчас</button></div>
      <a class="card tight linkcard" href="#" id="open-facts"><div class="row between"><div style="flex:1;min-width:0"><b>💡 Факты про страну</b><div class="muted small">Что мы узнали за сезон — общий список для журналов.</div></div><span class="muted chevron">›</span></div></a>
      <details class="card"><summary>Как всё устроено</summary><div class="content helptext">
        <b>Отчёты</b> Человек присылает боту текст или фото — или отправляет их из приложения. Текст = минимум ✅, фото или видео = максимум ⭐. Копия приходит тебе в чат с шапкой «📨 Отчёт за неделю N от…»; ответь на неё реплаем — бот передаст автору.
        <b>Штампы</b> Ставятся сами по первому отчёту недели и никогда не понижаются (кроме «это не отчёт»). Ручной штамп ставишь во вкладке «Люди»; он важнее автоматического.
        <b>Заморозки</b> Две базовые, до пяти. Пропущенная неделя тратит одну сама. За слово в словарике и за первый максимум бот выдаёт сам; за комментарий, встречу и друга — ты, во вкладке «Люди».
        <b>Задания</b> Тексты недели правятся во вкладке «Задания» и появляются в боте сразу. Прошедшие недели закрыты.
        <b>Сводка</b> Вкладка «Неделя»: кто взялся, кто сдал, ядро (две недели подряд) и черновик «Привала».
        <b>Письма</b> Вкладка «Письма»: всё, что пришло не отчётом. Ответ отсюда или реплаем в чате — одно и то же, письмо помечается отвеченным.
        <b>Правки</b> Пока неделя идёт, человек может поправить свой отчёт в приложении: текст и файлы. Тебе приходит копия с шапкой «✏️ Правка отчёта», штамп пересчитывается по правилам отчётов.
        <b>Журналы</b> Каждый может собрать свой PDF в приложении в любой момент; на следующий день после конца сезона бот сам разошлёт журналы всем, у кого есть хоть один штамп. Пожелание в журнал пишется во вкладке «Люди».
        <b>Изменения</b> Всё, что ты меняешь здесь или в боте, записывается ниже: кто, что, когда, что было и что стало. Ничего не удаляется — только скрывается.
      </div></details>
      <details class="card" id="audit-box"><summary>Изменения</summary><div class="content" id="audit">${loading()}</div></details>
      <div class="actions"><a class="btn soft" href="/app${location.hash}">📱 Приложение участника</a></div>`;
    try {
      const r = await RM.api("/api/admin/reminders");
      $("rem-toggle").checked = r.enabled;
      $("rem-state").textContent = r.enabled ? "вкл" : "выкл";
    } catch (e) { $("rem-state").textContent = "?"; }
    $("rem-toggle").addEventListener("change", async () => {
      try { const r = await RM.api("/api/admin/reminders", { method: "PUT", body: { enabled: $("rem-toggle").checked } }); $("rem-state").textContent = r.enabled ? "вкл" : "выкл"; RM.toast(r.enabled ? "Автонапоминания включены" : "Автонапоминания выключены"); }
      catch (e) { RM.toast(e.message); }
    });
    $("remind-now").addEventListener("click", () => { const cur = currentWeek(); remindNow(cur && cur.number, cur && cur.title); });
    $("open-facts").addEventListener("click", (e) => { e.preventDefault(); go("facts"); });
    $("audit-box").addEventListener("toggle", loadAudit, { once: true });
  }

  async function loadAudit() {
    let rows;
    try { rows = await RM.api("/api/admin/audit?limit=100"); } catch (e) { $("audit").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
    $("audit").innerHTML = rows.length ? rows.map((r) => `<div class="audit-row" style="padding:8px 0;border-bottom:1px solid var(--line)"><div><b>${esc(auditAction(r))}</b> <span class="muted">· ${esc(r.actor_name || (r.actor_id ? "участник " + r.actor_id : "бот"))} · ${RM.fmtDateTime(r.created_at)}</span></div><div class="diff">${diff(r.before, r.after)}</div></div>`).join("") : `<p class="muted">Изменений пока нет.</p>`;
  }
  const AUDIT_ACTIONS = { "week.update": "Неделя изменена", "stamp.set": "Штамп поставлен вручную", "stamp.clear": "Штамп снят", "stamp.override": "Отчёт поднял ручной штамп", "fact.delete": "Факт убран", "season.activate": "Сезон включён", "report.edit": "Отчёт поправлен", "wish.set": "Пожелание записано" };
  const AUDIT_ENTITIES = { week: "неделя", stamp: "штамп", fact: "факт", season: "сезон", report: "отчёт", wish: "пожелание" };
  const AUDIT_FIELDS = { title: "название", intro: "вступление", task_min: "минимум", task_max: "максимум", word: "слово недели", word_ru: "транскрипция", word_meaning: "значение слова", level: "уровень", source: "источник", text: "текст", cleared_reports: "отчёты без штампа", added: "добавлено файлов", removed: "убрано файлов", edit_key: "ключ правки", note: "заметка", post_url: "ссылка на пост" };
  const AUDIT_VALUES = { max: "⭐ максимум", min: "✅ минимум", admin: "вручную", report: "по отчёту" };
  function auditAction(r) {
    const key = r.entity + "." + r.action;
    const what = r.entity === "stamp" && r.entity_id ? `неделя ${String(r.entity_id).split(":")[1] || r.entity_id}` : r.entity_id ? `${AUDIT_ENTITIES[r.entity] || r.entity} ${r.entity_id}` : "";
    return (AUDIT_ACTIONS[key] || key) + (what ? ` · ${what}` : "");
  }
  function diff(before, after) {
    const keys = [...new Set([...Object.keys(before || {}), ...Object.keys(after || {})])].filter((k) => k !== "edit_key");
    const value = (v) => Array.isArray(v) ? (v.length ? v.join(", ") : "—") : (v != null && AUDIT_VALUES[v]) || short(v);
    return keys.map((k) => `${esc(AUDIT_FIELDS[k] || k)}: ${esc(value((before || {})[k]))} → ${esc(value((after || {})[k]))}`).join("\n") || "—";
  }
  function short(v) { const s = v == null ? "—" : typeof v === "string" ? v : JSON.stringify(v); return s.length > 140 ? s.slice(0, 140) + "…" : s; }

  // --- sheet ---------------------------------------------------------------------------

  function openSheet(title, body, bind, onClose) {
    $("sheet-title").textContent = title;
    $("sheet-body").innerHTML = body;
    $("sheet").hidden = false;
    document.body.style.overflow = "hidden";
    state.onSheetClose = onClose || null;
    RM.onBack(closeSheet);
    if (bind) bind();
  }
  function closeSheet() {
    if ($("sheet").hidden) return;
    $("sheet").hidden = true;
    document.body.style.overflow = "";
    RM.onBack(null);
    const done = state.onSheetClose;
    state.onSheetClose = null;
    if (done) done();
  }
})();
