#!/usr/bin/env python3
# Проверка DNS-правил sing-box на роутере, не трогая боевой экземпляр.
# Поднимает временный sing-box без TUN на 127.0.0.1:5399 с секцией dns и rule-set'ами из заданного конфига,
# спрашивает домены и показывает, какое правило сработало и какой сервер ответил.
#
#   scp -O scripts/router-dnstest.py openwrt-ts:/tmp/ && \
#   ssh openwrt-ts 'python3 /tmp/router-dnstest.py [-c /path/config.json] ya.ru google.com ...; rm /tmp/router-dnstest.py'
#
# Удалённый DNS ходит через одну P1-ноду из конфига (как через группу proxy). Память: ~20 MB на время теста.
import json, os, random, re, shutil, socket, struct, subprocess, sys, time

args = sys.argv[1:]
config_path = "/etc/sing-box/config.json"
if args[:1] == ["-c"]:
    config_path, args = args[1], args[2:]
names = args or ["ya.ru", "ozon.ru", "avito.st", "login.tailscale.com", "google.com", "telegram.org"]

WORK, PORT = "/tmp/dnstest", 5399
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)

c = json.load(open(config_path))
node = next(o for o in c["outbounds"] if o["type"] == "vless" and o["tag"].startswith("P1-"))
test = {
    "log": {"level": "debug"},
    "inbounds": [{"type": "direct", "tag": "dns-in", "listen": "127.0.0.1", "listen_port": PORT}],
    "outbounds": [dict(node, tag="proxy"), {"type": "direct", "tag": "direct"}],
    "route": {"rule_set": c["route"]["rule_set"], "final": "proxy",
              "rules": [{"inbound": "dns-in", "action": "hijack-dns"}],
              "default_domain_resolver": c["route"].get("default_domain_resolver")},
    "dns": c["dns"],
}
json.dump(test, open(f"{WORK}/config.json", "w"))
log = open(f"{WORK}/log", "w")
proc = subprocess.Popen(["sing-box", "run", "-c", f"{WORK}/config.json", "-D", WORK], stdout=log, stderr=log,
                        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "GOMEMLIMIT": "25MiB", "GOGC": "30"})
time.sleep(3)

def query(name):
    msg = struct.pack(">HHHHHH", random.randint(0, 65535), 0x0100, 1, 0, 0, 0)
    for part in name.split("."):
        msg += bytes([len(part)]) + part.encode()
    msg += b"\0" + struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(8)
    t0 = time.time()
    try:
        s.sendto(msg, ("127.0.0.1", PORT))
        data = s.recv(4096)
    except Exception as e:
        return f"ОШИБКА {e}"
    return f"rcode={data[3] & 15} {int((time.time() - t0) * 1000)} мс"

results = [(n, query(n)) for n in names]
time.sleep(1)
proc.terminate()
proc.wait()
log.close()

text = re.sub(r"\x1b\[[0-9;]*m", "", open(f"{WORK}/log").read())
for name, res in results:
    # Строка вида "dns: match[5] domain_suffix=[...] => route(dns-ru)"; нет совпадения — сработал final.
    ids = re.findall(r"\[(\d+) \d+ms\] dns: exchange " + re.escape(name) + r"\. IN A", text)
    rule = "final → " + c["dns"].get("final", "?")
    for i in ids:
        m = re.search(r"\[" + i + r" \d+ms\] dns: match\[\d+\] (.*?) => route\((.*?)\)", text)
        if m:
            rule = f"{m.group(1)[:50]} → {m.group(2)}"
    print(f"{name:30s} {res:18s} {rule}")
errors = [l for l in text.splitlines() if l.startswith(("ERROR[", "FATAL["))]
if errors:
    print("\nВ логе тестового экземпляра есть ошибки:")
    print("\n".join(errors)[:2000])
shutil.rmtree(WORK, ignore_errors=True)
