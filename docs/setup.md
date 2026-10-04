# Как собрать такой же роутер самому

Пошаговая инструкция: из чистого OpenWrt получить ту же схему, что описана в [architecture.md](architecture.md):
российское — напрямую, остальное — через VPN (VLESS + Reality), раздельный DNS, автообновление подписок
и сторожа, которые чинят сбои сами.

Пути в таблицах — относительно корня репозитория (`mirror/…`) и на роутере (`/etc/…`, `/usr/bin/…`).
Команды с `ssh router` выполняются с компьютера, остальные — на самом роутере.

## 0. Что нужно

- **Роутер на OpenWrt 25.x** (пакеты через `apk`). Схема проверена на Xiaomi AX3000T (arm64, 256 MB RAM).
  Меньше 256 MB RAM не советую. На флеше нужно ~80 MB свободного места: sing-box ~44 MB, tailscale ~26 MB, python3.
  На OpenWrt 24.x и старше всё то же, только вместо `apk add` — `opkg install`, а закрепление версий другое.
- **Подписка на VLESS + Reality** (ссылка, по которой отдаётся список `vless://…`, обычный или в base64).
  Скрипты рассчитаны на **две** подписки от разных провайдеров: `P1` — основная, `P2` — запасная.
- **Компьютер с ssh и scp.** В примерах роутер назван `router` — заведи такой хост в `~/.ssh/config`.
- **LAN роутера в другой подсети, чем сеть выше.** Если роутер стоит за роутером провайдера, у них не должно
  быть одинаковой подсети, например `UPSTREAM_NET/24` и там и там. Поменяй LAN в LuCI → Network → Interfaces → lan.

Перед началом сделай бэкап: LuCI → System → Backup/Flash firmware → Generate archive.

## 1. Пакеты

```sh
apk update
apk add sing-box python3 jq curl ca-bundle kmod-tun kmod-nft-offload https-dns-proxy zram-swap
# dnsmasq-full вместо dnsmasq: нужен для DNS-перенаправлений. Замена на секунды оставит без DNS — делай с кабеля.
apk del dnsmasq && apk add dnsmasq-full
# по желанию: удалённый доступ и пробуждение ПК по сети
apk add tailscale wakeonlan
```

Закрепи мажорные версии, чтобы случайный `apk upgrade` не обновил sing-box мимо проверки конфига: в
`/etc/apk/world` замени строки `sing-box` и `tailscale` на `sing-box~1.13` и `tailscale~1.98` (подставь свои версии,
`sing-box version`). Скрипты и конфиг написаны под **sing-box 1.13**; на 1.12 конфиг тоже работает.

## 2. Файлы из репозитория

Скопируй на роутер без изменений:

| Из репозитория | На роутер | Зачем |
|---|---|---|
| `mirror/usr/lib/sb-common.sh` | `/usr/lib/sb-common.sh` | общая библиотека всех скриптов |
| `mirror/usr/bin/*` | `/usr/bin/` | скрипты: `vpn`, `sb-ping`, `update-vless.py`, `apply-vless.sh`, сторожа и т.д. |
| `mirror/etc/init.d/sing-box` | `/etc/init.d/sing-box` | служба с лимитами памяти и безопасным перезапуском при подъёме WAN |
| `mirror/etc/config/sing-box` | `/etc/config/sing-box` | включает службу, запуск от root (нужно для TUN) |
| `mirror/etc/config/https-dns-proxy` | `/etc/config/https-dns-proxy` | запасной DoH на `127.0.0.1:5053` |
| `mirror/etc/sing-box/config.json` | `/etc/sing-box/config.json` | конфиг sing-box (нужно подставить своё — шаг 3) |
| `mirror/etc/sing-box/rule-set/my-*.json` | `/etc/sing-box/rule-set/` | ручные списки «всегда напрямую / всегда через VPN» (пустые) |
| `mirror/etc/sysctl.conf` | `/etc/sysctl.conf` | conntrack: больше записей, короче таймауты |
| `mirror/etc/sysupgrade.conf` | `/etc/sysupgrade.conf` | чтобы всё это пережило обновление прошивки |
| `mirror/etc/crontabs/root` | `/etc/crontabs/root` | расписание (шаг 6) |
| `mirror/etc/init.d/tailscale`, `mirror/etc/config/tailscale` | `/etc/init.d/`, `/etc/config/` | только если ставишь Tailscale |

```sh
# с компьютера, из корня репозитория. COPYFILE_DISABLE — чтобы macOS не подложила файлы ._*
COPYFILE_DISABLE=1 tar -C mirror -cf - usr etc/init.d/sing-box etc/config/sing-box etc/config/https-dns-proxy \
    etc/sing-box etc/sysctl.conf etc/sysupgrade.conf | ssh router 'tar -xf - -C /'
ssh router 'chmod 755 /usr/bin/vpn /usr/bin/sb-* /usr/bin/*.sh /usr/bin/*.py /usr/bin/mem /usr/bin/router-report /etc/init.d/sing-box'
```

Не копируй целиком `network`, `firewall`, `dhcp` и `system` из `mirror/etc/config/`: там настройки конкретного
железа. Нужные части — командами в шаге 4.

## 3. Подставить своё

**Подписки** — в начале `/usr/bin/update-vless.py`:

```python
SUB1_URL = "https://…"        # основная подписка → ноды получат теги P1-…
SUB2_URL = "https://…"        # запасная → P2-…
SUB1_EXCLUDE = ["россия", "russia", "украин", "ukraine"]          # ноды, в названии которых есть эти слова, не берутся
SUB1_EU_KEYWORDS = [...]      # для P1 берутся только ноды с этими словами в названии (страны ЕС); None — все
SUB2_EXCLUDE = ["lte", "gaming", "free", "россия", "russia"]
PREFIX_GROUPS = {"gemini-auto": "P1-", "gemini-select": "P1-"}   # группы только из нод P1 (см. ниже)
```

Названия у нод (фрагмент после `#` в ссылке `vless://`) — у каждого провайдера свои: посмотри, что отдаёт
подписка (`curl -s ССЫЛКА | base64 -d | head`), и поправь списки слов. Вторую подписку скрипт качает с cookies
(`fetch_with_cookies`): некоторым провайдерам так надо. Обычной хватит `fetch_plain` — это в цикле ближе к концу
файла, где перечислены `("P1", …)` и `("P2", …)`.

Подписка только одна? Убери из этого цикла строку `("P2", …)` и `"P2"` из `PREFIXES`. Но учти: сторож и проверки в
`sb-common.sh` (`providers_ok`, п. 3 в `sb-watchdog.sh`) рассчитаны на двух провайдеров, их придётся поправить.

**Группы для Gemini.** У автора Gemini работает только через P1, поэтому в конфиге есть отдельные группы
`gemini-auto` / `gemini-select` и правило для доменов Gemini. Не нужно — удали из `config.json` это правило и обе
группы, а из `PREFIX_GROUPS` — обе строки.

**Секрет Clash API.** Им пользуются `sb-ping`, сторож и отчёт:

```sh
head -c 18 /dev/urandom | base64 > /etc/sing-box/.clash-secret
chmod 600 /etc/sing-box/.clash-secret
```
Тот же секрет впиши в `config.json` вместо `CHANGE_ME` (`experimental.clash_api.secret`).

**Примерные ноды в `config.json`.** Ноды `P1-example-*` / `P2-example-*` `update-vless.py` заменит настоящими сам.
Только удали из `route.rules` два правила-примера: с адресами `203.0.113.x` и с `nodes.example.com`
(правила обхода для настоящих нод скрипт вставит сам).

**Базы маршрутизации** geoip-ru и geosite-category-ru:

```sh
/usr/bin/update-rulesets.sh      # скачает .srs в /etc/sing-box/rule-set/ (потом раз в неделю по cron)
```

## 4. Сеть, firewall, DNS

**Интерфейс для туннеля и зона firewall** (sing-box создаёт `tun0` сам; интерфейс нужен, чтобы дать ему зону):

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
uci set firewall.@defaults[0].flow_offloading='1'   # программный offloading: у автора ~1.5× скорости
uci commit firewall
```

**dnsmasq**: ходит только в sing-box, при его отказе — в запасной DoH, в последнюю очередь — в DNS выше по сети.

```sh
uci set dhcp.@dnsmasq[0].noresolv='1'
uci set dhcp.@dnsmasq[0].strictorder='1'
uci set dhcp.@dnsmasq[0].cachesize='1000'
uci -q delete dhcp.@dnsmasq[0].server
uci add_list dhcp.@dnsmasq[0].server='/mask.icloud.com/'          # iCloud Private Relay не обходит DNS роутера
uci add_list dhcp.@dnsmasq[0].server='/mask-h2.icloud.com/'
uci add_list dhcp.@dnsmasq[0].server='/use-application-dns.net/'  # Firefox не включает свой DoH
uci add_list dhcp.@dnsmasq[0].server='127.0.0.1#5300'             # 1) sing-box: раздельный DNS
uci add_list dhcp.@dnsmasq[0].server='127.0.0.1#5053'             # 2) https-dns-proxy: Cloudflare DoH
uci add_list dhcp.@dnsmasq[0].server='UPSTREAM_IP'                # 3) DNS роутера выше или провайдера — впиши свой
uci commit dhcp
```

`https-dns-proxy` из шага 2 уже настроен: слушает 5053, заворачивает DNS-запросы из LAN (порт 53) на роутер,
режет DoT (853) и **не** переписывает серверы dnsmasq (`dnsmasq_config_update '-'`).

**zram** (сжатый своп в памяти — запас на случай всплеска): `uci set system.@system[0].zram_size_mb='150'; uci commit system`.

Применить: `/etc/init.d/network reload; fw4 reload; /etc/init.d/dnsmasq restart; /etc/init.d/https-dns-proxy restart; sysctl -p`.

## 5. Первый запуск

```sh
python3 /usr/bin/update-vless.py; echo "код $?"   # 0 — ноды записаны в конфиг; 1 — ошибка (подписка, разбор)
sing-box check -c /etc/sing-box/config.json        # вывод должен быть пустым
/etc/init.d/sing-box enable
/usr/bin/apply-vless.sh --force                    # безопасный запуск + проверка туннеля + «конфиг рабочий»
vpn                                                # сводка: интернет, VPN (страна выхода), sing-box, события
```

Ждёшь в выводе `apply-vless.sh` строку «Применено, связь есть». Потом проверь маршрутизацию и DNS:

```sh
vpn site ya.ru          # → напрямую
vpn site google.com     # → VPN, через auto
vpn nodes               # ноды с задержками
```
и с компьютера — [router-dnstest.py](../scripts/router-dnstest.py): зарубежные домены не должны уходить в `dns-ru`.

**Перезапуск sing-box — всегда `/usr/bin/sb-restart.sh` или `vpn restart`, никогда `/etc/init.d/sing-box restart`.**
Быстрый перезапуск через init.d однажды оставил LAN без интернета; `sb-restart.sh` останавливает, ждёт,
проверяет конфиг и после запуска убеждается, что `tun0` и ip rules на месте.

## 6. Автоматика

Расписание из `mirror/etc/crontabs/root`:

| Когда | Что |
|---|---|
| `7,37 * * * *` | `apply-vless.sh` — обновить подписки, перезапуск только если сменились серверы |
| `* * * * *` | `sb-watchdog.sh` — сторож sing-box |
| `*/5 * * * *` | `health.sh` — строка состояния в `/tmp/health.log` |
| `45 4,16 * * *` | `router-report --save` — отчёт о здоровье в `/root/reports.log` |
| `0 5 * * 0` | `update-rulesets.sh` — базы geoip/geosite |
| `30 5 * * 0` | `router-backup.sh` — полный бэкап в `/root/backups` (хранит 2) |
| `*/2 * * * *` | `ts-watchdog.sh` — сторож Tailscale (без Tailscale эту строку убери) |

```sh
/etc/init.d/cron enable; /etc/init.d/cron restart
```

Все скрипты делят блокировку `/tmp/apply-vless.lock` и не мешают друг другу. Что они делают и когда что-то
перезапускают — [architecture.md](architecture.md), разделы «Подписки и автоматика» и «Что будет, если…».
Что уже случалось и как это чинилось — [history.md](history.md).

## 7. Tailscale (по желанию)

Нужен для доступа к роутеру откуда угодно и чтобы ходить через дом как через exit node.

```sh
# init.d и config из шага 2: свои лимиты памяти, --cleanup при старте и остановке
/etc/init.d/tailscale enable; /etc/init.d/tailscale start
tailscale up --accept-dns=false --advertise-exit-node   # откроет ссылку для входа; один раз
```

Зона firewall `tailscale` (интерфейс `tailscale0`) и форварды `tailscale → lan / wan / vpn`, `vpn → tailscale` —
см. конец `mirror/etc/config/firewall`, добавь так же через `uci`. Трафик самого tailscaled sing-box не трогает:
Tailscale метит его fwmark и отправляет мимо туннеля, поэтому удалённый доступ переживает падения sing-box.
Дальше настройки менять только `tailscale set …`; `tailscale up --reset` и перезапуски при загрузке не нужны.
Exit node заработает после одобрения в админке Tailscale (Machines → роутер → Edit route settings).

## 8. Если ты не в России

Схема заточена под «российское — напрямую». Под другую страну поменяй:

- `config.json`: правила `.ru/.su/.рф`, rule-set `geoip-ru` и `geosite-category-ru` → свои
  (`geoip-xx`, `geosite-category-xx` из [SagerNet](https://github.com/SagerNet/sing-geosite)), то же в `update-rulesets.sh`;
- DNS для своих доменов: `dns-ru` (77.88.8.8, Яндекс) → DNS своего провайдера или страны; этот же адрес в
  `update-vless.py` (`DIRECT_DNS`);
- `sb-common.sh`: `direct_ok` пингует 77.88.8.8 / 77.88.8.1 / 8.8.8.8 через WAN; `tunnel_ok` считает, что VPN
  работает, если Cloudflare trace показывает `loc` не `RU`, — впиши код своей страны.

## Частые грабли

- **Около :07 и :37** работает `apply-vless.sh`: ручные тяжёлые действия в это время подождут блокировку.
- **busybox**: нет `timeout`, `nohup`, `diff`, `flock -w`. Долгие команды по ssh запускай отвязанно:
  `setsid sh -c '/usr/bin/apply-vless.sh --force > /tmp/apply.log 2>&1' &`.
- **Память**: смотри `mem` или `vpn mem`. «Своя» память (RssAnon) у sing-box ~15 MB. Большая цифра RSS — это в
  основном код программы, он не страшен. Лимиты Go (`GOMEMLIMIT`) — в init-скриптах.
- **Ручная правка `config.json`** — на копии + `sing-box check`, потом `apply-vless.sh --force`. При невалидном
  конфиге автоматика вернёт последний рабочий (`config.json.good`).
- **Перепрошивка**: всё из `sysupgrade.conf` сохранится, но пакеты — нет. После сброса поставь их заново (шаг 1).

Подробные рецепты на каждый день — [runbook.md](runbook.md).
