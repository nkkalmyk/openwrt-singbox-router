# Как собрать такой же роутер самому

Пошаговая инструкция: из чистого OpenWrt получить ту же схему, что описана в [architecture.md](architecture.md).
- **Российские сайты — напрямую, всё остальное — через VPN** (VLESS + Reality).
- **Раздельный DNS**: российские имена спрашиваются у Яндекса напрямую, остальные — у Cloudflare через VPN.
- **Подписка обновляется сама**, мёртвые ноды обходятся, упавший или зависший sing-box поднимает сторож.

> **Проверено** сборкой с нуля 2026-10-04: OpenWrt 25.12.2 в виртуальной машине (arm64, 256 MB RAM),
> одна подписка — всё по этой инструкции. Работают: VPN для самого роутера и для устройств в LAN, раздельные
> маршруты, сторож (поднял остановленный sing-box за 52 с), подъём после перезагрузки (~25 с).
> Не проверялось в тестовой сборке: Tailscale (шаг 8) и две подписки сразу (у автора на живом роутере работает).

Как читать:
- блоки кода с `ssh router …` или с пометкой «на компьютере» выполняются **на компьютере**, остальные — **на роутере**
  (зайти на роутер: `ssh router`, выйти: `exit`);
- `ПОДСТАВЬ_…` — место, куда нужно вписать своё;
- пути `mirror/…` — внутри этого репозитория, пути `/etc/…`, `/usr/…` — на роутере.

Сколько займёт: около часа, если OpenWrt уже стоит.

---

## 0. Что нужно заранее

**Роутер с OpenWrt 25.x.** Схема работает на Xiaomi AX3000T (arm64, 256 MB памяти). Требования:
- оперативной памяти от **256 MB** (на 128 MB sing-box будет падать);
- свободного места на флеше от **~80 MB** (sing-box — 44 MB, python3 — ~15 MB, Tailscale — 26 MB, если нужен).
  Проверить: `df -h /overlay`, колонка Available.

Как поставить OpenWrt на конкретный роутер — у каждой модели свой способ. Ищи модель в
[таблице устройств OpenWrt](https://openwrt.org/toh/start), а образ бери в
[Firmware Selector](https://firmware-selector.openwrt.org/). Эта инструкция начинается с уже прошитого роутера.
На OpenWrt 24.x и старше всё то же, только пакеты ставятся через `opkg install`, а не `apk add`.

**Подписка VLESS + Reality**: ссылка, по которой отдаётся список строк `vless://…`, открытым текстом или в
base64. Её даёт VPN-провайдер — обычно в личном кабинете или в боте, пункт вроде «ссылка для v2ray / sing-box /
Hiddify». Скрипты рассчитаны на две подписки (`P1` — основная, `P2` — запасная), но работают и с одной (шаг 4).

**Компьютер с `ssh` и `scp`.** На macOS и Linux они уже есть. На Windows 10/11 тоже есть в PowerShell (`ssh`, `scp`
и `tar` входят в систему), но длинные команды удобнее выполнять в WSL.

**Кабель.** Пару раз DNS и сеть на роутере перезапускаются — по Wi-Fi можно потерять связь.

## 1. Первый вход и подготовка

Новый OpenWrt отвечает по адресу `192.168.1.1`, у root пароля нет. На компьютере, подключённом кабелем к LAN:

```sh
ssh root@192.168.1.1
passwd                 # задай пароль root — без него любой в сети может зайти
```

**Подсеть LAN.** Если роутер стоит за другим роутером (например, роутером провайдера), у них не должна совпадать
подсеть. Узнать подсеть верхнего роутера: `ip route show default` → после `via` его адрес. Если он тоже
`192.168.1.x`, переведи LAN нашего роутера, например, на `192.168.10.1`:

```sh
uci del network.lan.ipaddr
uci add_list network.lan.ipaddr='192.168.10.1/24'    # в OpenWrt 25 ipaddr — список; в 24 и старше: uci set network.lan.ipaddr='192.168.10.1'
uci commit network
reboot                                              # после перезагрузки роутер — 192.168.10.1, компьютер получит новый адрес сам
```

**Вход по ключу** (дальше удобнее без пароля). На компьютере:

```sh
# на компьютере
ssh-keygen -t ed25519 -f ~/.ssh/router          # Enter на все вопросы
cat ~/.ssh/router.pub | ssh root@192.168.10.1 'cat >> /etc/dropbear/authorized_keys; chmod 600 /etc/dropbear/authorized_keys'
```

И короткое имя `router` — допиши в `~/.ssh/config` на компьютере:

```
Host router
    HostName 192.168.10.1
    User root
    IdentityFile ~/.ssh/router
```

Проверка: `ssh router` заходит без пароля.

**Бэкап до изменений**: LuCI (`http://192.168.10.1`) → System → Backup / Flash firmware → Generate archive.

## 2. Пакеты

Все команды — на роутере (`ssh router`).

```sh
apk update                    # скачать список пакетов (без этого apk add ничего не найдёт)
df -h /overlay                # сколько места; нужно ~80 MB свободных
```

Основное:

```sh
apk add sing-box python3 jq curl ca-bundle kmod-tun kmod-nft-offload https-dns-proxy zram-swap ip-full
```

| Пакет | Зачем |
|---|---|
| `sing-box` | сам VPN-клиент: туннель `tun0`, правила маршрутизации, DNS |
| `python3` | на нём `update-vless.py` (разбор подписок), `sb-ping`, `router-report` |
| `jq`, `curl`, `ca-bundle` | разбор JSON и запросы к API sing-box в шелл-скриптах; HTTPS-сертификаты |
| `kmod-tun` | модуль ядра для туннельного интерфейса `tun0` |
| `kmod-nft-offload` | программное ускорение пересылки (flow offloading) — ~1.5× скорости на слабом CPU |
| `https-dns-proxy` | запасной DNS-over-HTTPS на `127.0.0.1:5053`, если sing-box не отвечает |
| `zram-swap` | сжатый своп в памяти — запас при всплесках |
| `ip-full` | полноценная утилита `ip` (во встроенной из busybox нет части команд для разбора проблем) |

**dnsmasq → dnsmasq-full.** Стандартный `dnsmasq` урезан; полный нужен для правил DNS, которые использует схема.
Пока один удалён, а второй не поставлен, у роутера нет DNS, поэтому делай одной строкой:

```sh
apk del dnsmasq && apk add dnsmasq-full
nslookup openwrt.org 127.0.0.1            # проверка: должен вернуться адрес
```

Если `apk add` после удаления не может скачать пакет (не находит сервер), временно дай роутеру внешний DNS и повтори:
`echo 'nameserver 1.1.1.1' > /tmp/resolv.conf && apk add dnsmasq-full`.

По желанию:

```sh
apk add tailscale        # удалённый доступ к роутеру откуда угодно (шаг 8)
apk add wakeonlan        # будить компьютер в сети по MAC
```

**Закрепить версии.** Скрипты и конфиг написаны под **sing-box 1.13**. Чтобы случайный `apk upgrade` (в том числе
из LuCI) не обновил его до несовместимой версии:

```sh
sing-box version | head -1                                    # убедись, что 1.13.x
sed -i -e 's/^sing-box$/sing-box~1.13/' -e 's/^tailscale$/tailscale~1.98/' /etc/apk/world
grep -E '^(sing-box|tailscale)' /etc/apk/world                # должно быть sing-box~1.13 (и tailscale~1.98)
```

`~1.13` значит «любая 1.13.x». Переход на 1.14 — вручную, по [runbook.md](runbook.md), раздел «Обновить sing-box».

## 3. Скрипты и конфиги из репозитория

**Скачай репозиторий** на компьютер: кнопка Code → Download ZIP на GitHub (и распакуй) или
`git clone https://github.com/nkkalmyk/openwrt-singbox-router.git`. Дальше команды выполняются из его корня.

**Подставь подписку до копирования** — проще, чем править файл на роутере. Открой в любом редакторе
`mirror/usr/bin/update-vless.py` и найди в начале:

```python
SUB1_URL = "https://subscription.example/CHANGE_ME"   # ← ссылка основной подписки (ноды получат теги P1-…)
SUB2_URL = "https://subscription.example/CHANGE_ME"   # ← запасной (P2-…)
```

Что ещё там настраивается — в шаге 4. Теперь копируй (на компьютере, из корня репозитория):

```sh
# на компьютере
COPYFILE_DISABLE=1 tar --uid 0 --gid 0 --uname root --gname root -C mirror -cf - \
    usr etc/init.d/sing-box etc/config/sing-box etc/config/https-dns-proxy etc/sing-box \
    etc/init.d/router-bot etc/sysctl.conf etc/sysupgrade.conf etc/crontabs/root \
  | ssh router 'tar -xf - -C /'
ssh router 'chmod 755 /usr/bin/vpn /usr/bin/sb-* /usr/bin/*.sh /usr/bin/*.py /usr/bin/mem /usr/bin/router-report /usr/bin/router-notify /usr/bin/router-tg /usr/bin/router-day /usr/bin/uptime-hourly /etc/init.d/sing-box /etc/init.d/router-bot'
```

- `--uid 0 --gid 0 --uname root --gname root` — без них файлы и даже каталоги `/usr`, `/usr/bin` на роутере станут
  принадлежать пользователю компьютера (uid 501 и т.п.);
- `COPYFILE_DISABLE=1` — чтобы macOS не подложила служебные файлы `._*`.

Что куда попало:

| Из репозитория | На роутер | Зачем |
|---|---|---|
| `mirror/usr/lib/sb-common.sh` | `/usr/lib/sb-common.sh` | общая библиотека: проверки, блокировка, журнал |
| `mirror/usr/lib/sb_ui.py` | `/usr/lib/sb_ui.py` | общий вид вывода: имена нод без флагов, цвета в терминале |
| `mirror/usr/bin/*` | `/usr/bin/` | `vpn`, `sb-ping`, `update-vless.py`, `apply-vless.sh`, сторожа, отчёт, бэкап |
| `mirror/etc/init.d/sing-box` | `/etc/init.d/sing-box` | служба: лимиты памяти, безопасный перезапуск при подъёме WAN |
| `mirror/etc/init.d/router-bot` | `/etc/init.d/router-bot` | бот команд в Telegram (по желанию, шаг 7) |
| `mirror/etc/config/sing-box` | `/etc/config/sing-box` | включает службу, запуск от root (нужно для TUN) |
| `mirror/etc/config/https-dns-proxy` | `/etc/config/https-dns-proxy` | запасной DoH на 5053, запрет чужих DNS из LAN |
| `mirror/etc/sing-box/config.json` | `/etc/sing-box/config.json` | конфиг sing-box — с примерными нодами, доводится в шаге 4 |
| `mirror/etc/sing-box/rule-set/my-*.json` | `/etc/sing-box/rule-set/` | ручные списки «всегда напрямую / всегда через VPN» (пустые) |
| `mirror/etc/sysctl.conf` | `/etc/sysctl.conf` | conntrack: больше записей, короче таймауты |
| `mirror/etc/sysupgrade.conf` | `/etc/sysupgrade.conf` | всё это переживёт обновление прошивки |
| `mirror/etc/crontabs/root` | `/etc/crontabs/root` | расписание автоматики (шаг 7) |

**Не копируй** `network`, `firewall`, `dhcp`, `system` из `mirror/etc/config/` — там настройки конкретного железа.
Нужное делается командами в шаге 5.

Если нужен Tailscale — ещё два файла: `scp mirror/etc/init.d/tailscale router:/etc/init.d/` и
`scp mirror/etc/config/tailscale router:/etc/config/` (на компьютере).

## 4. Своё в настройках

### Интерфейс WAN

Скрипты ждут, что интерфейс WAN в OpenWrt называется `wan` (так по умолчанию). Само устройство может быть любым —
`wan`, `eth1`, `pppoe-wan`: скрипты спрашивают его у системы. Проверка:

```sh
ubus call network.interface.wan status | jsonfilter -e '@.l3_device'    # напечатает имя устройства, например eth1
```

### Подписка: какие ноды брать

Посмотри, как называются ноды в твоей подписке (это то, что после `#` в строках `vless://`):

```sh
cat > /tmp/names.py <<'EOF'
import base64, sys, urllib.parse
d = sys.stdin.read().strip()
if "vless://" not in d:
    d = base64.b64decode(d + "=" * (-len(d) % 4)).decode("utf-8", "replace")
for line in d.splitlines():
    if "#" in line:
        print(urllib.parse.unquote(line.split("#", 1)[1]))
EOF
curl -s 'ПОДСТАВЬ_ССЫЛКУ_ПОДПИСКИ' | python3 /tmp/names.py
```

Ничего не напечаталось — провайдер не отдаёт подписку без особого клиента. Попробуй
`curl -s -A 'Mozilla/5.0' 'ССЫЛКА'` или спроси у провайдера ссылку «для sing-box / v2ray».

Теперь в `/usr/bin/update-vless.py` (на роутере: `vi /usr/bin/update-vless.py`; в vi: `i` — правка, `Esc` и
`:wq` — сохранить и выйти) настрой списки слов. Регистр не важен, сравнивается кусок названия:

```python
SUB1_EXCLUDE = ["россия", "russia"]    # ноды с этими словами в названии НЕ берём (например, российские)
SUB1_EU_KEYWORDS = [...]               # берём ТОЛЬКО ноды с этими словами (сейчас — страны ЕС); None — брать все
SUB2_EXCLUDE = ["lte", "gaming"]       # то же для запасной подписки
```

Ноды с транспортом не TCP (`type=ws`, `grpc`…) скрипт пропускает сам и пишет «пропущено N нод с неподдерживаемым
транспортом» — это нормально.

### Если подписка одна

Скрипты по умолчанию ждут две. С одной — три правки.

1. В `/usr/bin/update-vless.py` найди строку `PREFIXES = ("P1", "P2")` и замени на:
   ```python
   PREFIXES = ("P1",)
   ```
2. Там же, ближе к концу, цикл по подпискам. Было:
   ```python
       for prefix, fetch, url, exclude_kw, require_kw in (
               ("P1", fetch_plain, SUB1_URL, SUB1_EXCLUDE, SUB1_EU_KEYWORDS),
               ("P2", fetch_with_cookies, SUB2_URL, SUB2_EXCLUDE, None)):
   ```
   Стало (строку `P2` убрать, а скобки закрыть на отдельной строке — иначе синтаксическая ошибка):
   ```python
       for prefix, fetch, url, exclude_kw, require_kw in (
               ("P1", fetch_plain, SUB1_URL, SUB1_EXCLUDE, SUB1_EU_KEYWORDS),
       ):
   ```
   Проверка: `python3 -m py_compile /usr/bin/update-vless.py` — тишина значит, что всё верно.
3. Убери из конфига примерные ноды `P2-…` (с одной подпиской скрипт их уже не заменит):
   ```sh
   cd /etc/sing-box
   jq '(.outbounds |= map(select((.tag // "") | startswith("P2-") | not)))
     | (.outbounds[] | select(.outbounds) | .outbounds) |= map(select(startswith("P2-") | not))' \
     config.json > /tmp/c.json && mv /tmp/c.json config.json
   ```

Отчёт `vpn report` будет писать «живы только 0 P2-нод» — с одной подпиской это не ошибка, не обращай внимания.

### Если подписок две

Вторую подписку скрипт качает с cookies (`fetch_with_cookies`): некоторым провайдерам так надо. Если с ней
не скачивается — замени в строке `("P2", fetch_with_cookies, …)` на `fetch_plain`.

### Группы для Gemini

У автора Gemini открывается только через P1, поэтому в конфиге есть группы `gemini-auto` и `gemini-select` и правило
для доменов Gemini. С одной подпиской они просто повторяют основную группу — можно оставить. Убрать совсем:

```sh
cd /etc/sing-box
jq '(.outbounds |= map(select(.tag != "gemini-auto" and .tag != "gemini-select")))
  | (.route.rules |= map(select(.outbound != "gemini-select")))' config.json > /tmp/c.json && mv /tmp/c.json config.json
```
и в `/usr/bin/update-vless.py` замени строку с `PREFIX_GROUPS` на `PREFIX_GROUPS = {}`.

### Секрет API и чистка примеров

К sing-box обращаются `sb-ping`, сторож и отчёт — через его API с паролем («секретом»). Создаём секрет и
вписываем в конфиг, заодно убираем два правила-примера (с адресами `203.0.113.x` и `nodes.example.com`) —
правила для настоящих нод скрипт вставит сам:

```sh
tr -dc A-Za-z0-9 < /dev/urandom | head -c 32 > /etc/sing-box/.clash-secret
chmod 600 /etc/sing-box/.clash-secret
cd /etc/sing-box
jq --arg s "$(cat .clash-secret)" '.experimental.clash_api.secret = $s
  | .route.rules |= map(select(((.ip_cidr // []) | any(startswith("203.0.113."))) | not)
                       | select(((.domain_suffix // []) | index("nodes.example.com")) | not))' \
  config.json > /tmp/c.json && mv /tmp/c.json config.json
```

## 5. Сеть, firewall, DNS

**Туннель и зона firewall.** sing-box сам создаёт интерфейс `tun0`. Описываем его в OpenWrt, чтобы дать ему зону
firewall `vpn` и разрешить пересылку LAN → VPN:

```sh
uci set network.vlesstun=interface
uci set network.vlesstun.proto='none'
uci set network.vlesstun.device='tun0'
uci commit network

uci set firewall.vpn=zone
uci set firewall.vpn.name='vpn'
uci set firewall.vpn.input='ACCEPT'
uci set firewall.vpn.output='ACCEPT'
uci set firewall.vpn.forward='ACCEPT'
uci set firewall.vpn.masq='1'
uci set firewall.vpn.mtu_fix='1'
uci add_list firewall.vpn.network='vlesstun'
uci set firewall.lan_vpn=forwarding
uci set firewall.lan_vpn.src='lan'
uci set firewall.lan_vpn.dest='vpn'
uci set firewall.vpn_wan=forwarding
uci set firewall.vpn_wan.src='vpn'
uci set firewall.vpn_wan.dest='wan'
uci set firewall.@defaults[0].flow_offloading='1'
uci commit firewall
```

**IPv6 выключить.** Туннель в этой схеме только IPv4. Если провайдер даёт IPv6, устройства пойдут по нему **мимо VPN**.
Поэтому IPv6 выключаем и на WAN, и в LAN (у автора так же):

```sh
uci set network.wan6.disabled='1'
uci set dhcp.lan.dhcpv6='disabled'
uci set dhcp.lan.ra='disabled'
uci commit network; uci commit dhcp
```

**DNS.** dnsmasq (DNS-сервер для устройств в сети) спрашивает по порядку: sing-box (раздельный DNS) → запасной DoH →
DNS роутера выше. Адрес роутера выше: `ip route show default` → после `via` (если WAN подключён напрямую к провайдеру —
это шлюз провайдера; подойдёт и он).

```sh
uci set dhcp.@dnsmasq[0].noresolv='1'
uci set dhcp.@dnsmasq[0].strictorder='1'
uci set dhcp.@dnsmasq[0].cachesize='1000'
uci -q delete dhcp.@dnsmasq[0].server
uci add_list dhcp.@dnsmasq[0].server='/mask.icloud.com/'          # iCloud Private Relay не обходит DNS роутера
uci add_list dhcp.@dnsmasq[0].server='/mask-h2.icloud.com/'
uci add_list dhcp.@dnsmasq[0].server='/use-application-dns.net/'  # Firefox не включает свой DoH
uci add_list dhcp.@dnsmasq[0].server='127.0.0.1#5300'             # 1) sing-box
uci add_list dhcp.@dnsmasq[0].server='127.0.0.1#5053'             # 2) https-dns-proxy
uci add_list dhcp.@dnsmasq[0].server='ПОДСТАВЬ_АДРЕС_РОУТЕРА_ВЫШЕ' # 3) последний резерв
uci commit dhcp
```

`https-dns-proxy` (его конфиг скопирован в шаге 3) слушает 5053 и **запрещает** устройствам в LAN DNS-запросы к чужим
серверам (порты 53 и 853): устройство с «зашитым» 8.8.8.8 получит отказ и спросит роутер.

**zram:** `uci set system.@system[0].zram_size_mb='150'; uci commit system`.

**Применить:**

```sh
/etc/init.d/network reload; fw4 reload; /etc/init.d/odhcpd restart
/etc/init.d/dnsmasq restart; /etc/init.d/https-dns-proxy restart; sysctl -p
nslookup ya.ru 127.0.0.1         # DNS работает (пока через запасной DoH — sing-box ещё не запущен)
```

## 6. Первый запуск

Порядок важен: сначала настоящие ноды (с примерными конфиг невалиден), потом базы маршрутизации, потом запуск.

```sh
python3 /usr/bin/update-vless.py; echo "код $?"
```
Ждёшь «P1-подписка (…): N нод» и «код 0». `1` — ошибка: текст выше скажет, что не так (подписка не скачалась,
ни одна нода не подошла под фильтры).

Базы «что считать российским» (geoip-ru и geosite-category-ru от SagerNet). Первый раз — напрямую, дальше их раз
в неделю обновляет `update-rulesets.sh`:

```sh
cd /etc/sing-box/rule-set
curl -fLo geoip-ru.srs https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-ru.srs
curl -fLo geosite-category-ru.srs https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-category-ru.srs
ls -la                           # оба файла не пустые (десятки КБ)
```

Проверка конфига и запуск:

```sh
sing-box check -c /etc/sing-box/config.json && echo OK    # должно быть просто OK, без FATAL
/etc/init.d/sing-box enable                                # запускать при загрузке
/usr/bin/apply-vless.sh --force                            # запуск + проверка туннеля + «этот конфиг рабочий»
```
Ждёшь в конце **«Применено, связь есть.»**

Проверь, что всё на месте:

```sh
vpn                      # сводка: Интернет провайдера OK, VPN OK — выход в <страна> через <нода>, Маршрутизация OK
vpn site ya.ru           # → НАПРЯМУЮ
vpn site google.com      # → VPN
vpn nodes                # ноды с задержками
```
`vpn site` иногда пишет «соединение не поймано» (короткое соединение не успело попасть в журнал) — повтори.

VPN точно работает, если адрес через туннель и напрямую разный:

```sh
W=$(ubus call network.interface.wan status | jsonfilter -e '@.l3_device')
curl -s https://1.1.1.1/cdn-cgi/trace | grep -E '^(ip|loc)='                 # через VPN
curl -s --interface "$W" https://1.1.1.1/cdn-cgi/trace | grep -E '^(ip|loc)='  # напрямую
```

С компьютера в сети роутера: сайты открываются, [2ip.ru](https://2ip.ru) показывает домашний IP, а
[ifconfig.me](https://ifconfig.me) — IP VPN-ноды. DNS-правила можно проверить
[router-dnstest.py](../scripts/router-dnstest.py): зарубежные домены не должны уходить в `dns-ru`.

**Перезапуск sing-box — только `vpn restart` (или `/usr/bin/sb-restart.sh`), никогда `/etc/init.d/sing-box restart`.**
Быстрый перезапуск через init.d однажды оставил LAN без интернета; `sb-restart.sh` останавливает, ждёт, проверяет
конфиг и после запуска убеждается, что туннель и правила маршрутизации на месте.

## 7. Автоматика

Расписание уже скопировано в `/etc/crontabs/root`:

| Когда | Что |
|---|---|
| `7,37 * * * *` | `apply-vless.sh` — обновить подписки; перезапуск только если сменились серверы |
| `* * * * *` | `sb-watchdog.sh` — сторож sing-box |
| `*/5 * * * *` | `health.sh` — строка состояния в `/tmp/health.log` |
| `58 * * * *` | `uptime-hourly` — почасовая сводка сбоев в `/root/uptime.log` (для `vpn day`) |
| `45 7,19 * * *` | `router-report --save` — отчёт о здоровье в `/root/reports.log` (`vpn report`) |
| `0 8 * * 0` | `update-rulesets.sh` — базы geoip/geosite |
| `30 8 * * 0` | `router-backup.sh` — полный бэкап в `/root/backups` (хранит 2) |
| `* * * * *` | `router-notify` — уведомления в Telegram (пока не подключены — сразу выходит) |
| `*/2 * * * *` | `ts-watchdog.sh` — сторож Tailscale |

Без Tailscale убери его строку: `sed -i '/ts-watchdog/d' /etc/crontabs/root`.

Cron работает по местному времени роутера, а у чистого OpenWrt это UTC. Расписание рассчитано на московское время:
отчёт в 07:45 и 19:45, бэкап и базы в воскресенье утром. Поставь свой часовой пояс, иначе задачи сдвинутся:

```sh
uci set system.@system[0].zonename='Europe/Moscow'   # свой: Europe/Berlin, Asia/Almaty…
uci set system.@system[0].timezone='MSK-3'           # строка TZ для этого пояса (видна в LuCI → System → Timezone)
uci commit system; /etc/init.d/system reload
```

```sh
/etc/init.d/cron enable; /etc/init.d/cron restart
```

Проверка, что сторож живой: останови sing-box `/etc/init.d/sing-box stop` (именно stop, procd тогда не перезапускает
сам) и подожди минуту — `vpn log` покажет «sing-box не запущен — поднимаю … готово».

### Telegram: уведомления и команды (по желанию)

```sh
/etc/init.d/router-bot enable; /etc/init.d/router-bot start   # служба команд; без настройки просто ждёт
vpn tg setup                                                   # токен от @BotFather → «Старт» в боте → Enter
```
Придёт «✅ Роутер подключён» со списком команд. Попробуй `/status`. Что бот присылает, какие команды понимает
и как защищён — [telegram.md](telegram.md).

Все скрипты делят одну блокировку `/tmp/apply-vless.lock` и не мешают друг другу. Что они делают и когда что-то
перезапускают — [architecture.md](architecture.md), разделы «Подписки и автоматика» и «Что будет, если…».

## 8. Tailscale (по желанию, в тестовой сборке не проверялся)

Удалённый доступ к роутеру откуда угодно и возможность ходить в интернет «через дом» (exit node).

```sh
/etc/init.d/tailscale enable; /etc/init.d/tailscale start
tailscale up --accept-dns=false --advertise-exit-node   # напечатает ссылку — открой, войди в Tailscale; один раз
```

Firewall: своя зона для `tailscale0`, вход Tailscale с WAN и форварды:

```sh
uci set firewall.tailscale=zone
uci set firewall.tailscale.name='tailscale'
uci set firewall.tailscale.input='ACCEPT'
uci set firewall.tailscale.output='ACCEPT'
uci set firewall.tailscale.forward='ACCEPT'
uci set firewall.tailscale.mtu_fix='1'
uci add_list firewall.tailscale.device='tailscale0'
for pair in tailscale:lan tailscale:wan tailscale:vpn vpn:tailscale; do
    s=${pair%%:*} d=${pair##*:}
    uci set firewall.fw_${s}_${d}=forwarding
    uci set firewall.fw_${s}_${d}.src="$s"
    uci set firewall.fw_${s}_${d}.dest="$d"
done
uci set firewall.ts_in=rule
uci set firewall.ts_in.name='Allow-Tailscale-In'
uci set firewall.ts_in.src='wan'
uci set firewall.ts_in.proto='udp'
uci set firewall.ts_in.dest_port='41641'
uci set firewall.ts_in.target='ACCEPT'
uci commit firewall; fw4 reload
```

Трафик самого Tailscale sing-box не трогает (Tailscale метит его и отправляет мимо туннеля), поэтому удалённый доступ
переживает падения sing-box. Настройки потом менять только `tailscale set …`; `tailscale up --reset` и перезапуски при
загрузке не нужны. Exit node заработает после одобрения в админке Tailscale: Machines → роутер → Edit route settings.

## 9. Если ты не в России

Схема заточена под «российское — напрямую». Под другую страну поменяй:

- в `config.json`: правило с доменами `ru`, `su`, `xn--p1ai` (.рф) и rule-set `geoip-ru` / `geosite-category-ru` →
  свои (`geoip-xx`, `geosite-category-xx` из [sing-geoip](https://github.com/SagerNet/sing-geoip) /
  [sing-geosite](https://github.com/SagerNet/sing-geosite)); те же имена — в `update-rulesets.sh` и в шаге 6;
- DNS для своих доменов: сервер `dns-ru` (77.88.8.8, Яндекс) → DNS своего провайдера или страны; этот же адрес —
  `DIRECT_DNS` в `update-vless.py`;
- в `sb-common.sh`: `direct_ok` пингует 77.88.8.8 / 77.88.8.1 / 8.8.8.8; `tunnel_ok` считает VPN рабочим, если
  Cloudflare видит страну не `RU` — впиши код своей страны.

## Если не заработало

Сначала `vpn` — он показывает те же проверки, что и автоматика. Потом по строке, которая не OK:

| Что видно | Что проверить |
|---|---|
| «Интернет провайдера» не OK, хотя сайты напрямую открываются | `ubus call network.interface.wan status \| jsonfilter -e '@.l3_device'` — интерфейс должен называться `wan` (шаг 4) |
| `update-vless.py`: ошибка или 0 нод | `curl -s ССЫЛКА \| python3 /tmp/names.py` (шаг 4) — отдаётся ли список; не отсеяли ли все ноды списки слов; транспорт нод — только TCP |
| `SyntaxError` при запуске `update-vless.py` | правка «одна подписка» — скобки (шаг 4); `python3 -m py_compile /usr/bin/update-vless.py` покажет строку |
| `sing-box check`: `invalid public_key` | остались примерные ноды: не запущен `update-vless.py` или (с одной подпиской) не удалены `P2-…` (шаг 4) |
| `sing-box check`: другая ошибка | текст называет поле: оставшийся `CHANGE_ME`, пустая группа, нет файла rule-set (шаг 6) |
| `apply-vless.sh` откатился («связи нет») | `logread -e sing-box \| tail -20`; `vpn nodes` — живы ли ноды. Мертвы все — проблема подписки или провайдера |
| VPN OK, у устройств в LAN нет интернета | `nft list flowtable inet fw4 ft` — есть ли там `tun0` (нет — `fw4 reload`); зона `vpn` и форвард `lan → vpn` (шаг 5) |
| устройства ходят мимо VPN | выключен ли IPv6 (шаг 5); `vpn site сайт` — по какому правилу идёт |
| сайты не открываются по имени, по IP открываются | `nslookup ya.ru 127.0.0.1` и `nslookup ya.ru 127.0.0.1 -port=5300`; серверы dnsmasq (шаг 5) |
| всё через VPN, даже российское | `ls -la /etc/sing-box/rule-set/` — есть ли `geoip-ru.srs` и `geosite-category-ru.srs` (шаг 6) |
| что делала автоматика | `vpn log` (постоянный журнал), `vpn report 24` |

## Частые грабли

- **Около :07 и :37** работает `apply-vless.sh`. Ручные тяжёлые действия в это время будут ждать общую блокировку.
- **busybox**: на роутере нет `base64`, `timeout`, `nohup`, `diff`, `flock -w`. Долгие команды по ssh запускай
  отвязанно, чтобы обрыв связи их не убил: `setsid sh -c '/usr/bin/apply-vless.sh --force > /tmp/apply.log 2>&1' &`.
- **Память**: смотри `vpn mem`. «Своя» память sing-box (RssAnon) — 10–20 MB. Большая цифра RSS — в основном код
  программы, он не страшен. Лимиты Go (`GOMEMLIMIT`) — в `/etc/init.d/sing-box` и `/etc/init.d/tailscale`.
- **Ручная правка `config.json`** — на копии, `sing-box check -c копия`, потом на место и `apply-vless.sh --force`.
  При невалидном конфиге автоматика вернёт последний рабочий (`config.json.good`).
- **Перепрошивка**: всё из `sysupgrade.conf` сохранится, пакеты — нет. После сброса повтори шаг 2.

Рецепты на каждый день — [runbook.md](runbook.md).
