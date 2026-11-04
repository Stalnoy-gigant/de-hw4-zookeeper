"""Собирает report/report.pdf из журналов logs/, выводов outputs/ и скриншотов (все числа берутся из файлов)."""
import os, re
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
from PIL import Image as PI
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
F = "/System/Library/Fonts/Supplemental/"
pdfmetrics.registerFont(TTFont("A", F + "Arial.ttf")); pdfmetrics.registerFont(TTFont("AB", F + "Arial Bold.ttf"))
pdfmetrics.registerFont(TTFont("M", "/System/Library/Fonts/Menlo.ttc", subfontIndex=0))
pdfmetrics.registerFontFamily("A", normal="A", bold="AB", italic="A", boldItalic="AB")
fm.fontManager.addfont(F + "Arial.ttf")
plt.rcParams["font.family"] = "Arial"

H1 = ParagraphStyle("h1", fontName="AB", fontSize=15, spaceBefore=10, spaceAfter=6)
H2 = ParagraphStyle("h2", fontName="AB", fontSize=11.5, spaceBefore=8, spaceAfter=4, keepWithNext=1)
P = ParagraphStyle("p", fontName="A", fontSize=9.5, leading=13, spaceAfter=4)
C = ParagraphStyle("c", fontName="M", fontSize=6.6, leading=8.2, backColor=colors.HexColor("#f2f2f2"), borderPadding=3, spaceAfter=6)
CAP = ParagraphStyle("cap", fontName="A", fontSize=8, textColor=colors.grey, spaceAfter=6)
CELL = ParagraphStyle("cell", fontName="A", fontSize=7.5, leading=9)
COL_A, COL_B, MUTED = "#2a6fdb", "#d9480f", "#6b6b6b"


def code(text, maxlines=60):
    L = text.rstrip().splitlines()
    if len(L) > maxlines:
        L = L[:maxlines] + [f"... ({len(L)-maxlines} строк опущено)"]
    return Preformatted("\n".join(l[:150].replace("\t", "  ") for l in L), C)


def table(rows, widths):
    data = [[Paragraph(str(c), CELL) for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), .3, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6eaf2")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    return t


def screen(fn, cap, width=170):
    w, h = PI.open("report/screens/" + fn).size
    hh = width * h / w
    if hh > 200:                       # высокие скрины ужимаем по высоте
        width, hh = width * 200 / hh, 200
    return KeepTogether([Image("report/screens/" + fn, width * mm, hh * mm), Paragraph(cap, CAP)])


def strip_t(line):
    return re.sub(r"\s+\(t=[\d.]+\)$", "", line)


def events(path):
    """[(t, текст)] из журнала координатора или сценария."""
    out = []
    for line in open(path, encoding="utf-8"):
        m = re.search(r"\] (.*)\s+\(t=([\d.]+)\)$", line.rstrip())
        if m:
            out.append((float(m.group(2)), m.group(1).strip()))
    return out


def first(ev, pat):
    return next((t for t, s in ev if re.search(pat, s)), None)


def run_info(run):
    sc, A, B = (events(f"logs/{run}_{x}.log") for x in ("scenario", "A", "B"))
    t0 = sc[0][0]
    i = dict(t0=t0, sc=sc, A=A, B=B,
             stop=first(sc, "kill -STOP"), cont=first(sc, "kill -CONT"),
             b_lead=first(B, "СТАЛ ЛИДЕРОМ"), b_pub=first(B, "записал результат"),
             a_pub=first(A, "записал результат|ЗАПИСЬ ОТКЛОНЕНА"),
             a_pub_text=next(s for t, s in A if re.search("записал результат|ЗАПИСЬ ОТКЛОНЕНА", s)),
             b_pub_text=next(s for t, s in B if "записал результат" in s),
             a_chunks=[t for t, s in A if "обработан" in s], b_chunks=[t for t, s in B if "обработан" in s],
             b_skipped=sum("пропуск" in s for t, s in B), a_skipped=sum("пропуск" in s for t, s in A))
    i["switch"] = i["b_lead"] - i["stop"]
    i["pause"] = i["cont"] - i["stop"]
    ctl = {}
    for who, ev in (("A", A), ("B", B)):
        red = next(s for t, s in ev if "reduce завершён" in s)
        ks = [re.search(r"k=(\d): distinct=([\d,]+).*(СОВПАЛО|НЕ СОВПАЛО)", s).groups() for t, s in ev if re.match(r"k=\d: distinct", s)]
        ctl[who] = dict(lines=re.search(r"lines=([\d,]+)", red).group(1), variant=re.search(r"variant_vacancies=([\d,]+)", red).group(1),
                        k={k: d for k, d, _ in ks}, match=all(m == "СОВПАЛО" for _, _, m in ks))
    i["ctl"] = ctl
    return i


def timeline(i, fn, title, fenced):
    x = lambda t: t - i["t0"]
    fig, ax = plt.subplots(figsize=(8, 2.6))
    ax.axvspan(x(i["stop"]), x(i["cont"]), ymin=.58, ymax=.86, color="#d0d0d0", lw=0)
    ax.text((x(i["stop"]) + x(i["cont"])) / 2, 1.42, f"A приостановлен (SIGSTOP), {i['pause']:.0f} с", ha="center", fontsize=7.5, color="#333")
    ax.scatter([x(t) for t in i["a_chunks"]], [1] * len(i["a_chunks"]), s=14, color=COL_A, zorder=3, label="координатор A: чанк обработан")
    ax.scatter([x(t) for t in i["b_chunks"]], [0] * len(i["b_chunks"]), s=14, color=COL_B, zorder=3, label="координатор B: чанк обработан")
    ax.plot([x(i["t0"]), x(i["b_lead"])], [0, 0], color=COL_B, lw=1, ls=":", zorder=2)
    ax.plot([x(i["t0"]), x(i["stop"])], [1, 1], color=COL_A, lw=1, zorder=2)
    ax.plot([x(i["b_lead"]), x(i["b_pub"])], [0, 0], color=COL_B, lw=1, zorder=2)
    ax.annotate("", xy=(x(i["b_lead"]), -.38), xytext=(x(i["stop"]), -.38), arrowprops=dict(arrowstyle="<->", color="#333", lw=.8))
    ax.text((x(i["stop"]) + x(i["b_lead"])) / 2, -.62, f"смена лидера {i['switch']:.2f} с", ha="center", fontsize=7.5, color="#333")
    ax.scatter([x(i["b_pub"])], [0], marker="*", s=110, color=COL_B, edgecolor="white", lw=.8, zorder=4)
    ax.text(x(i["b_pub"]), .22, "B: записал (v1)", ha="center", fontsize=7, color="#333")
    if fenced:
        ax.scatter([x(i["a_pub"])], [1], marker="X", s=70, color=COL_A, edgecolor="white", lw=.8, zorder=4)
        ax.text(x(i["a_pub"]) + .6, .72, "A: BadVersionError", ha="left", fontsize=7, color="#333")
    else:
        ax.scatter([x(i["a_pub"])], [1], marker="*", s=110, color=COL_A, edgecolor="white", lw=.8, zorder=4)
        ax.text(x(i["a_pub"]) + .6, .72, "A: записал (v2)", ha="left", fontsize=7, color="#333")
    ax.set_yticks([0, 1]); ax.set_yticklabels(["B (запасной)", "A (лидер)"], fontsize=8)
    ax.set_ylim(-.8, 1.6); ax.set_xlim(-1, x(i["a_pub"]) + 9)
    ax.set_xlabel("секунды от запуска сценария", fontsize=8, color=MUTED); ax.tick_params(axis="x", labelsize=7.5, colors=MUTED)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#bbb"); ax.grid(axis="x", color="#eee", lw=.6); ax.set_axisbelow(True)
    ax.set_title(title, fontsize=9, loc="left")
    ax.legend(loc="upper left", bbox_to_anchor=(0, -.28), ncol=2, fontsize=7, frameon=False)
    plt.tight_layout(); plt.savefig(f"report/img/{fn}.png", dpi=170); plt.close()


os.makedirs("report/img", exist_ok=True)
NF, FE = run_info("nofence"), run_info("fence")
timeline(NF, "timeline_nofence", "Этап 3 (FENCING=0): A записал результат повторно", False)
timeline(FE, "timeline_fence", "Этап 4 (FENCING=1): запись A отклонена по эпохе", True)

S = []
add = S.append
add(Paragraph("ДЗ №4. Обеспечение отказоустойчивости средствами Apache ZooKeeper", H1))
add(Paragraph("Выполнил: Афанасьев Николай Игоревич, группа J3314 (ФТИИ, ИТМО). <b>Вариант 1: «Программист, разработчик на 1С».</b> "
              "Окружение: MacBook (Apple M4, 10 ядер, 16 ГБ), Docker Desktop, ZooKeeper 3.9.6 (официальный образ, Java 17.0.21), "
              "RustFS из ДЗ №3 (бакет hh-lake), Python 3.14, kazoo 2.11. "
              "Репозиторий: <font name='M'>github.com/Stalnoy-gigant/de-hw4-zookeeper</font>.", P))

add(Paragraph("1. Цель и общая схема", H2))
add(Paragraph("Поднять ансамбль из трёх серверов ZooKeeper, проверить кворум и эфемерные узлы, а затем встроить ZooKeeper в координатор "
              "расчёта сочетаний навыков из ДЗ №3: две копии координатора выбирают лидера, лидер сохраняет чекпоинты, при его зависании "
              "запасной продолжает с чекпоинта, а устаревший лидер после «пробуждения» не должен перезаписать результат. "
              "Последнее обеспечивается проверкой эпохи при публикации (FENCING=1).", P))

add(Paragraph("2. Этап 1. Установка", H2))
add(Paragraph("Java на машине не установлена, поэтому выбран разрешённый заданием вариант с Docker: три контейнера из образа "
              "<font name='M'>zookeeper:3.9.6</font> (внутри Eclipse Temurin Java 17.0.21). Каждый контейнер играет роль отдельной "
              "распакованной копии: у него свой каталог <font name='M'>zk/zkN/</font> с <font name='M'>conf/zoo.cfg</font> и "
              "<font name='M'>data/myid</font>, подключённый в контейнер. Сервер запускается штатной командой "
              "<font name='M'>zkServer.sh start-foreground</font> (поле command в compose.yaml). Версии видны в выводе клиента: "
              "<font name='M'>zookeeper.version=3.9.6</font>, <font name='M'>java.version=17.0.21</font> (рис. 3).", P))

add(Paragraph("3. Этап 2. Ансамбль из трёх серверов", H2))
add(Paragraph("Строки участников <font name='M'>server.N</font> одинаковые во всех трёх конфигурациях, а клиентский порт, порты кворума/выборов "
              "и dataDir у каждого сервера свои — как если бы три копии работали на одной машине. Файл myid в dataDir сообщает серверу, "
              "какая строка server.N относится к нему.", P))
add(table([["Сервер", "myid", "clientPort", "Порт кворума : выборов", "dataDir"],
           ["zk1", "1", "2181", "2888 : 3888", "/var/zookeeper/data1 (← zk/zk1/data)"],
           ["zk2", "2", "2182", "2889 : 3889", "/var/zookeeper/data2 (← zk/zk2/data)"],
           ["zk3", "3", "2183", "2890 : 3890", "/var/zookeeper/data3 (← zk/zk3/data)"]],
          [18 * mm, 12 * mm, 20 * mm, 38 * mm, 92 * mm]))
add(Spacer(1, 4))
add(Paragraph("Конфигурация zk/zk1/conf/zoo.cfg (у zk2/zk3 отличаются только dataDir и clientPort):", CAP))
add(code(open("zk/zk1/conf/zoo.cfg", encoding="utf-8").read()))
add(Paragraph("Проверки выполнены дважды: вручную (скриншоты ниже) и скриптом stage2.sh (полный текстовый вывод — outputs/stage2.txt). "
              "Лидер выбирается при каждом запуске заново, поэтому в ручном прогоне лидером был zk3, а в скриптовом — zk2.", P))
add(screen("01_roles.png", "Рис. 1. Роли серверов: zk3 — leader, zk1 и zk2 — follower.", 120))
add(screen("02_one_down_status.png", "Рис. 2. Остановлен zk1 (docker stop zk1): zk2 — follower, zk3 — leader, ансамбль работает.", 170))
add(screen("03_one_down_create.png", "Рис. 3. Запись при двух живых серверах из трёх: Created /one-down (версии ZooKeeper и Java видны в начале вывода).", 170))
add(screen("04_two_down_and_restart.png", "Рис. 4. Остановлен второй сервер: zk3 — «Error contacting service», "
           "«This ZooKeeper instance is not currently serving requests», клиент получает ConnectionLoss. После docker start zk1 zk2 "
           "роли восстановились, узел /one-down со значением works сохранился.", 160))
add(Paragraph("Процесс на zk3 при этом жив (в скриптовом прогоне <font name='M'>ruok</font> отвечает <font name='M'>imok</font>), "
              "но без кворума (2 из 3) сервер отказывается обслуживать запросы — он не может знать, не выбрало ли большинство "
              "уже другого лидера и не ушли ли данные вперёд.", P))
add(Paragraph("Эфемерный узел", H2))
add(Paragraph("В окне 2 открыт интерактивный zkCli и создан узел с флагом -e; в окне 1 другой клиент видит его, а после quit в окне 2 "
              "(сессия закрыта) узел исчезает. Обычный узел /one-down при этом остаётся.", P))
add(screen("05_ephemeral_window2.png", "Рис. 5. Окно 2: create -e /ephemeral-demo hello; ephemeralOwner = 0x2000d76a5700000 — id сессии этого клиента.", 170))
add(screen("06_ephemeral_window1_before.png", "Рис. 6. Окно 1, пока окно 2 открыто: узел ephemeral-demo есть.", 140))
add(screen("07_ephemeral_after_quit.png", "Рис. 7. Окно 1 после quit в окне 2: ephemeral-demo пропал, Node does not exist.", 140))

add(PageBreak())
add(Paragraph("4. Координатор расчёта под управлением ZooKeeper", H2))
add(Paragraph("Расчёт тот же, что в ДЗ №3 (src/mr.py): объект <font name='M'>clean/vacancies.jsonl</font> (100 922 060 байт) читается из RustFS "
              "чанками через HTTP Range, фильтр варианта (роль «Программист, разработчик» + 1С в названии или навыках), подсчёт сочетаний "
              "навыков размера 1, 2, 3. Чтобы лидера можно было остановить посреди расчёта, чанки уменьшены до 4 МиБ (25 штук), "
              "обрабатываются последовательно, и после каждого стоит пауза 1 с. Код — src/coordinator.py, тайм-аут сессии 10 с.", P))
add(code("""/hw4/<run>/
├── election/n_0000000000   эфемерный последовательный узел A  (лидер = наименьший номер)
├── election/n_0000000001   эфемерный узел B, B ставит watch на узел предшественника
├── epoch                   счётчик эпох: новый лидер делает set(epoch, e+1, version=v); версия узла = номер эпохи
├── checkpoint/chunk_NNNN   чанк обработан (данные "A epoch=1"); частичный Counter -> s3://hh-lake/hw4/<run>/partial/
└── result                  указатель на результат (создаётся пустым, version 0); CSV -> s3://hh-lake/hw4/<run>/result_*"""))
for t in ("<b>Выбор лидера.</b> Каждая копия создаёт эфемерный последовательный узел в election/. Владелец наименьшего номера — лидер; "
          "остальные следят только за предшественником (без «стада», когда все ждут одного узла). Когда сессия лидера истекает, "
          "ZooKeeper удаляет его эфемерный узел, и запасной получает уведомление.",
          "<b>Эпоха.</b> Став лидером, координатор увеличивает /epoch условной записью и запоминает версию узла — это его номер эпохи.",
          "<b>Чекпоинт.</b> После каждого чанка частичный результат пишется в S3, затем создаётся узел checkpoint/chunk_NNNN. "
          "Новый лидер пропускает чанки, для которых узел уже есть, а в reduce берёт частичные результаты всех 25 чанков.",
          "<b>Публикация.</b> Без защиты — <font name='M'>set(result)</font> без условий. С FENCING=1 — транзакция "
          "<font name='M'>check(epoch, version=моя_эпоха) + set_data(result)</font>: если эпоха сменилась, вся транзакция "
          "отклоняется с BadVersionError и указатель не меняется.",
          "<b>Сценарий</b> (src/scenario.py) запускает A, затем B, после 8-го чанка делает <font name='M'>kill -STOP</font> лидеру A, "
          "ждёт, пока B опубликует результат, держит A на паузе 40 с и делает <font name='M'>kill -CONT</font>. "
          "Время смены лидера = момент «СТАЛ ЛИДЕРОМ» в журнале B минус момент SIGSTOP."):
    add(Paragraph("• " + t, P))


def ev_rows(i):
    rel = lambda t: f"{t - i['t0']:.2f}"
    return [["t, с", "Событие"],
            [rel(i["stop"]), f"kill -STOP: лидер A приостановлен после {len(i['a_chunks'])} обработанных чанков"],
            [rel(i["b_lead"]), f"B стал лидером, эпоха 2 (через <b>{i['switch']:.2f} с</b> после SIGSTOP)"],
            [rel(i["b_lead"]), f"B: {i['b_skipped']} чанков уже в чекпоинте A — пропущены, расчёт продолжен с чанка {i['b_skipped'] + 1}"],
            [rel(i["b_pub"]), "B: " + i["b_pub_text"]],
            [rel(i["cont"]), f"kill -CONT: A возобновлён (пауза {i['pause']:.1f} с); сессия A истекла (LOST), переподключение"],
            [rel(i["a_pub"]), f"A: все {i['a_skipped']} оставшихся чанков в чекпоинте; " + i["a_pub_text"]]]


add(Paragraph("5. Этап 3. Приостановка лидера без защиты (FENCING=0)", H2))
add(table(ev_rows(NF), [16 * mm, 164 * mm]))
add(Spacer(1, 4))
add(Image("report/img/timeline_nofence.png", 170 * mm, 170 * mm * 2.6 / 8))
add(Paragraph("Рис. 8. Хронология прогона nofence по журналам logs/nofence_A.log и logs/nofence_B.log.", CAP))
add(Paragraph("Запасной B продолжил с чекпоинта, а возобновивший работу A не знает, что он уже не лидер: досчитал reduce и, не проверяя эпоху, "
              "перезаписал указатель. В итоге две строки «записал результат», указатель <font name='M'>/hw4/nofence/result</font> "
              "имеет <b>dataVersion = 2</b> и owner = A, epoch = 1 — актуальный результат B затёрт результатом устаревшего лидера. "
              "Здесь данные одинаковые, поэтому вреда нет, но в общем случае старый лидер мог бы записать устаревшие данные.", P))
add(screen("08_nofence_B_log.png", "Рис. 9. Журнал B: ожидание, «СТАЛ ЛИДЕРОМ, эпоха 2», пропуск 8 чанков из чекпоинта A, "
           "продолжение с 9-го, СОВПАЛО с ДЗ 3, записал результат (версия 1).", 170))
add(screen("09_nofence_A_log.png", "Рис. 10. Журнал A: 8 чанков до kill -STOP в 13:48:41 (строки выше — хвост предыдущего прогона).", 170))
add(screen("10_nofence_scenario.png", "Рис. 11. Итог сценария nofence: две строки «записал результат» (версии 2 и 1), смена лидера 11.63 с.", 170))

add(Paragraph("6. Этап 4. Приостановка лидера с защитой по эпохе (FENCING=1)", H2))
add(table(ev_rows(FE), [16 * mm, 164 * mm]))
add(Spacer(1, 4))
add(Image("report/img/timeline_fence.png", 170 * mm, 170 * mm * 2.6 / 8))
add(Paragraph("Рис. 12. Хронология прогона fence по журналам logs/fence_A.log и logs/fence_B.log.", CAP))
add(Paragraph("Сценарий тот же, но публикация идёт транзакцией с проверкой версии /epoch. A пришёл с эпохой 1, а текущая — 2, поэтому "
              "транзакция отклонена с <b>BadVersionError</b>, указатель не изменился: <font name='M'>/hw4/fence/result</font> — "
              "<b>dataVersion = 1</b>, owner = B, epoch = 2. Результат один.", P))
add(screen("11_fence_scenario_pointers.png", "Рис. 13. Итог сценария fence (BadVersionError у A) и указатели: "
           "fence — dataVersion 1, owner B; nofence — dataVersion 2, owner A.", 170))
add(Paragraph("Вывод <font name='M'>zkCli get -s</font> для указателей и узлов epoch (outputs/zk_pointers.txt):", CAP))
ptr = open("outputs/zk_pointers.txt", encoding="utf-8").read()
add(code("\n".join(l if len(l) < 125 else l[:122] + "..." for l in ptr.splitlines() if re.match(r"###|\{|dataVersion|^\d$|^$", l))))

add(Paragraph("7. Сверка результатов с ДЗ №3", H2))
ref = {"lines": "229,388", "variant": "23,627", "1": "3,112", "2": "48,616", "3": "338,381"}
rows = [["Показатель", "ДЗ №3", "nofence A", "nofence B", "fence A", "fence B"]]
for key, lab in (("lines", "строк в JSONL"), ("variant", "вакансий варианта"), ("1", "k=1, разных навыков"), ("2", "k=2, разных пар"), ("3", "k=3, разных троек")):
    vals = [(i["ctl"][w]["k"][key] if key in "123" else i["ctl"][w][key]) for i in (NF, FE) for w in ("A", "B")]
    rows.append([lab, ref[key]] + vals)
rows.append(["CSV побайтово = results/mr_k{1,2,3}.csv"] + ["—"] + ["да" if i["ctl"][w]["match"] else "НЕТ" for i in (NF, FE) for w in ("A", "B")])
add(table([[c.replace(",", " ") for c in r] for r in rows], [56 * mm, 25 * mm, 25 * mm, 25 * mm, 25 * mm, 25 * mm]))
add(Spacer(1, 4))
add(Paragraph("Все четыре координатора (в том числе B, который половину чанков взял из чекпоинта A) получили те же числа, что ДЗ №3 "
              "(logs/fault_test.log, logs/verify.log в репозитории ДЗ №3). Кроме того, координатор сравнивает свои CSV с файлами "
              "<font name='M'>s3://hh-lake/results/mr_k{1,2,3}.csv</font> из ДЗ №3 байт в байт — в каждом журнале три строки "
              "«СОВПАЛО с ДЗ 3». Самая частая пара: «1с программирование | 1с: предприятие 8» — 4 465 вакансий.", P))

add(Paragraph("8. Время смены лидера", H2))
add(table([["Прогон", "SIGSTOP → «СТАЛ ЛИДЕРОМ» у B", "SIGSTOP → B опубликовал", "Пауза A"],
           ["nofence (этап 3)", f"<b>{NF['switch']:.2f} с</b>", f"{NF['b_pub'] - NF['stop']:.2f} с", f"{NF['pause']:.1f} с"],
           ["fence (этап 4)", f"<b>{FE['switch']:.2f} с</b>", f"{FE['b_pub'] - FE['stop']:.2f} с", f"{FE['pause']:.1f} с"]],
          [40 * mm, 55 * mm, 50 * mm, 35 * mm]))
add(Spacer(1, 4))
add(Paragraph("Смена лидера занимает тайм-аут сессии (10 с) плюс до двух секунд. Приостановленный A перестаёт слать heartbeat, "
              "сервер замечает истечение сессии не мгновенно, а при очередной проверке (они идут с шагом tickTime = 2 с), "
              "удаляет эфемерный узел A и отправляет B уведомление watch. Сами перевыборы и увеличение эпохи у B занимают ~15 мс "
              "(«узел исчез — перевыборы» → «СТАЛ ЛИДЕРОМ»). Уменьшить время можно меньшим тайм-аутом сессии, но тогда "
              "короткая пауза GC или сети будет чаще приводить к ложной смене лидера.", P))

add(Paragraph("9. Ответы на вопросы", H2))
add(Paragraph("<b>Почему серверов три, а не два и не четыре?</b>", P))
add(Paragraph("ZooKeeper выполняет запись, только если её подтвердило большинство серверов — строго больше половины. При трёх серверах "
              "большинство — два, значит один сервер может упасть, и ансамбль продолжит работать; это и проверено на этапе 2. "
              "При двух серверах большинство — оба, поэтому падение любого из них останавливает систему: надёжность не выше, чем у "
              "одного сервера, а машин, которые могут сломаться, уже две. При четырёх большинство — три, то есть ансамбль "
              "по-прежнему переживает только один отказ, как и три сервера, но каждая запись ждёт лишнего подтверждения. "
              "Поэтому размер ансамбля выбирают нечётным: 3 сервера выдерживают 1 отказ, 5 — 2. Нечётное число ещё и исключает "
              "разбиение сети ровно пополам, когда ни одна из половин не может набрать большинство.", P))
add(Paragraph("<b>Почему защиту по эпохе проверяет получатель записи, а не ZooKeeper?</b>", P))
add(Paragraph("ZooKeeper может только констатировать, что сессия старого лидера истекла, и дать лидерство другому. Остановить сам процесс "
              "он не может и не может ему ничего сообщить, пока тот завис: в этапах 3–4 A стоял на паузе и после неё продолжил с того же "
              "места, считая себя лидером. Проверка «я ещё лидер?» перед записью не помогает: между проверкой и записью может "
              "снова случиться пауза (GC, своп, задержка сети), и ответ устареет. Надёжно только одно: писатель прикладывает к записи "
              "номер своей эпохи, а тот, кто запись принимает, в тот же момент атомарно сравнивает его с текущим и отказывает старому. "
              "Кроме того, ZooKeeper вообще не видит большинство записей — результат может уходить в S3, базу или файл. "
              "В нашей работе получатель — узел-указатель result, поэтому проверка сделана транзакцией check(epoch) + set(result) "
              "прямо в момент записи. Сами CSV в S3 при этом пишут оба координатора (под разными именами с номером эпохи), "
              "а опубликованным считается только тот, на который указывает result.", P))

add(Paragraph("10. Замечания", H2))
for t in ("ZooKeeper запущен в Docker, а не из трёх распакованных архивов: Java на машине нет, задание допускает этот вариант. "
          "Конфигурации и dataDir при этом свои у каждого сервера, как требуется.",
          "Расчёт искусственно замедлен (пауза 1 с после чанка), иначе он занимает около 10 с и лидера не успеть остановить посреди работы. "
          "На результат это не влияет — сверка с ДЗ №3 побайтовая.",
          "Старый лидер после возобновления не пересчитывает чанки: все они уже в чекпоинте, поэтому он сразу делает reduce и "
          "пытается опубликовать результат — именно этот момент и проверяется защитой по эпохе.",
          "Журналы logs/ — из последних прогонов, по которым сделаны скриншоты. Текст outputs/stage2.txt получен отдельным прогоном "
          "stage2.sh, поэтому роли серверов там другие (лидер zk2), что нормально для ZooKeeper."):
    add(Paragraph("• " + t, P))
add(Paragraph("Репозиторий: compose.yaml, zk/zkN/conf/zoo.cfg и myid, src/ (coordinator.py, scenario.py, build_report.py), stage2.sh, "
              "журналы logs/, выводы outputs/, скриншоты report/screens/, README и этот отчёт.", P))


def foot(c, d):
    c.setFont("A", 7.5); c.setFillColor(colors.grey)
    c.drawString(15 * mm, 8 * mm, "ДЗ №4 · Афанасьев Н.И., J3314 · вариант 1 (1С)")
    c.drawRightString(195 * mm, 8 * mm, f"стр. {d.page}")


SimpleDocTemplate("report/report.pdf", pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=15 * mm,
                  title="ДЗ №4 — вариант 1 (1С)", author="Афанасьев Н.И.").build(S, onFirstPage=foot, onLaterPages=foot)
print("ok")
