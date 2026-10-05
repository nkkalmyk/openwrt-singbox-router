# Общий вид вывода для sb-ping, router-report, sb-route-test: имена нод без флагов и цвета (только в терминале).
# Подключать: sys.path.insert(0, "/usr/lib"); from sb_ui import ...  (см. sb-ping)
import os, re, sys

# Имена нод только для показа: без флагов-эмодзи (Termius и др. их не рисуют и сбивают колонки) и без
# хвоста с названием тарифа (в теге он часто обрезан до 40 символов). Теги в конфиге не трогаем:
# по ним идёт выбор нод.
FLAGS = re.compile("[\U0001F1E6-\U0001F1FF]")
TAG = re.compile(r"\b(?:P1|P2)-[^\s()/]+")

def pretty(tag):
    s = FLAGS.sub("", tag)
    s = re.sub(r",-?E(?:x(?:t(?:r(?:a)?)?)?)?$", "", s)
    s = re.sub(r"^(P1|P2)-+", r"\1 ", s)
    s = s.replace(",-", ", ")
    s = re.sub(r"--+", "-", s)
    return s.strip(" ,-")

def pretty_text(text):
    return TAG.sub(lambda m: pretty(m.group(0)), text)

# Цвета — только 8 базовых (читаются и на светлой, и на тёмной теме). Красим, если вывод идёт в терминал и
# нет NO_COLOR. `vpn` сам проверяет терминал и передаёт решение в VPN_COLOR: его вывод идёт через трубу.
def color_on():
    if os.environ.get("NO_COLOR"):
        return False
    if "VPN_COLOR" in os.environ:
        return os.environ["VPN_COLOR"] == "1"
    return sys.stdout.isatty()

_CODES = {"ok": "32", "warn": "33", "bad": "31", "dim": "90", "head": "1"}

def paint(text, kind):
    return f"\033[{_CODES[kind]}m{text}\033[0m" if kind in _CODES and color_on() else text

def delay_kind(ms):
    return "dim" if ms <= 0 else "ok" if ms < 150 else "warn" if ms < 300 else "bad"

# Строка журнала автоматики → цвет по смыслу (None — без цвета).
_BAD = ("НЕВАЛИДН", "откат", "Перезагружаю", "не запущен", "не поднялись", "Мертвы все", "требует входа")
_OK = ("снова работает", "Применено, связь есть", "снова в строю", "готово")
_WARN = ("перетест", "обновляю подписки", "перезапускаю", "Перезапуск", "Ноды изменились")

def event_kind(line):
    for words, kind in ((_BAD, "bad"), (_OK, "ok"), (_WARN, "warn")):
        if any(w in line for w in words):
            return kind
    return None

# Строка отчёта (vpn report) → цвет.
def report_kind(line):
    s = line.lstrip()
    if line.startswith("ИТОГ: всё хорошо"):
        return "ok"
    if line.startswith("ИТОГ: сейчас"):
        return "warn"
    if line.startswith("ИТОГ: ПРОБЛЕМА") or s.startswith("! "):
        return "bad"
    if s.startswith("~ "):
        return "warn"
    if s.startswith("· "):
        return "dim"
    if line.startswith("====="):
        return "head"
    return None
