// Participant Mini App — one door, three tabs (DOMAIN §7, 14.09.2026): «Неделя» is what is
// happening now (day, task, intent, the form), «Рюкзак» is what the person has gathered
// (passport, achievements, journal with PDF), «Карта» is what Mila made (weeks, words, facts,
// about). Rules and texts come from the API (the same services and `texts/ru.py` the bot
// uses); this file only draws. Old tab names still route (links in old messages).
(function () {
  const RM = window.RM;
  const esc = RM.escape, html = RM.html, fmt = RM.fmtDate, kindName = RM.kindName;
  const $ = (id) => document.getElementById(id);
  const app = $("app"), screen = $("screen"), tabbar = $("tabbar");
  const MAX_FILES = 10, MAX_BYTES = 50 * 1024 * 1024, MAX_TOTAL = 200 * 1024 * 1024; // the API's limits (routes/api.py), checked here first
  const TAB_ALIASES = { today: "week", passport: "bag", journal: "bag", words: "season", more: "season", map: "season" };
  const state = { tab: TAB_ALIASES[app.dataset.tab] || app.dataset.tab || "week", home: null, journal: null, files: [], clientId: null };

  boot();

  async function boot() {
    await RM.openSession();
    try {
      state.home = await RM.api("/api/home");
    } catch (e) {
      return fatal(e);
    }
    tabbar.hidden = false;
    tabbar.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => go(b.dataset.tab)));
    $("sheet-close").addEventListener("click", closeSheet);
    $("sheet").addEventListener("click", (e) => { if (e.target === $("sheet")) closeSheet(); });
    go(state.tab);
  }

  function fatal(e) {
    const bot = app.dataset.bot;
    let text, hint = "";
    if (e.status === 401) {
      text = "Открой приложение из бота";
      hint = "Telegram передаёт, кто ты, только внутри самого Telegram: нажми кнопку «Открыть» в чате с ботом" + (bot ? ` <a href="https://t.me/${esc(bot)}">@${esc(bot)}</a>` : "") + ".";
    } else if (e.status === 404) {
      text = "Сезон ещё не начался";
      hint = "Как только рандомайзер выберет страну, здесь появится задание.";
    } else {
      text = "Не получилось загрузить";
      hint = esc(e.message) + ". Попробуй закрыть и открыть приложение ещё раз.";
    }
    screen.innerHTML = `<div class="empty"><div class="big">🧭</div><h2>${text}</h2><p class="muted">${hint}</p></div>`;
  }

  function go(tab) {
    tab = TAB_ALIASES[tab] || tab;
    state.tab = tab;
    tabbar.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    window.scrollTo(0, 0);
    RM.haptic("light");
    render();
  }

  async function refreshHome() {
    try { state.home = await RM.api("/api/home"); RM.adoptTexts(state.home.texts); } catch (e) { /* keep what we have */ }
  }

  function render() {
    closeSheet();
    return ({ week: renderWeek, bag: renderBag, season: renderSeason })[state.tab]();
  }

  // --- Неделя --------------------------------------------------------------------------

  function renderWeek() {
    const h = state.home, w = h.week, t = h.today;
    const dateTitle = new Date(t.date + "T12:00:00").toLocaleDateString("ru-RU", { day: "numeric", month: "long", weekday: "long" });
    // The position in the calendar, not the number: numbers may have gaps once Mila edits the season.
    const position = w ? h.weeks.findIndex((x) => x.id === w.id) + 1 : 0;
    const status = w ? `Неделя ${w.number}${position && position !== w.number ? ` · ${position} из ${h.weeks.length}` : ` из ${h.weeks.length}`} · дедлайн задания ${esc(w.deadline)}` : h.next_week_starts_on ? `Между неделями · следующая с ${fmt(h.next_week_starts_on)}` : "Сезон завершён";
    let out = `<header class="screen-head"><p class="eyebrow">Романтика маршрутов</p><h1 class="season-title">${esc(h.season.title)}</h1><p class="date-line">${esc(capital(dateTitle))}</p><p class="muted">${status}</p></header>`;
    // The day comes first: a reason to open the app on a Wednesday. The week's word lives in the task card.
    out += dayCard(t);

    if (w) {
      out += `<div class="card current">
        <p class="eyebrow">Задание недели</p>
        <h2>${esc(w.title)}</h2>
        ${w.intro ? `<p>${esc(w.intro)}</p>` : ""}
        <div class="kv">
          <div><div class="k">Минимум ✅ · на пять минут</div><div class="v">${esc(w.task_min)}</div></div>
          ${w.task_max ? `<div><div class="k">Максимум ⭐ · на вечер</div><div class="v">${esc(w.task_max)}</div></div>` : ""}
        </div>
        ${w.word ? `<div class="divider"></div><div class="k" style="font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)">Слово недели</div><div class="wordline">${esc(w.word)}${w.word_ru ? ` <span class="ru">· ${esc(w.word_ru)}</span>` : ""}</div>${w.word_meaning ? `<div class="muted"><i>${esc(w.word_meaning)}</i></div>` : ""}` : ""}
        ${w.level ? `<p class="note" style="margin-top:14px">${w.level === "max" ? "⭐ Максимум за эту неделю уже в паспорте." : "✅ Минимум за эту неделю уже в паспорте — фото поднимут его до максимума."}</p>` : `<h3>Берёшься?</h3>
        <div class="segment" id="intent">${["take", "skip"].map((c) => `<button data-choice="${c}" class="${w.intent === c || (c === "take" && w.intent === "try") ? "active" : ""}">${RM.intentName[c]}</button>`).join("")}</div>
        <p class="note" id="intent-note">${w.intent ? intentNote(w.intent) : "Напоминания приходят только тем, кто нажал «Берусь»."}</p>`}
      </div>`;
      out += `<div class="card composer" id="composer">${composerHtml(w)}</div>`;
    } else {
      const next = h.next_week_starts_on ? h.weeks.find((x) => x.starts_on === h.next_week_starts_on) : null;
      out += `<div class="card"><h2>Сейчас неделя не идёт</h2><p class="muted">${h.next_week_starts_on ? `Задание ${next ? `недели ${next.number} ` : ""}придёт в понедельник, ${fmt(h.next_week_starts_on)}.` : "Сезон завершён. Спасибо тебе за него."}</p></div>`;
      out += `<div class="card composer" id="composer">${composerHtml(null)}</div>`;
    }

    screen.innerHTML = out;

    if (w && !w.level) bindIntent(w);
    bindComposer(w);
  }

  // «Поправить» and «Это не отчёт» on a report card; `done` redraws whatever the card sits in.
  function bindReportActions(box, j, done) {
    box.querySelectorAll("[data-cancel]").forEach((b) => b.addEventListener("click", async () => {
      const late = j.reports.some((x) => String(x.id) === b.dataset.cancel && x.late);
      if (!(await RM.confirm(late ? NOT_REPORT_CONFIRM_LATE : NOT_REPORT_CONFIRM))) return;
      try { const res = await RM.api(`/api/reports/${b.dataset.cancel}/cancel`, { method: "POST", body: {} }); RM.toast(res.message.replace(/<[^>]+>/g, "")); await done(); } catch (e) { RM.toast(e.message); }
    }));
    box.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => {
      const report = j.reports.find((x) => String(x.id) === b.dataset.edit);
      if (report) openEditor(report, done);
    }));
  }

  function capital(s) { return s.charAt(0).toUpperCase() + s.slice(1); }
  // Bot texts open with their own bold heading; inside an accordion the summary already is one.
  function unhead(s) { return String(s || "").replace(/^\s*<b>[^<]*<\/b>\s*/, ""); }

  // One line: the Maya day and what it means (Mila, 18.09: half the size, no memory word, no disclaimer).
  function dayCard(t) {
    const tz = t.tzolkin;
    if (!tz) return "";
    return `<div class="card day tight">
      <p class="muted daylabel">день по календарю майя</p>
      <p class="daytitle"><b>${tz.number} ${esc(tz.sign_name)}</b> <span class="muted">· ${esc(tz.sign_symbol)}</span></p>
      <p class="advice">${esc(tz.day_advice)}</p>
      ${t.calendar_url ? `<a class="btn link small daylink" href="${esc(t.calendar_url)}" id="calendar-link">Узнай своё предназначение →</a>` : ""}
    </div>`;
  }

  function intentNote(choice) {
    // «try» is what people pressed before 19.09: it reads as «берусь» now.
    return { take: "Записала: берёшься 💪 Как сделаешь — пришли отчёт ниже.", try: "Записала: берёшься 💪 Как сделаешь — пришли отчёт ниже.", skip: "Хорошо, неделя может не задаться. Напоминаний не будет." }[choice];
  }

  function bindIntent(w) {
    $("intent").querySelectorAll("button").forEach((b) => b.addEventListener("click", async () => {
      const choice = b.dataset.choice;
      if (b.classList.contains("active")) return; // the same answer again: nothing to send
      $("intent").querySelectorAll("button").forEach((x) => (x.disabled = true));
      try {
        const r = await RM.api("/api/intent", { method: "POST", body: { week_number: w.number, choice } });
        w.intent = choice;
        $("intent").querySelectorAll("button").forEach((x) => { x.classList.toggle("active", x.dataset.choice === choice); x.disabled = false; });
        $("intent-note").textContent = intentNote(choice);
        RM.haptic("success");
      } catch (e) {
        $("intent").querySelectorAll("button").forEach((x) => (x.disabled = false));
        RM.toast("Не записалось: " + e.message);
      }
    }));
  }

  function stampChip(w) {
    if (!w) return "";
    if (w.level === "max") return `<span class="chip star">⭐ максимум</span>`;
    if (w.level === "min") return `<span class="chip ok">✅ минимум</span>`;
    return `<span class="chip">пока без штампа</span>`;
  }

  // `late` — the week has ended: the report goes into the journal chapter only (DOMAIN §2).
  function composerHtml(w, late) {
    const title = late ? (late.again ? "Дописать в журнал" : "Добавить в журнал") : w ? "Сдать отчёт" : "Написать Миле";
    const note = late
      ? lateNote(late)
      : w
      ? "Текст — минимум ✅, фото или видео — максимум ⭐. Присылать можно сколько угодно раз."
      : "Неделя не идёт, штамп не ставится. Сообщение сохранится, и я его прочитаю.";
    return `<div class="row between"><h2 style="margin:0">${title}</h2>${late ? "" : stampChip(w)}</div>
      <p class="note">${note}</p>
      <textarea id="report-text" placeholder="${w ? "Что было на этой неделе?" : "Что хочешь сказать?"}"></textarea>
      <div class="attach"><label class="btn soft small" for="report-files">📷 Загрузить фото или видео</label><input id="report-files" type="file" accept="image/*,video/*" multiple><span class="muted small" id="files-count"></span></div>
      <div class="previews" id="previews" hidden></div>
      <div class="bar" id="bar" hidden><i></i></div>
      <button class="btn block" id="send" style="margin-top:12px">Отправить</button>`;
  }

  // The stamp decides the wording: a stamped week keeps its stamp, an unstamped one gets none.
  function lateNote(late) {
    return late.stamped
      ? "Штамп за неделю уже стоит, он не изменится. Текст и фото лягут в ту же главу журнала."
      : "Неделя прошла — штамп за неё уже не ставится, а в журнале сезона будет.";
  }

  function bindComposer(w, late) {
    state.files = [];
    state.clientId = RM.uid(); // one id per attempt: a retry after a lost answer finds the same report
    const input = $("report-files");
    input.addEventListener("change", () => { addFiles(state.files, input.files, 0); input.value = ""; renderPreviews(state.files, $("previews"), $("files-count")); });
    $("send").addEventListener("click", () => sendReport(w, late));
  }

  // Adds what fits within the API's limits and says what did not (D4). `taken` counts files
  // already on the report when editing.
  function addFiles(list, picked, taken) {
    let bytes = list.reduce((n, f) => n + f.size, 0), refused = [];
    for (const f of picked) {
      if (list.length + taken >= MAX_FILES) { refused.push("больше " + MAX_FILES + " файлов в одном отчёте нельзя"); break; }
      if (f.size > MAX_BYTES) { refused.push(`«${f.name}» больше 50 МБ`); continue; }
      if (bytes + f.size > MAX_TOTAL) { refused.push("вместе получается больше 200 МБ"); continue; }
      list.push(f); bytes += f.size;
    }
    if (refused.length) RM.toast("Не добавила: " + [...new Set(refused)].join("; ") + ". Остальное — следующим отчётом.", 4500);
  }

  function renderPreviews(list, box, counter) {
    box.hidden = list.length === 0;
    if (counter) counter.textContent = list.length ? `${list.length} ${RM.plural(list.length, "файл", "файла", "файлов")} · ${mb(list.reduce((n, f) => n + f.size, 0))}` : "";
    (box._urls || []).forEach((u) => URL.revokeObjectURL(u));
    box._urls = [];
    box.innerHTML = list.map((f, i) => {
      const url = URL.createObjectURL(f);
      box._urls.push(url);
      const media = f.type.startsWith("image/") ? `<img src="${url}" alt="">` : f.type.startsWith("video/") ? `<video src="${url}" muted></video>` : esc(f.name);
      return `<div class="pv">${media}<button class="x" data-i="${i}" aria-label="Убрать">✕</button></div>`;
    }).join("");
    box.querySelectorAll(".x").forEach((b) => b.addEventListener("click", () => { list.splice(+b.dataset.i, 1); renderPreviews(list, box, counter); }));
  }
  function mb(bytes) { return bytes < 1024 * 1024 ? Math.max(1, Math.round(bytes / 1024)) + " КБ" : (bytes / 1024 / 1024).toFixed(bytes > 10 * 1024 * 1024 ? 0 : 1) + " МБ"; }

  async function sendReport(w, late) {
    const text = $("report-text").value.trim();
    if (!text && !state.files.length) return RM.toast("Напиши хотя бы слово или добавь фото");
    const button = $("send"), bar = $("bar");
    button.disabled = true;
    button.textContent = "Отправляю…";
    const form = new FormData();
    form.append("text", text);
    form.append("client_id", state.clientId);
    if (late) form.append("week_number", String(w.number)); // a past week: journal only
    state.files.forEach((f) => form.append("files", f, f.name));
    if (state.files.length) bar.hidden = false;
    try {
      const r = await RM.upload("/api/reports", form, (p) => { bar.querySelector("i").style.width = Math.round(p * 100) + "%"; });
      RM.haptic("success");
      state.clientId = RM.uid();
      if (late) { late.done(r); return; }
      await refreshHome();
      renderWeek(); // the task card above changes too: the intent question gives way to the stamp
      showResult(r);
    } catch (e) {
      RM.haptic("error");
      button.disabled = false;
      bar.hidden = true;
      if (e.status === 422) { // the server refused what was typed: a retry as is will not help
        button.textContent = "Отправить";
        RM.toast(e.message, 4500);
      } else {
        button.textContent = "Отправить ещё раз";
        RM.toast("Не отправилось: " + e.message + ". Нажми ещё раз — второго отчёта не будет.", 4500);
      }
    }
  }

  const NOT_REPORT_CONFIRM = "Пометить как не отчёт? Штамп за неделю пересчитается, а текст останется у меня как обычное сообщение.";
  const NOT_REPORT_CONFIRM_LATE = "Убрать из журнала? Текст останется у меня как обычное сообщение; штампов это не касается.";

  function showResult(r) {
    const box = $("composer");
    const w = state.home.week;
    // A stamp never goes down (DOMAIN §2), so the only correction offered is upwards.
    const canRaise = !r.out_of_week && r.stamp_level === "min";
    box.innerHTML = `<div class="row between"><h2 style="margin:0">${r.out_of_week ? "Сохранила" : "Принято"}</h2>${stampChip(w)}</div>
      <div class="result ${r.out_of_week ? "" : "ok"}"><div class="rich">${html(r.message)}</div></div>
      ${canRaise ? `<div class="row" style="margin-top:12px"><button class="btn soft small" id="fix-level">⭐ Это был максимум</button></div>` : ""}
      <p class="note" style="margin:8px 0 0">${r.out_of_week ? "Отвечу в чат с ботом." : "Отчёт в журнале: «Рюкзак» → «Журнал». Там его можно поправить, пока неделя идёт, или пометить «это не отчёт»."}</p>
      <button class="btn link" id="again" style="margin-top:4px">Отправить ещё один</button>`;
    if ($("fix-level")) $("fix-level").addEventListener("click", async () => {
      try {
        const res = await RM.api(`/api/weeks/${r.week_number}/level`, { method: "POST", body: { level: "max" } });
        RM.alert(res.message.replace(/<[^>]+>/g, ""));
        if (res.ok) { r.stamp_level = res.stamp_level; r.level = "max"; r.message = res.message; await refreshHome(); renderWeek(); showResult(r); }
      } catch (e) { RM.toast(e.message); }
    });
    $("again").addEventListener("click", () => { box.innerHTML = composerHtml(w); bindComposer(w); });
  }

  // --- Рюкзак --------------------------------------------------------------------------

  async function renderBag() {
    const h = state.home, p = h.passport;
    const level = RM.levelLabel(p.level);
    let out = `<header class="screen-head"><p class="eyebrow">Рюкзак · ${esc(h.season.title)}</p><h1>${esc(h.user.first_name || "Паспорт")}</h1><p class="muted">${fmt(h.season.starts_on)} — ${fmt(h.season.ends_on)}</p></header>
      <div class="tiles">
        <div class="tile"><div class="big">${p.stamps} <span class="muted">/ ${p.weeks_total}</span></div><div class="label">${RM.plural(p.stamps, "штамп", "штампа", "штампов")}${p.stamps_max ? ` · ⭐ ${p.stamps_max}` : ""}</div></div>
        <div class="tile"><div class="big">${esc(level)}</div><div class="label">статус</div></div>
        <div class="tile"><div class="big">${p.freezes_left}</div><div class="label">${RM.plural(p.freezes_left, "заморозка", "заморозки", "заморозок")} · <a href="#" id="freezes-how">как заработать ещё?</a></div></div>
        <div class="tile"><div class="big">${p.current_streak}</div><div class="label">${RM.plural(p.current_streak, "неделя", "недели", "недель")} подряд · лучшая ${p.best_streak}</div></div>
      </div>
      <div class="card"><h3 style="margin-top:0">Недели</h3><div class="stamps">${h.weeks.map(stampHtml).join("")}</div>
        <ul class="legend">
          <li><span>⭐</span> максимум</li>
          <li><span>✅</span> минимум</li>
          <li><span>❄️</span> пропуск закрыт заморозкой</li>
          <li><span>◦</span> пропущена или была до твоего прихода</li>
          <li><span>▸</span> идёт сейчас</li>
          <li><span>🔒</span> откроется в понедельник</li>
        </ul>
        <p class="note" style="margin:8px 0 0">Нажми на неделю — откроется задание; за прошедшую можно добавить в журнал.</p></div>`;
    if (h.achievements.length) out += `<div class="card"><h3 style="margin-top:0">Ачивки</h3><div class="chips">${h.achievements.map((a) => `<span class="chip star">${esc(a)}</span>`).join("")}</div><p class="note" style="margin:8px 0 0">Не за посещаемость, а за поступок. Останутся в журнале сезона.</p></div>`;
    if (h.wish) out += `<div class="card accent"><h3 style="margin-top:0">От Милы</h3><p><i>${esc(h.wish)}</i></p></div>`;
    out += `<div id="mine-box">${loading()}</div>`; // own words and facts (DOMAIN §6)
    out += `<div id="journal-box">${loading()}</div>`;
    out += `<details class="card"><summary>Что будет в конце сезона</summary><div class="content richtext">${html(unhead(h.texts.end_of_season))}</div></details>`;
    screen.innerHTML = out;
    screen.querySelectorAll(".stamp").forEach((b) => b.addEventListener("click", () => openWeek(+b.dataset.n)));
    $("freezes-how").addEventListener("click", (e) => { e.preventDefault(); openFreezes(state.home.passport); });
    await Promise.all([renderMineInto($("mine-box")), renderJournalInto($("journal-box"))]);
  }

  // A person's own words and facts: seen only by them, kept for their journal (DOMAIN §6, 15.09.2026).
  async function renderMineInto(box) {
    const [dict, facts] = await Promise.allSettled([RM.api("/api/dictionary"), RM.api("/api/facts")]);
    if (!box.isConnected) return;
    // Personal entries have one copy on screen: a failed load must look like a failure, not like «пусто».
    const failed = [dict, facts].find((r) => r.status === "rejected");
    if (failed) { box.innerHTML = errorBox(failed.reason); return; }
    const words = dict.value.user_words;
    // Mila's own facts carry no author and are the club's shared ones (DOMAIN §6): her card says so.
    const isAdmin = !!state.home.user.is_admin;
    const mine = facts.value.facts.filter((x) => isAdmin ? !x.author : x.mine);
    const earned = state.home.passport.freeze_reasons || [];
    const firstWord = !earned.includes("word"), firstFact = !earned.includes("fact");
    box.innerHTML = `<div class="card"><h3 style="margin-top:0">Мои слова</h3>
      ${words.length ? `<ul class="list tight">${words.map((w) => `<li><span class="mark">✍️</span><span class="body"><div class="title">${esc(w.word)}</div>${w.meaning ? `<div>${esc(w.meaning)}</div>` : ""}</span></li>`).join("")}</ul>` : `<p class="muted">Пока пусто — слова, которые зацепили тебя в этой стране.</p>`}
      <div class="row" style="margin-top:8px"><input id="word-text" placeholder="слово — что оно значит" style="flex:1"><button class="btn small" id="word-send">Записать</button></div>
      <p class="note" style="margin:8px 0 0">Видишь только ты; будут в твоём журнале сезона.${firstWord ? " За первое слово — ❄️ +1 заморозка." : ""}</p></div>
      <div class="card"><h3 style="margin-top:0">${isAdmin ? "Факты клуба" : "Мои факты"}</h3>
      ${mine.length ? `<ol style="padding-left:20px;margin:0 0 6px">${mine.map((x) => `<li>${esc(x.text)}</li>`).join("")}</ol>` : `<p class="muted">Пока пусто — что зацепило из постов или нашлось само?</p>`}
      <div class="row" style="margin-top:8px"><input id="fact-text" placeholder="Что нового о стране — в одну-две фразы" style="flex:1"><button class="btn small" id="fact-send">Записать</button></div>
      <p class="note" style="margin:8px 0 0">${isAdmin ? "Твои факты — общие: их видят все на «Карте», и они попадут в журналы всех." : "Видишь только ты; будут в твоём журнале сезона. Общие факты — от Милы — на «Карте»." + (firstFact ? " За первый факт — ❄️ +1 заморозка." : "")}</p></div>`;
    const again = async () => { const y = window.scrollY; await refreshHome(); await renderMineInto(box); window.scrollTo(0, y); };
    $("word-send").addEventListener("click", async () => {
      const text = $("word-text").value.trim();
      if (!text) return RM.toast("Напиши слово и значение");
      $("word-send").disabled = true;
      try {
        const r = await RM.api("/api/words", { method: "POST", body: { text } });
        RM.haptic("success");
        RM.alert(r.message.replace(/<[^>]+>/g, ""));
        if (r.freeze_granted) { const y = window.scrollY; await refreshHome(); await renderBag(); window.scrollTo(0, y); } // the freezes tile above changed
        else await again();
      } catch (e) { $("word-send").disabled = false; RM.toast(e.message); }
    });
    $("fact-send").addEventListener("click", async () => {
      const text = $("fact-text").value.trim();
      if (!text) return RM.toast("Напиши факт");
      $("fact-send").disabled = true;
      try {
        const r = await RM.api("/api/facts", { method: "POST", body: { text } });
        RM.haptic("success");
        if (r.freeze_granted) { RM.alert(r.message.replace(/<[^>]+>/g, "")); const y = window.scrollY; await refreshHome(); await renderBag(); window.scrollTo(0, y); }
        else { RM.toast(r.message.replace(/<[^>]+>/g, "")); await again(); }
      }
      catch (e) { $("fact-send").disabled = false; RM.toast(e.message); }
    });
  }

  // The rules of freezes and the way to tell Mila about one — under the number, on demand.
  function openFreezes(p) {
    const h = state.home;
    openSheet("Заморозки", `<p><b>Пропустила неделю — тратится одна заморозка.</b></p>
      <p style="margin-bottom:6px"><b>Накопить можно до ${p.freezes_total > 2 ? p.freezes_total : 6} за сезон:</b></p>
      <ul class="plain">
        <li>+1 — за своё слово в словарике</li>
        <li>+1 — за свой факт про страну</li>
        <li>+1 — за первый максимум (задание сделано по максимуму)</li>
        <li>+1 — от Милы: за комментарий в канале, встречу или приведённого друга</li>
      </ul>
      ${p.freeze_reasons.length ? `<p class="muted small">Уже заработано: ${p.freeze_reasons.map((r) => esc(RM.freezeReason[r] || r)).join(", ")}.</p>` : ""}
      ${p.freezes_left === 0 ? `<p class="muted small">Заморозки кончились. Статус «Резидент» больше недоступен, но участие продолжается — это главное.</p>` : ""}
      <div class="card" style="margin-top:14px"><h3 style="margin:0 0 6px">Написать Миле про заморозку</h3>
      <p class="note richtext">${html(h.texts.write_prompt)}</p>
      <textarea id="letter-text" placeholder="Это обычное сообщение, не отчёт"></textarea><button class="btn block" id="letter-send" style="margin-top:10px">Написать Миле</button></div>`, bindLetter);
  }

  function bindLetter() {
    $("letter-send").addEventListener("click", async () => {
      const text = $("letter-text").value.trim();
      if (!text) return RM.toast("Пустое письмо не отправлю");
      $("letter-send").disabled = true;
      try { const r = await RM.api("/api/letters", { method: "POST", body: { text } }); RM.haptic("success"); $("letter-text").value = ""; RM.alert(r.message.replace(/<[^>]+>/g, "")); closeSheet(); }
      catch (e) { RM.toast(e.message); }
      $("letter-send").disabled = false;
    });
  }

  function stampHtml(w) {
    const cls = w.state === "stamped" ? w.level : w.state;
    const mark = w.state === "stamped" ? (w.level === "max" ? "⭐" : "✅") : w.state === "current" ? "▸" : RM.stateMark[w.state] || "·";
    return `<button class="stamp ${cls}" data-n="${w.number}"><span class="n">${w.number}</span><span class="m">${mark}</span><span class="t">${esc(w.state === "locked" ? fmt(w.starts_on) : RM.weekName(w.title))}</span></button>`;
  }

  function openWeek(n) {
    const w = state.home.weeks.find((x) => x.number === n);
    if (!w) return;
    const status = w.state === "locked" ? `Откроется ${fmt(w.starts_on)}` : w.state === "current" ? "Идёт сейчас" : w.state === "stamped" ? (w.level === "max" ? "⭐ Максимум" : "✅ Минимум") : w.state === "frozen" ? "❄️ Закрыта заморозкой — цепочка не порвалась" : w.state === "before_join" ? "Была до тебя" : "Пропущена";
    const body = w.state === "locked"
      ? `<p class="muted">${fmt(w.starts_on)} — ${fmt(w.ends_on)}</p><p>Задание появится в понедельник. Недели открываются по одной за раз.</p>`
      : `<p class="muted">${fmt(w.starts_on)} — ${fmt(w.ends_on)} · ${status}</p>
         ${w.intro ? `<p>${esc(w.intro)}</p>` : ""}
         <div class="kv"><div><div class="k">Минимум ✅</div><div class="v">${esc(w.task_min)}</div></div>${w.task_max ? `<div><div class="k">Максимум ⭐</div><div class="v">${esc(w.task_max)}</div></div>` : ""}</div>
         ${w.word ? `<div class="divider"></div><div class="wordline">${esc(w.word)}${w.word_ru ? ` <span class="ru">· ${esc(w.word_ru)}</span>` : ""}</div>${w.word_meaning ? `<div class="muted"><i>${esc(w.word_meaning)}</i></div>` : ""}` : ""}
         ${w.state === "current" ? `<button class="btn block" id="sheet-report" style="margin-top:14px">Сдать отчёт</button>` : ""}
         ${w.late_open ? `<div id="late-box" style="margin-top:14px">${loading()}</div>` : ""}`;
    // The server sends «Неделя N» as the placeholder of a week that has not opened (views.py).
    const named = w.title && w.title !== `Неделя ${w.number}`;
    const title = named ? `Неделя ${w.number} · ${RM.weekName(w.title)}` : `Неделя ${w.number}`;
    openSheet(title, body, () => {
      if ($("sheet-report")) $("sheet-report").addEventListener("click", () => go("week"));
      if (w.late_open) renderLateBox(w);
    });
  }

  // The screen under the sheet follows what the sheet changed: «Это не отчёт» on an on-time
  // report moves the stamp, so the bag is redrawn whole (the sheet lives outside #screen).
  async function syncBag() {
    await refreshHome();
    if (state.tab === "bag") await renderBag();
  }

  // The chapter of a past week inside its sheet: what is already there, and the door to add more.
  async function renderLateBox(w, notice) {
    const box = $("late-box");
    if (!box) return;
    let j;
    try { j = await RM.api("/api/journal"); } catch (e) { if (box.isConnected) box.innerHTML = errorBox(e); return; }
    if (!box.isConnected) return;
    const rs = j.reports.filter((r) => r.week_number === w.number);
    const fresh = (state.home.weeks || []).find((x) => x.number === w.number) || w;
    const late = { again: rs.length > 0, stamped: !!fresh.level };
    box.innerHTML = `${notice ? `<div class="result ok" style="margin-bottom:12px"><div class="rich">${html(notice)}</div></div>` : ""}
      ${rs.length ? `<h3 style="margin:0 0 8px">В журнале · ${rs.length} ${RM.plural(rs.length, "запись", "записи", "записей")}</h3>${rs.map(reportHtml).join("")}` : ""}
      <button class="btn block" id="late-open" style="margin-top:10px">${late.again ? "Дописать в журнал" : "Добавить в журнал"}</button>
      <p class="note" id="late-note" style="margin:8px 0 0"${notice ? " hidden" : ""}>${lateNote(late)}</p>`;
    // The editor opens its own sheet over this one; after it the week sheet is reopened whole.
    // After an edit or a cancel the sheet is reopened from fresh state: the week's stamp may have moved.
    bindReportActions(box, j, async () => { state.sheetReturn = null; await syncBag(); openWeek(w.number); });
    box.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => { state.sheetReturn = () => openWeek(w.number); }));
    $("late-open").addEventListener("click", () => {
      const form = document.createElement("div");
      form.className = "composer";
      form.id = "composer";
      form.innerHTML = composerHtml(w, late);
      $("late-note").remove();
      $("late-open").replaceWith(form);
      bindComposer(w, { ...late, done: async (r) => { await renderLateBox(w, r.message); await syncBag(); } });
      $("report-text").focus();
    });
  }

  // --- Журнал --------------------------------------------------------------------------

  async function renderJournalInto(box) {
    try { state.journal = await RM.api("/api/journal"); } catch (e) { box.innerHTML = errorBox(e); return; }
    if (!box.isConnected) return; // the tab changed while the journal loaded
    const j = state.journal;
    const live = j.reports.filter((r) => r.week_number !== null);
    const letters = j.reports.filter((r) => r.week_number === null);
    const byWeek = new Map();
    live.forEach((r) => { if (!byWeek.has(r.week_number)) byWeek.set(r.week_number, []); byWeek.get(r.week_number).push(r); });
    const weeksDone = [...byWeek.keys()].sort((a, b) => b - a);
    let out = "";
    if (weeksDone.length) out += `<div class="card accent tight"><div class="row between"><div><b>Журнал в PDF</b><div class="muted small">К концу сезона соберётся целиком. Собрать можно и сейчас — одним файлом в бота.</div></div><button class="btn small" id="pdf">Собрать</button></div><p class="muted small" id="pdf-status" style="margin:6px 0 0"></p></div>`;
    out += `<h3 style="margin:18px 0 8px">Журнал${weeksDone.length ? ` · ${weeksDone.length} ${RM.plural(weeksDone.length, "неделя", "недели", "недель")}` : ""}</h3>`;
    if (!weeksDone.length) out += `<div class="empty"><div class="big">📔</div><h2>Пока пусто</h2><p class="muted">Здесь появятся твои недели и твои же слова о них. К концу сезона это будет целый журнал.</p></div>`;
    weeksDone.forEach((n) => {
      const week = j.weeks.find((w) => w.number === n) || { title: "" };
      const rs = byWeek.get(n);
      // No stamp behind the chapter: everything in it was added after the week ended.
      const lateOnly = !week.level && rs.every((r) => r.late);
      // The mark follows the stamp: none → «◦» like the grid above; all late → the journal mark.
      const level = lateOnly ? "📔" : week.level === "max" ? "⭐" : week.level ? "✅" : "◦";
      out += `<div class="card"><h2>${level} Неделя ${n} · ${esc(RM.weekName(week.title))}${lateOnly ? ` <span class="muted small" style="font-weight:400">· дослано позже</span>` : ""}</h2>${rs.map(reportHtml).join("")}</div>`;
    });
    if (letters.length) out += `<details class="card"><summary>Сообщения вне недель (${letters.length})</summary><div class="content">${letters.map(reportHtml).join("")}</div></details>`;
    box.innerHTML = out;
    // The passport tiles and the week grid above the journal change with the stamp: redraw the tab.
    bindReportActions(box, j, async () => { const y = window.scrollY; await refreshHome(); await render(); window.scrollTo(0, y); });
    if ($("pdf")) $("pdf").addEventListener("click", requestPdf);
  }

  function reportHtml(r) {
    const images = r.media.filter((m) => m.mime && m.mime.startsWith("image/") && m.downloaded);
    const others = r.media.filter((m) => !images.includes(m));
    const edited = r.edited_at ? ` · <span class="edited">изменено ${RM.fmtDateTime(r.edited_at)}</span>` : "";
    // A late report has no stamp behind it: the mark replaces the level (DOMAIN §2).
    const level = r.late ? "📔 дослано позже" : r.level === "max" ? "⭐ максимум" : "✅ минимум";
    return `<article class="report">
      <div class="meta">${RM.fmtDateTime(r.created_at)} · ${level} · ${kindName(r.kind)}${edited}</div>
      ${r.text ? `<div class="text">${esc(r.text)}</div>` : ""}
      ${images.length ? `<div class="gallery">${images.map((m, i) => `<a href="${m.url}" target="_blank"><img src="${m.url}" alt="${r.week_number ? `Фото ${i + 1} из отчёта за неделю ${r.week_number}` : `Фото ${i + 1} из сообщения`}" loading="lazy"></a>`).join("")}</div>` : ""}
      ${others.map((m) => m.downloaded ? `<a class="btn ghost small" href="${m.url}" target="_blank" style="margin-top:8px">Открыть файл</a>` : `<span class="muted small">файл ещё скачивается</span>`).join(" ")}
      ${r.week_number !== null ? `<div class="row" style="margin-top:6px">
        ${r.editable ? `<button class="btn link small" data-edit="${r.id}">✏️ Поправить</button>` : ""}
        <button class="btn link small" data-cancel="${r.id}">Это не отчёт</button>
      </div>` : ""}
    </article>`;
  }

  // The editor: text, the files already on the report (tap to take one out), new files.
  // Saves through PATCH; the API recomputes the stamp and tells Mila (DOMAIN §2).
  function openEditor(r, done) {
    const week = (state.home.weeks || []).find((w) => w.number === r.week_number);
    const added = [], removed = new Set();
    const body = `<p class="muted small">${week ? `Неделя ${week.number} · ${esc(RM.weekName(week.title))} · ` : ""}${r.late ? "дослано позже: менять можно до конца сезона — я увижу новую версию." : "пока неделя идёт, отчёт можно менять — я увижу новую версию."}</p>
      <textarea id="edit-text" placeholder="Что было на этой неделе?">${esc(r.text || "")}</textarea>
      ${r.media.length ? `<p class="note" style="margin:10px 0 4px">Файлы в отчёте — нажми, чтобы убрать</p><div class="previews" id="edit-existing">${r.media.map((m) => `<button class="pv keep" data-id="${m.id}" title="${esc(m.mime || "")}">${m.mime && m.mime.startsWith("image/") && m.downloaded ? `<img src="${m.url}" alt="">` : `<span>${kindName(m.mime && m.mime.startsWith("video/") ? "video" : "document")}</span>`}<span class="x" aria-hidden="true">✕</span></button>`).join("")}</div>` : ""}
      <div class="attach" style="margin-top:10px"><label class="btn soft small" for="edit-files">📷 Загрузить фото или видео</label><input id="edit-files" type="file" accept="image/*,video/*" multiple><span class="muted small" id="edit-count"></span></div>
      <div class="previews" id="edit-previews" hidden></div>
      <div class="bar" id="edit-bar" hidden><i></i></div>
      <button class="btn block" id="edit-save" style="margin-top:12px">Сохранить</button>
      <p class="note" style="margin:8px 0 0">${r.late ? "Штамп это не трогает: поздняя запись живёт только в журнале." : "После сохранения штамп за неделю пересчитается по всем твоим отчётам за неё: есть фото — максимум, только текст — минимум."}</p>`;
    openSheet(r.late ? "Поправить запись" : "Поправить отчёт", body, () => {
      const existing = $("edit-existing");
      if (existing) existing.querySelectorAll(".pv").forEach((b) => b.addEventListener("click", () => {
        const id = b.dataset.id;
        if (removed.has(id)) removed.delete(id); else removed.add(id);
        b.classList.toggle("removed", removed.has(id));
        b.classList.toggle("keep", !removed.has(id));
      }));
      const input = $("edit-files");
      input.addEventListener("change", () => { addFiles(added, input.files, r.media.length - removed.size); input.value = ""; renderPreviews(added, $("edit-previews"), $("edit-count")); });
      $("edit-save").addEventListener("click", async () => {
        const text = $("edit-text").value.trim();
        const kept = r.media.length - removed.size;
        if (!text && !kept && !added.length) return RM.toast("Пустым отчёт оставить нельзя — напиши слово или оставь фото");
        if (kept + added.length > MAX_FILES) return RM.toast(`В отчёте может быть не больше ${MAX_FILES} файлов`);
        if (!r._editKey) r._editKey = RM.uid();
        const button = $("edit-save"), bar = $("edit-bar");
        button.disabled = true; button.textContent = "Сохраняю…";
        const form = new FormData();
        form.append("text", text);
        form.append("edit_key", r._editKey);
        removed.forEach((id) => form.append("remove", id));
        added.forEach((f) => form.append("files", f, f.name));
        if (added.length) bar.hidden = false;
        try {
          const res = await RM.upload(`/api/reports/${r.id}`, form, (p) => { bar.querySelector("i").style.width = Math.round(p * 100) + "%"; }, "PATCH");
          RM.haptic("success");
          state.sheetReturn = null; // `done` reopens the week sheet itself, from fresh state
          closeSheet();
          if (res.message) RM.toast(res.message.replace(/<[^>]+>/g, ""), 4000);
          if (done) await done();
        } catch (e) {
          RM.haptic("error");
          button.disabled = false; button.textContent = "Сохранить"; bar.hidden = true;
          RM.toast(e.status === 409 ? e.message : "Не сохранилось: " + e.message, 4500);
        }
      });
    });
  }

  async function requestPdf() {
    const button = $("pdf"), status = $("pdf-status");
    button.disabled = true;
    status.textContent = "Собираю… обычно это меньше минуты.";
    try {
      const job = await RM.api("/api/journal/pdf", { method: "POST", body: {} });
      let tries = 0;
      const poll = async () => {
        let st;
        try { st = await RM.api(`/api/journal/pdf/${job.job_id}`); } catch (e) { status.textContent = e.message; button.disabled = false; return; }
        if (st.status === "done") { status.innerHTML = `Готово — <a href="${st.url}" target="_blank">открыть PDF</a>. Файл ушёл и в чат с ботом.`; button.disabled = false; RM.haptic("success"); }
        else if (st.status === "failed") { status.textContent = "Не получилось: " + (st.error || "ошибка"); button.disabled = false; }
        else if (tries++ < 60) setTimeout(poll, 2000);
        else { status.textContent = "Долго собирается — файл придёт в бота, когда будет готов."; button.disabled = false; }
      };
      setTimeout(poll, 1500);
    } catch (e) { status.textContent = "Не получилось: " + e.message; button.disabled = false; }
  }

  // --- Карта ---------------------------------------------------------------------------

  async function renderSeason() {
    screen.innerHTML = loading();
    const [dict, facts] = await Promise.allSettled([RM.api("/api/dictionary"), RM.api("/api/facts")]);
    if (state.tab !== "season") return; // the tab changed while the season loaded
    if (dict.status === "rejected") return (screen.innerHTML = errorBox(dict.reason));
    const h = state.home, d = dict.value, f = facts.status === "fulfilled" ? facts.value : null;
    const released = h.weeks.filter((w) => w.state !== "locked");
    let out = `<header class="screen-head"><p class="eyebrow">Карта</p><h1 class="season-title">${esc(h.season.title)}</h1><p class="muted">${fmt(h.season.starts_on)} — ${fmt(h.season.ends_on)} · ${h.weeks.length} ${RM.plural(h.weeks.length, "неделя", "недели", "недель")}</p></header>`;
    // «О клубе» opens the tab: it is the answer to «что это вообще» (Mila, 19.09).
    out += `<details class="card"><summary>О клубе</summary><div class="content richtext">${html(h.texts.greeting)}</div></details>`;
    // Weeks as a chronicle, newest first; future weeks are not shown (DOMAIN §1).
    out += `<h3>Недели</h3>`;
    // The running week is «идёт» whatever its stamp: `state` says "stamped" once the person has one.
    const running = (w) => h.week && w.id === h.week.id;
    // The mark is the week's own — a stamp, a freeze, a miss — the same as in the bag's grid.
    const chronicleMark = (w) => running(w) ? "▸" : w.state === "stamped" ? (w.level === "max" ? "⭐" : "✅") : RM.stateMark[w.state] || "◦";
    out += released.length ? `<ul class="list">${released.slice().reverse().map((w) => `<li data-week="${w.number}" style="cursor:pointer"><span class="mark">${chronicleMark(w)}</span><span class="body"><div class="title">${w.number}. ${esc(RM.weekName(w.title))}</div><div class="sub">${fmt(w.starts_on)} — ${fmt(w.ends_on)}${running(w) ? " · идёт" : ""}${w.word ? ` · ${esc(w.word)}` : ""}</div></span></li>`).join("")}</ul>` : `<p class="muted">Первая неделя ещё не началась.</p>`;
    out += `<h3>Слова недели</h3>`;
    out += d.week_words.length ? `<ul class="list">${d.week_words.map((w) => `<li><span class="mark">📖</span><span class="body"><div class="title">${esc(w.word)}${w.word_ru ? ` <span class="muted" style="font-weight:400">· ${esc(w.word_ru)}</span>` : ""}</div>${w.meaning ? `<div>${esc(w.meaning)}</div>` : ""}<div class="sub">неделя ${w.week_number} · ${esc(RM.weekName(w.title))}</div></span></li>`).join("")}</ul>` : `<p class="muted">Слова недели появятся вместе с заданиями.</p>`;
    // Facts here are Mila's — the club's shared ones. A person's own facts and words live in the bag (DOMAIN §6).
    const shared = f ? f.facts.filter((x) => !x.mine && !x.author) : [];
    if (f) out += `<div class="card" style="margin-top:18px"><h2>💡 Что мы узнали про ${esc(f.about)}</h2>
      ${shared.length ? `<ol style="padding-left:20px;margin:0">${shared.map((x) => `<li>${esc(x.text)}</li>`).join("")}</ol>` : `<p class="muted">Пока пусто — Мила добавит после постов.</p>`}
      <p class="note" style="margin:8px 0 0">Свои факты и слова — в «Рюкзаке»: их видишь только ты, и они будут в твоём журнале сезона.</p></div>`;
    else out += `<div class="card" style="margin-top:18px"><h2>💡 Что мы узнали</h2><p class="muted">Факты не загрузились — открой вкладку ещё раз.</p></div>`;
    out += `<details class="card"><summary>❔ Если что-то пошло не так</summary><div class="content helptext">${html(unhead(h.texts.help))}</div></details>`;
    const links = [];
    if (h.links.channel_url) links.push(`<a class="btn soft" href="${esc(h.links.channel_url)}">📣 Канал клуба</a>`);
    if (h.links.admin_app) links.push(`<a class="btn" href="/app/admin${location.hash}">🛠 Админка</a>`);
    if (links.length) out += `<div class="actions" style="margin-top:4px">${links.join("")}</div>`;
    out += `<p class="muted small" style="margin-top:20px;text-align:center">Письмо Миле — в «Рюкзаке» у заморозок или в чате с ботом${h.links.bot_username ? ` @${esc(h.links.bot_username)}` : ""}: /help → «Написать Миле».</p>`;
    screen.innerHTML = out;
    screen.querySelectorAll("li[data-week]").forEach((li) => li.addEventListener("click", () => openWeek(+li.dataset.week)));
  }

  // --- helpers -------------------------------------------------------------------------

  function loading() { return RM.spinner(); }
  function errorBox(e) { return `<div class="empty"><div class="big">🙈</div><h2>Не получилось</h2><p class="muted">${esc(e.message)}</p><button class="btn soft" onclick="location.reload()">Обновить</button></div>`; }

  function openSheet(title, body, bind) {
    $("sheet-title").textContent = title;
    $("sheet-body").innerHTML = body;
    $("sheet").hidden = false;
    document.body.style.overflow = "hidden";
    RM.onBack(closeSheet);
    if (bind) bind();
  }
  function closeSheet() {
    if ($("sheet").hidden) return;
    $("sheet").hidden = true;
    document.body.style.overflow = "";
    RM.onBack(null);
    const back = state.sheetReturn;
    state.sheetReturn = null;
    if (back) back(); // the editor was opened over the week sheet: come back to it
  }
})();
