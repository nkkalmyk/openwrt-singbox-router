import base64, copy, datetime, ipaddress, json, os, shutil, signal, subprocess, sys, tempfile, urllib.parse, urllib.request

# Коды выхода: 0 — конфиг обновлён, 2 — изменений нет, 1 — ошибка (конфиг не тронут).

# Жёсткий лимит на всю работу: сервер подписки, отвечающий по капле, не должен держать общую
# блокировку (на ней же сторожа) бесконечно.
TIME_LIMIT = 240

class TimeLimit(Exception):
    pass

def on_alarm(signum, frame):
    raise TimeLimit(f"не уложился в {TIME_LIMIT} с")

signal.signal(signal.SIGALRM, on_alarm)
signal.alarm(TIME_LIMIT)

SUB1_URL = "https://subscription.example/CHANGE_ME"
SUB2_URL = "https://subscription.example/CHANGE_ME"

CONFIG_PATH = os.environ.get("SB_CONFIG", "/etc/sing-box/config.json")
BYPASS_STATE_PATH = os.environ.get("SB_BYPASS_STATE", "/etc/sing-box/.bypass-state")

PREFIXES = ("P1", "P2")
# Группы, которые собираются только из нод с заданным префиксом (Gemini не любит P2).
PREFIX_GROUPS = {"gemini-auto": "P1-", "gemini-select": "P1-"}

SUB1_EXCLUDE = ["россия", "russia", "украин", "ukraine"]
SUB1_EU_KEYWORDS = ["венгр", "hungary", "словак", "slovakia", "чехи", "czech",
                     "болгар", "bulgaria", "хорват", "croatia", "румын", "romania",
                     "польш", "poland", "дани", "denmark", "литв", "lithuania",
                     "финлянд", "finland", "норвеги", "norway", "швеци", "sweden",
                     "герман", "germany", "нидерланд", "netherlands", "бельги", "belgium",
                     "франци", "france", "швейцар", "switzerland", "австри", "austria",
                     "испани", "spain", "итали", "italy", "ирланди", "ireland",
                     "великобритан", "britain", "португали", "portugal", "греци", "greece"]

SUB2_EXCLUDE = ["lte", "gaming", "free", "россия", "russia"]

# vless + xtls-rprx-vision в sing-box работает только поверх TCP.
SUPPORTED_TRANSPORTS = {"", "tcp", "raw"}
SUPPORTED_SECURITY = {"reality", "tls"}

def decode_sub(raw):
    if b"vless://" in raw[:2000]:
        return raw.decode('utf-8', errors='ignore')
    data = b"".join(raw.split())
    data += b"=" * (-len(data) % 4)
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            text = decoder(data).decode('utf-8', errors='ignore')
            if "vless://" in text:
                return text
        except Exception:
            pass
    return raw.decode('utf-8', errors='ignore')

def fetch_plain(url):
    return decode_sub(urllib.request.urlopen(url, timeout=30).read())

def fetch_with_cookies(url):
    fd, cookie_jar = tempfile.mkstemp(prefix="sub2-cookies-")
    os.close(fd)
    try:
        result = subprocess.run(
            ["curl", "-sL", "-A",
             "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
             "-c", cookie_jar, "-b", cookie_jar, "--max-redirs", "10",
             "--max-time", "30", url],
            capture_output=True, timeout=35
        )
    finally:
        if os.path.exists(cookie_jar):
            os.remove(cookie_jar)

    if result.returncode != 0:
        raise RuntimeError(f"curl завершился с ошибкой: {result.stderr.decode(errors='ignore')[:200]}")
    return decode_sub(result.stdout)

# Запасной путь — мимо VPN. Нужен, когда мертвы все ноды: тогда не работает и обычный DNS
# для зарубежных доменов (он ходит через VPN), поэтому адрес берём у российского DNS напрямую.
DIRECT_DNS = "77.88.8.8"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

def wan_device():
    try:
        st = json.loads(subprocess.run(["ubus", "call", "network.interface.wan", "status"],
                                       capture_output=True, timeout=10).stdout)
        return st.get("l3_device") or st.get("device") or "wan"
    except Exception:
        return "wan"

def resolve_direct(host):
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    out = subprocess.run(["nslookup", host, DIRECT_DNS], capture_output=True,
                         text=True, timeout=15).stdout
    # До "Name:" — адрес самого DNS-сервера, после — ответы.
    for line in out.split("Name:", 1)[-1].splitlines() if "Name:" in out else []:
        parts = line.split()
        if parts and parts[0].startswith("Address"):
            try:
                if ipaddress.ip_address(parts[-1]).version == 4:
                    return parts[-1]
            except ValueError:
                pass
    raise RuntimeError(f"{host} не резолвится через {DIRECT_DNS}")

def fetch_direct(url):
    # curl привязан к WAN, поэтому идёт мимо туннеля. Редиректы ведём сами,
    # чтобы адрес каждого нового хоста тоже брался у DIRECT_DNS.
    wan = wan_device()
    fd, cookie_jar = tempfile.mkstemp(prefix="sub-direct-cookies-")
    os.close(fd)
    try:
        for _ in range(10):
            p = urllib.parse.urlsplit(url)
            port = p.port or (443 if p.scheme == "https" else 80)
            ip = resolve_direct(p.hostname)
            result = subprocess.run(
                ["curl", "-s", "-A", UA, "-c", cookie_jar, "-b", cookie_jar,
                 "--interface", wan, "--resolve", f"{p.hostname}:{port}:{ip}",
                 "--max-time", "30", "-w", "\n%{http_code} %{redirect_url}", url],
                capture_output=True, timeout=35
            )
            if result.returncode != 0:
                raise RuntimeError(f"curl напрямую завершился с кодом {result.returncode}")
            body, _, meta = result.stdout.rpartition(b"\n")
            code, _, location = meta.decode(errors="ignore").partition(" ")
            if code.startswith("3") and location:
                url = location
                continue
            if code != "200":
                raise RuntimeError(f"напрямую HTTP {code}")
            return decode_sub(body)
        raise RuntimeError("напрямую слишком много редиректов")
    finally:
        os.remove(cookie_jar)

def parse_line(url):
    try:
        parsed = urllib.parse.urlparse(url)
        if not parsed.scheme.startswith('vless') or '@' not in parsed.netloc:
            return None
        user_info, host_port = parsed.netloc.split('@', 1)
        server, port = host_port.rsplit(':', 1) if ':' in host_port else (host_port, "443")
        query = urllib.parse.parse_qs(parsed.query)
        fragment = urllib.parse.unquote(parsed.fragment).strip()
        pbk = query.get("pbk", [""])[0]
        return {
            "server": server.strip("[]"), "port": int(port),
            "uuid": urllib.parse.unquote(user_info), "fragment": fragment,
            "flow": query.get("flow", [None])[0],
            "type": query.get("type", ["tcp"])[0].lower(),
            "security": query.get("security", ["reality" if pbk else ""])[0].lower(),
            "sni": query.get("sni", [""])[0],
            "fp": query.get("fp", ["firefox"])[0],
            "pbk": pbk,
            "sid": query.get("sid", [""])[0],
        }
    except Exception:
        return None

def build_outbound(prefix, rec, tags_seen):
    base = (rec["fragment"].replace(" ", "-") or f"node-{rec['server'].replace('.', '-')}")
    tag = f"{prefix}-{base}"[:40]
    unique_tag, counter = tag, 1
    while unique_tag in tags_seen:
        unique_tag = f"{tag}-{counter}"
        counter += 1
    tags_seen.add(unique_tag)

    outbound = {"type": "vless", "tag": unique_tag, "server": rec["server"],
                "server_port": rec["port"], "uuid": rec["uuid"]}
    if rec["flow"]:
        outbound["flow"] = rec["flow"]
    tls = {"enabled": True, "server_name": rec["sni"],
           "utls": {"enabled": True, "fingerprint": rec["fp"]}}
    if rec["security"] == "reality":
        tls["reality"] = {"enabled": True, "public_key": rec["pbk"], "short_id": rec["sid"]}
    outbound["tls"] = tls
    return outbound

def collect(text, prefix, exclude_kw, require_kw, tags_seen):
    result, skipped = [], 0
    for line in text.splitlines():
        rec = parse_line(line.strip())
        if not rec:
            continue
        frag_lower = rec["fragment"].lower()
        if any(k in frag_lower for k in exclude_kw):
            continue
        if require_kw and not any(k in frag_lower for k in require_kw):
            continue
        if rec["type"] not in SUPPORTED_TRANSPORTS or rec["security"] not in SUPPORTED_SECURITY:
            skipped += 1
            continue
        result.append(build_outbound(prefix, rec, tags_seen))
    if skipped:
        print(f"{prefix}: пропущено {skipped} нод с неподдерживаемым транспортом")
    return result

def is_node(tag):
    return tag.split("-", 1)[0] in PREFIXES

def essence(o):
    # P1 при каждой выдаче подписки подставляет случайные SNI/short_id для тех же серверов.
    # Нода считается прежней, если совпадает всё, что определяет сам сервер.
    reality = o.get("tls", {}).get("reality", {})
    return (o.get("server"), o.get("server_port"), o.get("uuid"), o.get("flow"),
            reality.get("enabled", False), reality.get("public_key"))

def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

try:
    content_str = open(CONFIG_PATH, 'r', encoding='utf-8', errors='ignore').read()
    old_config = (json.loads(content_str) if content_str.strip().startswith('{')
                  else json.JSONDecoder().raw_decode(content_str.lstrip())[0])
    config = copy.deepcopy(old_config)

    tags_seen = set()
    new_outbounds = []
    fetched_any = False
    for prefix, fetch, url, exclude_kw, require_kw in (
            ("P1", fetch_plain, SUB1_URL, SUB1_EXCLUDE, SUB1_EU_KEYWORDS),
            ("P2", fetch_with_cookies, SUB2_URL, SUB2_EXCLUDE, None)):
        nodes, errors = [], []
        for how, getter in (("через VPN", fetch), ("напрямую", fetch_direct)):
            try:
                nodes = collect(getter(url), prefix, exclude_kw, require_kw, tags_seen)
                if not nodes:
                    raise RuntimeError("подходящих нод нет")
                print(f"{prefix}-подписка ({how}): {len(nodes)} нод")
                break
            except Exception as e:
                errors.append(f"{how}: {e}")
        if nodes:
            fetched_any = True
        else:
            # Подписка упала — оставляем её прежние ноды, а не выкидываем.
            nodes = [o for o in config.get("outbounds", [])
                     if o.get("type") == "vless" and o.get("tag", "").startswith(prefix + "-")]
            tags_seen.update(o["tag"] for o in nodes)
            print(f"{prefix}-подписка не загрузилась ({'; '.join(errors)}) — оставляю прежние {len(nodes)} нод")
        new_outbounds += nodes

    if not fetched_any:
        print("Ни одна подписка не загрузилась — конфиг не тронут.")
        sys.exit(1)
    if not new_outbounds:
        print("Подходящие ноды не найдены — конфиг не тронут.")
        sys.exit(1)

    # Ноды, у которых не изменился сервер, оставляем как были — без лишних перезапусков.
    old_by_tag = {o["tag"]: o for o in config.get("outbounds", []) if o.get("type") == "vless"}
    new_outbounds = [old_by_tag[o["tag"]] if o["tag"] in old_by_tag
                     and essence(old_by_tag[o["tag"]]) == essence(o) else o
                     for o in new_outbounds]

    # Всё, что не ноды подписок (группы, direct, свои outbound'ы), сохраняем.
    others = [o for o in config.get("outbounds", [])
              if not (o.get("type") == "vless" and is_node(o.get("tag", "")))]
    all_tags = [o["tag"] for o in new_outbounds]

    for o in others:
        t, tag = o.get("type"), o.get("tag")
        if t not in ("selector", "urltest"):
            continue
        keep = [x for x in o.get("outbounds", []) if not is_node(x)]
        if tag in PREFIX_GROUPS:
            picked = [x for x in all_tags if x.startswith(PREFIX_GROUPS[tag])] or all_tags
        else:
            picked = all_tags
        o["outbounds"] = keep + picked
        if t == "urltest":
            o.pop("default", None)
        elif o.get("default") not in o["outbounds"]:
            o["default"] = o["outbounds"][0]

    config["outbounds"] = new_outbounds + others

    ip_bypass = []
    domain_bases = set()
    for n in new_outbounds:
        srv = n["server"]
        try:
            ipaddress.ip_address(srv)
            ip_bypass.append(f"{srv}/32" if ":" not in srv else f"{srv}/128")
        except ValueError:
            parts = srv.split(".")
            if len(parts) >= 2:
                domain_bases.add(".".join(parts[-2:]))

    try:
        old_state = json.load(open(BYPASS_STATE_PATH, encoding="utf-8"))
        old_ips, old_domains = set(old_state.get("ips", [])), set(old_state.get("domains", []))
    except Exception:
        old_ips, old_domains = set(), set()
    known_ips, known_domains = old_ips | set(ip_bypass), old_domains | domain_bases

    if "route" in config and "rules" in config["route"]:
        rules = [r for r in config["route"]["rules"]
                 if not (r.get("outbound") == "direct" and "ip_cidr" in r
                         and set(r["ip_cidr"]) & known_ips)
                 and not (r.get("outbound") == "direct" and "domain_suffix" in r
                          and set(r["domain_suffix"]) & known_domains)]
        # Обход для нод — сразу после служебных правил: sniff должен отработать
        # раньше любых доменных правил.
        head = 0
        while head < len(rules) and rules[head].get("action") in ("hijack-dns", "sniff"):
            head += 1
        if domain_bases:
            rules.insert(head, {"domain_suffix": sorted(domain_bases), "outbound": "direct"})
        if ip_bypass:
            rules.insert(head, {"ip_cidr": sorted(set(ip_bypass)), "outbound": "direct"})
        if not any("100.64.0.0/10" in (r.get("ip_cidr") or []) for r in rules):
            rules.insert(head, {"ip_cidr": ["100.64.0.0/10"], "outbound": "direct"})
        config["route"]["rules"] = rules

    new_state = {"ips": ip_bypass, "domains": sorted(domain_bases)}
    if json.dumps(config, sort_keys=True) == json.dumps(old_config, sort_keys=True):
        print(f"Изменений нет. Нод: {len(new_outbounds)}")
        sys.exit(2)

    # Дальше только быстрые локальные операции — лимит больше не нужен.
    signal.alarm(0)
    backup = f"{CONFIG_PATH}.bak.{datetime.datetime.now():%Y%m%d%H%M%S}"
    shutil.copy(CONFIG_PATH, backup)
    # Храним один бэкап: вместе с config.json.good (последний рабочий) это две копии.
    backups = sorted(f for f in os.listdir(os.path.dirname(CONFIG_PATH))
                     if f.startswith(os.path.basename(CONFIG_PATH) + ".bak."))
    for f in backups[:-1]:
        os.remove(os.path.join(os.path.dirname(CONFIG_PATH), f))
    write_json(CONFIG_PATH, config)
    write_json(BYPASS_STATE_PATH, new_state)

    print(f"Успешно. Всего нод: {len(new_outbounds)}")
    print(f"Доменный bypass: {sorted(domain_bases)}")
    print(f"Бэкап: {backup}")
except Exception as e:
    print(f"Ошибка: {e}")
    sys.exit(1)
