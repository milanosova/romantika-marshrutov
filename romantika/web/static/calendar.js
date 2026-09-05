// Tzolkin calendar Mini App: the same formulas as romantika/domain/tzolkin.py.
(function () {
  const data = JSON.parse(document.getElementById("tzolkin-data").textContent);
  const SIGNS = data.signs;
  const TONES = data.tones;
  const CORRELATION = 584283;
  const esc = window.RM ? window.RM.escape : (s) => String(s);

  function jdn(d, m, y) {
    const a = Math.floor((14 - m) / 12);
    const yy = y + 4800 - a;
    const mm = m + 12 * a - 3;
    return d + Math.floor((153 * mm + 2) / 5) + 365 * yy + Math.floor(yy / 4) - Math.floor(yy / 100) + Math.floor(yy / 400) - 32045;
  }

  function tzolkin(d, m, y) {
    const days = jdn(d, m, y) - CORRELATION;
    const num = ((days % 13 + 13 + 3) % 13) + 1;
    const idx = ((days % 20 + 20 + 19) % 20);
    let pos = 0;
    while (pos < 260 && !(pos % 13 === num - 1 && pos % 20 === idx)) pos++;
    return { num, idx, kin: pos + 1 };
  }

  const dSel = document.getElementById("d"), mSel = document.getElementById("m"), ySel = document.getElementById("y");
  const months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  for (let i = 1; i <= 31; i++) dSel.insertAdjacentHTML("beforeend", `<option value="${i}">${i}</option>`);
  months.forEach((name, i) => mSel.insertAdjacentHTML("beforeend", `<option value="${i + 1}">${name}</option>`));
  const thisYear = new Date().getFullYear();
  for (let y = thisYear; y >= 1930; y--) ySel.insertAdjacentHTML("beforeend", `<option value="${y}">${y}</option>`);
  ySel.value = "1990";


  document.getElementById("birth").addEventListener("submit", (event) => {
    event.preventDefault();
    const d = +dSel.value, m = +mSel.value, y = +ySel.value;
    const probe = new Date(y, m - 1, d);
    if (probe.getMonth() !== m - 1) { alert("Такого дня в этом месяце нет"); return; }
    const r = tzolkin(d, m, y);
    const sign = SIGNS[r.idx];
    const tone = TONES[r.num - 1];
    const box = document.getElementById("result");
    box.innerHTML = `
      <p class="eyebrow">Твой день</p>
      <h2>${r.num} ${esc(sign.name)} ${esc(sign.emoji)}</h2>
      <p class="kin">кин ${r.kin} из 260${sign.name_academic && sign.name_academic !== sign.name ? ` · ${esc(sign.name_academic)}` : ""} · ${esc(sign.latin)} · ${esc(sign.symbol)}</p>
      <p>${esc(sign.meaning)}</p>
      <p><b>Предназначение.</b> ${esc(sign.destiny)}</p>
      <p><b>Число ${r.num}${tone && tone.name ? " · " + esc(tone.name) : ""}.</b> ${esc(tone ? tone.text : "")}</p>
      <p class="note">Коротко для комментария: «${esc(sign.short || sign.name)}»</p>`;
    box.hidden = false;
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  });
})();
