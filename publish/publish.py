#!/usr/bin/env python3
"""Публичная копия папки роутера: собрать в .public/, вычистить личное, проверить, (по желанию) отправить в GitHub.

  python3 publish/publish.py                  — собрать и проверить, ничего не отправляет
  python3 publish/publish.py --push [-m ТЕКСТ] — то же + commit и push (.public/ — git-клон репозитория)

Берутся только файлы из FILES. Конфиг sing-box превращается в пример (ноды, ключи, секрет — заглушки),
ручные списки сайтов — в пустые заглушки, статические DHCP-аренды выкидываются, MAC/DUID/ULA маскируются.
Личные замены (IP, имена, провайдеры) — в publish/private.txt, он в выгрузку не попадает никогда:
  A => B        заменить подстроку A на B (B может быть пустым)
  ip:A => B     заменить IPv4-адрес A целиком (192.0.2.1 не задевает 192.0.2.10)
  re:A => B     заменить регулярное выражение A (Python re, MULTILINE; в B можно \\1)
  drop:A        удалить строки, в которых есть регулярное выражение A
  deny:A        регулярное выражение, которого не должно остаться (только проверка)
Всё, что заменяется или удаляется, потом ищется в выгрузке ещё раз. Плюс общие проверки: IP вне списка
разрешённых, MAC, UUID, e-mail, ссылки на ноды, длинные ключи, слова из настоящих тегов нод (города, тарифы). Любая находка — выход с ошибкой,
и --push не выполняется.
"""
import argparse, glob, ipaddress, json, os, re, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, ".public")
PRIVATE = os.path.join(ROOT, "publish", "private.txt")

# (источник относительно ROOT или glob, путь в выгрузке; None — тот же)
FILES = [
    ("publish/README.en.public.md", "README.md"),
    ("publish/README.public.md", "README.ru.md"),
    ("publish/LICENSE.public.md", "LICENSE"),
    ("publish/SETUP.public.md", "docs/setup.md"),
    ("publish/TELEGRAM.public.md", "docs/telegram.md"),
    ("CLAUDE.md", None),
    ("docs/*.md", None),
    ("scripts/pull-from-router.sh", None),
    ("scripts/router-dnstest.py", None),
    ("publish/publish.py", None),
    ("mirror/usr/bin/*", None),
    ("mirror/usr/lib/*", None),
    ("mirror/etc/init.d/*", None),
    ("mirror/etc/config/*", None),
    ("mirror/etc/crontabs/root", None),
    ("mirror/etc/apk/world", None),
    ("mirror/etc/avahi/avahi-daemon.conf", None),
    ("mirror/etc/sysctl.conf", None),
    ("mirror/etc/sysupgrade.conf", None),
    ("mirror/etc/rc.local", None),
    ("mirror/etc/sing-box/config.json", None),
    ("mirror/etc/sing-box/rule-set/my-*.json", None),
]
SYMLINKS = {"AGENTS.md": "CLAUDE.md"}
GITIGNORE = ".DS_Store\nbackups/\nmirror/STATE.txt\npublish/private.txt\n"

# IP, которые можно оставлять: публичные DNS, служебные и документационные диапазоны.
ALLOWED_NETS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "0.0.0.0/32", "1.1.1.1/32", "1.0.0.1/32", "8.8.8.8/32", "8.8.4.4/32",
    "77.88.8.8/32", "77.88.8.1/32", "100.64.0.0/32", "172.19.0.0/30",
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]

NODE_TYPES = {"vless", "vmess", "trojan", "shadowsocks", "hysteria2", "tuic"}
ZERO_UUID = "00000000-0000-0000-0000-000000000000"


# --- особые файлы -------------------------------------------------------------------------------

def example_node(o, tag, i):
    e = {"type": o["type"], "tag": tag, "server": f"203.0.113.{10 + i}", "server_port": 443, "uuid": ZERO_UUID}
    if "flow" in o:
        e["flow"] = o["flow"]
    tls = o.get("tls")
    if tls:
        t = {"enabled": True, "server_name": "www.example.com"}
        if "utls" in tls:
            t["utls"] = {"enabled": True, "fingerprint": "chrome"}   # настоящий отпечаток выдаёт провайдера
        if tls.get("reality", {}).get("enabled"):
            t["reality"] = {"enabled": True, "public_key": "REALITY_PUBLIC_KEY", "short_id": "0123abcd"}
        e["tls"] = t
    return e


def sanitize_singbox(text):
    c = json.loads(text)
    nodes = [o for o in c["outbounds"] if o.get("type") in NODE_TYPES]
    servers = {o.get("server", "") for o in nodes} - {""}
    rename, keep, per_prefix = {}, [], {}
    for o in nodes:
        prefix = o["tag"].split("-", 1)[0]
        per_prefix[prefix] = per_prefix.get(prefix, 0) + 1
        rename[o["tag"]] = f"{prefix}-example-{per_prefix[prefix]}"
        if per_prefix[prefix] <= 2:
            keep.append(example_node(o, rename[o["tag"]], len(keep) + 1))
    kept = {o["tag"] for o in keep}

    outbounds, inserted = [], False
    for o in c["outbounds"]:
        if o.get("type") in NODE_TYPES:
            if not inserted:
                outbounds += keep
                inserted = True
            continue
        if "outbounds" in o:
            lst = []
            for t in o["outbounds"]:
                t = rename.get(t, t)
                if (t in kept or t not in rename.values()) and t not in lst:
                    lst.append(t)
            o["outbounds"] = lst
        if o.get("default") in rename:
            o["default"] = rename[o["default"]]
        outbounds.append(o)
    c["outbounds"] = outbounds

    def hits_node(v):
        v = v.split("/")[0].lstrip(".")
        return any(s == v or s.endswith("." + v) for s in servers)

    for r in c.get("route", {}).get("rules", []):
        if any(hits_node(v) for v in r.get("ip_cidr", [])):
            r["ip_cidr"] = [o["server"] + "/32" for o in keep]
        for key in ("domain", "domain_suffix"):
            if any(hits_node(v) for v in r.get(key, [])):
                r[key] = ["nodes.example.com"]

    api = c.get("experimental", {}).get("clash_api")
    if api and "secret" in api:
        api["secret"] = "CHANGE_ME"
    return json.dumps(c, ensure_ascii=False, indent=2) + "\n"


def placeholder_ruleset(text):
    # Ручные списки — личное (какие сайты человек открывает); оставляем заглушки, как в пустом списке.
    c = json.loads(text)
    for r in c.get("rules", []):
        for key in list(r):
            if key == "ip_cidr":
                r[key] = ["192.0.2.255/32"]
            elif key.startswith("domain"):
                r[key] = ["placeholder.invalid"]
    return json.dumps(c, ensure_ascii=False, indent=2) + "\n"


def drop_dhcp_hosts(text):
    # Статические аренды: MAC и имена домашних устройств.
    return re.sub(r"\nconfig host\n(?:[ \t]+[^\n]*\n?)*", "\n", text)


SPECIAL = [
    (r"mirror/etc/sing-box/config\.json$", sanitize_singbox),
    (r"mirror/etc/sing-box/rule-set/my-[^/]*\.json$", placeholder_ruleset),
    (r"mirror/etc/config/dhcp$", drop_dhcp_hosts),
]

GENERIC = [
    (re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"), "XX:XX:XX:XX:XX:XX"),
    (re.compile(r"(option dhcp_default_duid ')[0-9A-Fa-f]+'"), r"\1DUID'"),
    (re.compile(r"\bf[cd][0-9a-f]{2}(?::[0-9a-f]{1,4}){0,6}::/(\d+)"), r"fdXX:XXXX:XXXX::/\1"),
]


# --- личные правила -----------------------------------------------------------------------------

def ip_regex(ip):
    return r"(?<![\d.])" + re.escape(ip) + r"(?![\d]|\.\d)"


def load_private():
    rules = []
    with open(PRIVATE, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            m = re.match(r"^(drop|deny):(.+)$", line)
            if m:
                rules.append((m.group(1), re.compile(m.group(2), re.M), None))
                continue
            m = re.match(r"^(?:(ip|re):)?(.+?) =>(?: (.*))?$", line)
            if not m:
                sys.exit(f"private.txt:{n}: не понял строку")
            kind, a, b = m.group(1) or "lit", m.group(2), m.group(3) or ""
            if kind == "ip":
                rules.append(("re", re.compile(ip_regex(a)), b))
            elif kind == "re":
                rules.append(("re", re.compile(a, re.M), b))
            else:
                rules.append(("lit", a, b))
    return rules


def apply_private(text, rules):
    for kind, a, b in rules:
        if kind == "lit":
            text = text.replace(a, b)
        elif kind == "re":
            text = a.sub(b, text)
        elif kind == "drop":
            text = "".join(l for l in text.splitlines(keepends=True) if not a.search(l))
    return text


# --- проверки -----------------------------------------------------------------------------------

IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d]|\.\d)")
CHECKS = [
    ("MAC", re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")),
    ("IPv6", re.compile(r"\b[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){3,7}\b|\bf[cd][0-9a-f]{2}:[0-9a-f]{1,4}:", re.I)),
    ("UUID", re.compile(r"\b(?!00000000-0000-0000-0000-000000000000)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)),
    ("e-mail", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b")),
    ("ссылка на ноду", re.compile(r"\b(?:vless|vmess|trojan|ss|hysteria2?|tuic)://[^\s\"']{8,}", re.I)),
    ("hex-токен", re.compile(r"(?<![~\w])[0-9a-fA-F]{16,}\b")),   # после ~ — хэш версии пакета apk
]
TOKEN = re.compile(r"[A-Za-z0-9+/_-]{24,}={0,2}")


def looks_like_key(s):
    # base64-ключи почти всегда содержат и строчные, и заглавные, и цифры; пути и имена переменных — нет.
    return all(re.search(p, s) for p in (r"[a-z]", r"[A-Z]", r"\d"))


def node_words_regex():
    # Слова из настоящих тегов нод (города, страны, тарифы провайдера) — их не должно быть нигде в выгрузке.
    try:
        c = json.load(open(os.path.join(ROOT, "mirror/etc/sing-box/config.json"), encoding="utf-8"))
    except (OSError, ValueError):
        sys.exit("не прочитал mirror/etc/sing-box/config.json — без него не проверить названия нод")
    words = set()
    for o in c.get("outbounds", []):
        if o.get("type") in NODE_TYPES:
            for w in re.split(r"\W+", o["tag"].split("-", 1)[-1]):
                if len(w) >= 3 and not w.isdigit():
                    words.add(w)
    if not words:
        return None
    return re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(words, key=len, reverse=True))) + r")\b")


def check(path, text, rules, node_rx, authored=False):
    problems = []
    if node_rx:
        for m in node_rx.finditer(text):
            problems.append(f"слово из названия ноды: {m.group(0)}")
    for m in IPV4.finditer(text):
        try:
            ip = ipaddress.ip_address(m.group(1))
        except ValueError:
            continue
        if not any(ip in net for net in ALLOWED_NETS) and not (authored and ip.is_private):
            problems.append(f"IP {m.group(1)}")
    for name, rx in CHECKS:
        for m in rx.finditer(text):
            problems.append(f"{name}: {m.group(0)[:40]}")
    for m in TOKEN.finditer(text):
        if looks_like_key(m.group(0)):
            problems.append(f"похоже на ключ: {m.group(0)[:12]}…")
    for kind, a, _ in rules:
        if authored and kind in ("lit", "re"):
            continue
        if kind == "lit" and a in text:
            problems.append(f"осталось из private.txt: {a[:30]}")
        elif kind == "re" and a.sub(_, text) != text:   # повторная замена что-то меняет — значит, осталось
            problems.append(f"осталось по правилу private.txt: {a.pattern[:40]}")
        elif kind in ("drop", "deny") and a.search(text):
            problems.append(f"совпало правило private.txt: {a.pattern[:40]}")
    return [f"{path}: {p}" for p in problems]


# --- сборка -------------------------------------------------------------------------------------

def collect():
    pairs = []
    for src, dst in FILES:
        matches = sorted(glob.glob(os.path.join(ROOT, src)))
        if not matches:
            sys.exit(f"нет файла: {src}")
        for m in matches:
            if os.path.isfile(m) and not os.path.basename(m).startswith("."):
                rel = os.path.relpath(m, ROOT)
                pairs.append((rel, dst or rel))
    return pairs


def build():
    rules = load_private()
    node_rx = node_words_regex()
    if os.path.isdir(OUT):
        for name in os.listdir(OUT):
            if name != ".git":
                p = os.path.join(OUT, name)
                shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)
    os.makedirs(OUT, exist_ok=True)

    problems, written = [], 0
    for src, dst in collect():
        text = open(os.path.join(ROOT, src), encoding="utf-8").read()
        for pattern, fn in SPECIAL:
            if re.search(pattern, dst):
                text = fn(text)
        for rx, repl in GENERIC:
            text = rx.sub(repl, text)
        # Тексты, написанные сразу для публики (publish/*.public.md): личные замены не нужны и испортили бы
        # примеры вроде стандартного UPSTREAM_IP; частные адреса в них допустимы, остальные проверки — те же.
        authored = src.startswith("publish/") and src.endswith(".public.md")
        if not authored:
            text = apply_private(text, rules)
        problems += check(dst, text, rules, node_rx, authored)
        out = os.path.join(OUT, dst)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        shutil.copymode(os.path.join(ROOT, src), out)
        written += 1
    for link, target in SYMLINKS.items():
        os.symlink(target, os.path.join(OUT, link))
    with open(os.path.join(OUT, ".gitignore"), "w") as f:
        f.write(GITIGNORE)
    if os.path.exists(os.path.join(OUT, "publish", "private.txt")):
        problems.append("publish/private.txt попал в выгрузку")
    return written, problems


def git(*args):
    r = subprocess.run(["git", "-C", OUT, *args], text=True, capture_output=True)
    if r.returncode:
        sys.exit(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="закоммитить и отправить в GitHub")
    ap.add_argument("-m", "--message", default="Обновление публичной копии")
    a = ap.parse_args()

    written, problems = build()
    print(f"Собрано файлов: {written} → {OUT}")
    if problems:
        print("\nНЕ ПУБЛИКУЮ — найдено подозрительное:")
        for p in problems:
            print("  " + p)
        sys.exit(1)
    print("Проверка пройдена: IP, MAC, UUID, ключей, адресов и слов из private.txt не найдено.")

    if not a.push:
        return
    if not os.path.isdir(os.path.join(OUT, ".git")):
        sys.exit(".public/ ещё не git-репозиторий: склонируй туда репозиторий с GitHub (git clone … .public).")
    git("add", "-A")
    if not git("status", "--porcelain").strip():
        print("Изменений нет — отправлять нечего.")
        return
    git("commit", "-q", "-m", a.message)
    git("push", "-q", "origin", "HEAD:main")
    print("Отправлено: " + git("remote", "get-url", "origin").strip())


if __name__ == "__main__":
    main()
