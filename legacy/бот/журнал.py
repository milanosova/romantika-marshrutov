#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Журнал сезона в PDF.

Собирает страницу из базы бота и печатает её через Chrome.
Зависимостей нет, Chrome уже стоит в системе.

    python3 журнал.py 355363829        один человек
    python3 журнал.py --все            всем, у кого есть хоть один штамп
    python3 журнал.py --образец        показать на выдуманных данных

Готовое кладётся в папку «журналы» рядом.
"""

import json
import os
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import date, datetime
from html import escape

ЗДЕСЬ = os.path.dirname(os.path.abspath(__file__))
БАЗА = os.path.join(ЗДЕСЬ, "данные.sqlite")
КУДА = os.path.join(ЗДЕСЬ, "журналы")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

with open(os.path.join(ЗДЕСЬ, "сезон.json"), encoding="utf-8") as ф:
    СЕЗОН = json.load(ф)


def токен():
    п = os.path.join(ЗДЕСЬ, "токен.txt")
    return open(п, encoding="utf-8").read().strip() if os.path.exists(п) else ""


# ─────────────────────────── данные ───────────────────────────

def бд():
    conn = sqlite3.connect(БАЗА)
    conn.row_factory = sqlite3.Row
    return conn


def в_дату(с):
    return datetime.strptime(с, "%Y-%m-%d").date()


def собрать(человек):
    with бд() as conn:
        кто = conn.execute("SELECT * FROM люди WHERE id=?", (человек,)).fetchone()
        штампы = {р["неделя"]: р for р in conn.execute(
            "SELECT неделя, уровень, название FROM штампы WHERE человек=?",
            (человек,)).fetchall()}
        отчёты = conn.execute(
            "SELECT неделя, текст, файл FROM отчёты WHERE человек=? ORDER BY id",
            (человек,)).fetchall()
        ачивки = [р["подпись"] for р in conn.execute(
            "SELECT подпись FROM ачивки WHERE человек=? ORDER BY когда",
            (человек,)).fetchall()]
        свои_слова = [р["слово"] for р in conn.execute(
            "SELECT слово FROM свои_слова WHERE человек=? ORDER BY id",
            (человек,)).fetchall()]
        факты = conn.execute(
            "SELECT текст, автор FROM факты ORDER BY id").fetchall()
        пожелание = conn.execute(
            "SELECT текст FROM пожелания WHERE человек=?", (человек,)).fetchone()
        заморозки = conn.execute(
            "SELECT причина FROM заморозки WHERE человек=?", (человек,)).fetchall()

    цитаты, фото = {}, []
    for о in отчёты:
        if о["текст"]:
            цитаты.setdefault(о["неделя"], о["текст"])
        if о["файл"]:
            фото.append((о["неделя"], о["файл"]))

    return {
        "имя": (кто["имя"] if кто else "Участник"),
        "штампы": штампы, "цитаты": цитаты, "фото": фото,
        "ачивки": ачивки, "свои_слова": свои_слова, "факты": факты,
        "пожелание": пожелание["текст"] if пожелание else "",
        "заморозок": len(заморозки) + 2,
    }


def скачать_фото(файл_ид, куда):
    """Тянем картинку из Телеграма по file_id. Возвращает путь или None."""
    т = токен()
    if not т:
        return None
    try:
        with urllib.request.urlopen(
                "https://api.telegram.org/bot" + т + "/getFile?file_id=" + файл_ид,
                timeout=30) as о:
            путь = json.load(о)["result"]["file_path"]
        имя = os.path.join(куда, файл_ид[:24].replace("/", "_") + ".jpg")
        urllib.request.urlretrieve(
            "https://api.telegram.org/file/bot" + т + "/" + путь, имя)
        return имя
    except Exception as e:
        print("  фото не скачалось:", e)
        return None


# ─────────────────────────── вёрстка ───────────────────────────

СТИЛЬ = """
@page { size: A4; margin: 16mm 14mm; }
* { box-sizing: border-box; }
body {
  font: 11pt/1.55 Georgia, "PT Serif", serif;
  color: #2b2320; background: #fdfaf5; margin: 0;
}
.лист { max-width: 180mm; margin: 0 auto; }
.шапка { border-bottom: 2px solid #b4472f; padding-bottom: 14px; margin-bottom: 26px; }
.клуб { font: 600 8.5pt/1 -apple-system, sans-serif; letter-spacing: .22em;
        text-transform: uppercase; color: #b4472f; }
h1 { font-size: 30pt; margin: 10px 0 4px; font-weight: 400; letter-spacing: -.01em; }
.подзаг { font: 10pt/1.4 -apple-system, sans-serif; color: #7a6a60; }
h2 { font-size: 13pt; font-weight: 400; margin: 30px 0 12px;
     padding-bottom: 5px; border-bottom: 1px solid #e3d9cd;
     text-transform: uppercase; letter-spacing: .13em;
     font-family: -apple-system, sans-serif; color: #8a5a3b; }
.итог { display: flex; gap: 26px; margin: 22px 0 4px; }
.число { font-size: 26pt; line-height: 1; color: #b4472f; }
.метка { font: 8.5pt/1.3 -apple-system, sans-serif; color: #7a6a60;
         text-transform: uppercase; letter-spacing: .1em; }
.сетка { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.клетка { border: 1px solid #e3d9cd; border-radius: 3px; padding: 9px 10px;
          background: #fffdf9; min-height: 62px; }
.клетка.есть { border-color: #b4472f; background: #fdf3ee; }
.клетка .н { font: 8pt/1 -apple-system, sans-serif; color: #a6968a; }
.клетка .т { font-size: 9.5pt; margin-top: 5px; }
.клетка .з { font-size: 15pt; float: right; line-height: 1; }
.неделя { margin: 0 0 18px; padding-left: 15px; border-left: 3px solid #e8c9b8; }
.неделя h3 { font-size: 12pt; font-weight: 400; margin: 0 0 5px; }
.неделя .звезда { color: #b4472f; }
blockquote { margin: 0; font-style: italic; color: #4a3d36; }
.чипы { display: flex; flex-wrap: wrap; gap: 7px; }
.чип { border: 1px solid #d8c4b4; border-radius: 20px; padding: 4px 13px;
       font-size: 10pt; background: #fffdf9; }
ol { padding-left: 20px; margin: 0; }
ol li { margin-bottom: 7px; }
.кто { font-style: italic; color: #8a7a70; font-size: 9.5pt; }
.слова { column-count: 2; column-gap: 24px; }
.слова div { break-inside: avoid; margin-bottom: 7px; }
.слово { color: #b4472f; }
.фото { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.фото img { width: 100%; height: 150px; object-fit: cover; border-radius: 3px; }
.записка { background: #fdf3ee; border-left: 3px solid #b4472f;
           padding: 16px 20px; font-style: italic; }
.подпись { text-align: right; font-style: normal; color: #8a7a70;
           font-size: 9.5pt; margin-top: 10px; }
footer { margin-top: 34px; padding-top: 12px; border-top: 1px solid #e3d9cd;
         font: 9pt/1.5 -apple-system, sans-serif; color: #a6968a;
         display: flex; justify-content: space-between; }
"""


def страница(д, папка_фото, образец_ли=False):
    н = []
    a = н.append
    a("<!doctype html><html lang='ru'><head><meta charset='utf-8'>")
    a("<title>Журнал сезона</title><style>" + СТИЛЬ + "</style></head><body>")
    a("<div class='лист'>")

    # шапка
    a("<div class='шапка'><div class='клуб'>Романтика маршрутов · сезон 1</div>")
    a("<h1>" + escape(д["имя"]) + " и " + escape(СЕЗОН["season"]) + "</h1>")
    a("<div class='подзаг'>" + в_дату(СЕЗОН["start"]).strftime("%d.%m.%Y")
      + " — " + в_дату(СЕЗОН["end"]).strftime("%d.%m.%Y")
      + ("<br><b style='color:#b4472f'>Образец.</b> Так журнал выглядит "
         "у того, кто прошёл половину сезона. Даже одна-две недели — "
         "это уже журнал." if образец_ли else "")
      + "</div></div>")

    всего = len(д["штампы"])
    звёзд = sum(1 for ш in д["штампы"].values() if ш["уровень"] == "максимум")
    статус = ("Резидент" if всего >= 9 else "Путешественник" if всего >= 4
              else "Турист" if всего >= 1 else "Наблюдатель")
    a("<div class='итог'>")
    for число, метка in [(всего, "недель пройдено"), (звёзд, "по максимуму"),
                         (len(д["ачивки"]), "ачивок"),
                         (len(д["свои_слова"]), "своих слов")]:
        a("<div><div class='число'>" + str(число) + "</div>"
          "<div class='метка'>" + метка + "</div></div>")
    a("<div><div class='число' style='font-size:17pt'>" + статус + "</div>"
      "<div class='метка'>статус</div></div></div>")

    # паспорт
    a("<h2>Паспорт сезона</h2><div class='сетка'>")
    for нед in СЕЗОН["weeks"]:
        ш = д["штампы"].get(нед["num"])
        знак = "⭐" if ш and ш["уровень"] == "максимум" else "✅" if ш else "·"
        a("<div class='клетка" + (" есть" if ш else "") + "'>"
          "<span class='з'>" + знак + "</span>"
          "<div class='н'>" + str(нед["num"]) + " неделя</div>"
          "<div class='т'>" + escape((ш["название"] if ш else nil_title(нед))
                                     or нед["title"]) + "</div></div>")
    a("</div>")

    # недели с цитатами
    если_есть = [n for n in СЕЗОН["weeks"] if n["num"] in д["штампы"]]
    if если_есть:
        a("<h2>Твои недели</h2>")
        for нед in если_есть:
            ш = д["штампы"][нед["num"]]
            звезда = " <span class='звезда'>★</span>" if ш["уровень"] == "максимум" else ""
            a("<div class='неделя'><h3>" + str(нед["num"]) + " · "
              + escape(ш["название"] or нед["title"]) + звезда + "</h3>")
            цит = д["цитаты"].get(нед["num"])
            if цит:
                a("<blockquote>«" + escape(цит) + "»</blockquote>")
            a("</div>")

    # фотографии
    if папка_фото:
        a("<h2>Твои фотографии</h2><div class='фото'>")
        for п in папка_фото:
            a("<img src='" + escape(os.path.basename(п)) + "'>")
        a("</div>")

    if д["ачивки"]:
        a("<h2>Ачивки</h2><div class='чипы'>")
        for а in д["ачивки"]:
            a("<span class='чип'>" + escape(а) + "</span>")
        a("</div>")

    # словарь
    слова = [n for n in СЕЗОН["weeks"] if n["word"]]
    if слова or д["свои_слова"]:
        a("<h2>Словарик сезона</h2><div class='слова'>")
        for n in слова:
            a("<div><span class='слово'>" + escape(n["word"]) + "</span> — "
              + escape(n["word_meaning"]) + "</div>")
        for с in д["свои_слова"]:
            a("<div><span class='слово'>твоё</span> — " + escape(с) + "</div>")
        a("</div>")

    if д["факты"]:
        a("<h2>Что мы узнали про "
          + escape(СЕЗОН.get("season_about") or СЕЗОН["season"]) + "</h2><ol>")
        for ф in д["факты"]:
            a("<li>" + escape(ф["текст"]) + "</li>")
        a("</ol>")

    if д["пожелание"]:
        a("<h2>От Милы</h2><div class='записка'>" + escape(д["пожелание"])
          + "<div class='подпись'>— Мила</div></div>")

    a("<footer><span>Романтика маршрутов</span>"
      "<span>Следующая страна — с 23 ноября</span></footer>")
    a("</div></body></html>")
    return "\n".join(н)


def nil_title(нед):
    return нед["title"]


# ─────────────────────────── печать ───────────────────────────

def сделать(человек, качать_фото=True, образец_ли=False):
    д = собрать(человек)
    имя_файла = "образец журнала" if образец_ли else "журнал_" + str(человек)
    папка = os.path.join(КУДА, имя_файла)
    os.makedirs(папка, exist_ok=True)

    картинки = []
    if качать_фото:
        for _, ид in д["фото"][:9]:
            п = скачать_фото(ид, папка)
            if п:
                картинки.append(п)

    html = os.path.join(папка, "журнал.html")
    with open(html, "w", encoding="utf-8") as ф:
        ф.write(страница(д, картинки, образец_ли))

    pdf = os.path.join(КУДА, имя_файла + ".pdf")
    if os.path.exists(CHROME):
        subprocess.run([CHROME, "--headless", "--disable-gpu",
                        "--no-pdf-header-footer", "--print-to-pdf=" + pdf,
                        "file://" + html],
                       capture_output=True, timeout=120)
    print("  html:", html)
    print("  pdf: ", pdf if os.path.exists(pdf) else "не собрался")
    return html, pdf


def образец():
    """Прогон на выдуманных данных — посмотреть, как выглядит."""
    global собрать
    настоящая = собрать

    def подделка(_):
        return {
            "имя": "Мария",
            "штампы": {
                1: {"неделя": 1, "уровень": "максимум", "название": "За столом"},
                2: {"неделя": 2, "уровень": "максимум", "название": "Красками"},
                3: {"неделя": 3, "уровень": "минимум", "название": "Ночь Эль-Грито"},
                5: {"неделя": 5, "уровень": "минимум", "название": "На слух"},
                6: {"неделя": 6, "уровень": "максимум", "название": "Своим ходом"},
            },
            "цитаты": {
                1: "Чимичанга! Жареное буррито, хрустит. Дошла до Автомойки, "
                   "взяла — оказалось острее, чем я думала, и я запивала её орчатой",
                2: "Мой алебрихе: голова кота, крылья от бабочки, хвост как у лисы. "
                   "Рисовала два вечера и один раз чуть не бросила",
                3: "Нашла, что «Эль Грито» кричат ровно в 23:00, а не утром",
                5: "Кинула Chavela Vargas — La Llorona. Слушала три раза подряд",
                6: "Дошла до лавки со специями на Тульской, купила масу. "
                   "Продавец объяснял на пальцах, я почти всё поняла",
            },
            "фото": [],
            "ачивки": ["🌮 Повар", "🎨 Художник", "🥇 Первый в комментариях",
                       "📍 Следопыт"],
            "свои_слова": ["sobremesa — время за столом уже после еды, "
                           "когда все сидят и разговаривают"],
            "факты": [{"текст": т, "автор": None} for т in [
                "Ацтеки называли себя мешика — отсюда «Мексика»",
                "Испанский принесли учительницы, а не конкистадоры",
                "Почти всюду кукуруза — даже в горячем шоколаде",
                "Цолькин и наш календарь не связаны вообще: их сшили "
                "корреляционной константой, а проверили по дневальным "
                "в горах Гватемалы, которые не переставали считать пятьсот лет",
                "pib на майя — это яма. Кочинита пибиль буквально "
                "«поросёнок из ямы»",
            ]],
            "пожелание": "Маша, ты пришла на третьей неделе и решила, "
                         "что опоздала. А в итоге прошла больше половины "
                         "и притащила слово, которого не было ни у кого. "
                         "Спасибо, что не стала ждать следующего сезона.",
            "заморозок": 4,
        }

    собрать = подделка
    try:
        return сделать(0, качать_фото=False, образец_ли=True)
    finally:
        собрать = настоящая


if __name__ == "__main__":
    os.makedirs(КУДА, exist_ok=True)
    арг = sys.argv[1] if len(sys.argv) > 1 else "--образец"

    if арг == "--образец":
        print("Образец:")
        образец()
    elif арг == "--все":
        with бд() as conn:
            люди = [р[0] for р in conn.execute(
                "SELECT DISTINCT человек FROM штампы")]
        print("Журналов к сборке:", len(люди))
        for ч in люди:
            print(ч)
            сделать(ч)
    else:
        сделать(int(арг))
