# План: обновление OpenWrt 25.12.2 → 25.12.5

Составлен 2026-10-05. **Выполнен 2026-10-05 16:57–17:03** — итоги в docs/history.md.

## Зачем

Закрыть уязвимости из аудита безопасности (SEC-01): dnsmasq 2.91 → 2.93, mbedtls 3.6.5 → 3.6.7, ядро 6.12.74 → 6.12.94,
плюс свежие базы Wi-Fi-регуляторики и сертификатов. Через `apk upgrade` это не сделать: репозитории привязаны к 25.12.2.

## Что меняется и что нет

- **Не меняются:** sing-box 1.13.21 и tailscale 1.98.3 — в 25.12.5 те же версии. Конфиг sing-box, схема
  маршрутизации, DNS, скрипты — не трогаются.
- **Меняются:** система целиком (169 пакетов): ядро, dnsmasq, библиотеки, LuCI, python3 3.13.9 → 3.13.15.
- `owut check` (2026-10-05 16:30): **«It is safe to proceed with an upgrade»**, конфликтов нет
  (`dnsmasq` даёт `dnsmasq-full`, `nftables` — `nftables-json`).

## Как

Через `owut` (уже установлен): сервер сборки OpenWrt собирает прошивку **сразу со всеми нашими пакетами**
(sing-box, tailscale, python3, https-dns-proxy, avahi, zram, jq, tcpdump…). После перезагрузки ничего не нужно
доустанавливать — интернет и VPN поднимаются сами, как после обычной перезагрузки. Настройки сохраняются (`sysupgrade`
с сохранением конфигурации, список — `/etc/sysupgrade.conf`).

Без `owut` (стоковая прошивка + `apk add` 123 пакетов вручную) — запасной путь, если сервер сборки недоступен:
простой интернета 15+ мин вместо ~5.

## Подготовка (днём, без перерывов в работе)

1. **Дополнить `/etc/sysupgrade.conf`** — сейчас при перепрошивке потеряется:
   - журналы `/root/events.log`, `/root/uptime.log`, `/root/reports.log`, очередь `/root/.notify/`;
   - ярлык автозапуска бота `/etc/rc.d/S99router-bot` (сам скрипт сохраняется, а «запускать при загрузке» — нет).
   Проверка: `sysupgrade -l | grep -E "events.log|S99router-bot"`.
2. **Бэкапы на Mac** в `backups/<дата>-pre-25.12.5/`:
   - `./scripts/pull-from-router.sh` (снимок + полный бэкап);
   - `apk info -v`, `uci export`, `/etc/apk/world`, `ip rule`, `iw reg get` — для сверки «до/после»;
   - стоковые образы 25.12.2 и 25.12.5 для отката: `…ubootmod-squashfs-sysupgrade.itb` и
     `…ubootmod-initramfs-recovery.itb` (для аварийного восстановления через U-Boot).
3. **Пробная сборка:** `owut download` — сервер соберёт образ, owut проверит подпись и контрольную сумму.
   Посмотреть размер образа и свободную память (образ лежит в `/tmp`, это RAM; сейчас свободно ~70 MB).
   Если памяти впритык — образ можно собрать и проверить, но ставить отдельно.

### Подготовка выполнена 2026-10-05 16:34–16:45

- `/etc/sysupgrade.conf`: добавлены журналы и `/etc/rc.d/S99router-bot` (`sysupgrade -l`: 96 файлов, было 89).
- `backups/2026-10-05-pre-25.12.5/`: полный бэкап, `state-before.txt` (версии, пакеты, службы, ip rule, Wi-Fi, sysctl,
  cron, память), `uci-export.txt` (права 600, там пароли), прежний `sysupgrade.conf`.
- `images/`: стоковые 25.12.2 и 25.12.5 (`sysupgrade.itb` + `initramfs-recovery.itb`, sha256 сверены с сайтом OpenWrt) и
  **собранный owut образ 25.12.5 со всеми нашими пакетами** —
  `openwrt-25.12.5-ax3000t-ubootmod-owut-custom-sysupgrade.itb`, 42.6 MB, sha256 `cf31f423…dbaa4`
  (`owut-verify.txt`). Сборка на сервере — 87 с. `sysupgrade -T` на роутере: **Signature check OK**.
- **Память:** пока образ лежит в `/tmp`, у роутера свободно ~33 MB (без него ~70). Для установки этого хватает
  (sysupgrade сам останавливает службы), но образ кладём в `/tmp` только непосредственно перед установкой.

## Ночь обновления

Условия: пользователь **дома** (если Tailscale не поднимется, нужен вход по LAN; если роутер не загрузится —
нужен физический доступ к кнопке reset). Семья предупреждена: ~5 мин без интернета. Не около :07/:37.

1. `vpn` — всё OK; `free -m` — свободно 60+ MB.
2. Образ — уже собранный (с Mac: `scp -O …owut-custom-sysupgrade.itb openwrt-ts:/tmp/fw.itb`, сверить sha256) или
   свежий `owut download -V 25.12.5 -i /tmp/fw.itb` (если с тех пор вышли исправления пакетов). `sysupgrade -T /tmp/fw.itb`.
3. `owut install -i /tmp/fw.itb` (или `sysupgrade -v /tmp/fw.itb`) → роутер ставит прошивку и перезагружается.
   **Без интернета ~4–6 мин.**
4. Ждать: `ssh router` (по LAN), потом `ssh openwrt-ts`.

## Проверка после (чек-лист)

| Что | Как | Ожидаем |
|---|---|---|
| Версия | `cat /etc/openwrt_release; uname -r` | 25.12.5, 6.12.94 |
| Исправленные пакеты | `apk info -v \| grep -E "dnsmasq-full\|libmbedtls"` | 2.93, 3.6.7 |
| VPN-часть | `apk info -v \| grep -E "sing-box\|tailscale"`, `/etc/apk/world` | 1.13.21, 1.98.3; закрепления `~1.13`, `~1.98` на месте |
| Общее состояние | `vpn` | всё OK |
| Маршрутизация | `sb-ping route ozon.ru`, `sb-ping route youtube.com` | напрямую / через VPN |
| Init sing-box | `grep GOMEMLIMIT /etc/init.d/sing-box` | наш файл (лимиты памяти), рядом может лежать `.apk-new` |
| Службы | `ls /etc/rc.d/` | sing-box, router-bot, tailscale, https-dns-proxy, avahi, zram, cron |
| Автоматика | `crontab -l`, `sb-ping events` | 9 задач; сторож пишет события |
| Tailscale | `tailscale status` | Running, роутер виден |
| Безопасность | `uci show dropbear`, `sysctl net.ipv4.tcp_orphan_retries`, `ls -l /etc/config/wireless` | пароль выкл.; 3; права 600 |
| Wi-Fi | `iw reg get`, `iwinfo` | RU, каналы 13 и 48, клиенты вернулись |
| Сеть | `uci show firewall \| grep Asus`, `uci show dhcp \| grep yeelink` | правило к лампе, адрес лампы закреплён |
| Уведомления | Telegram | пришло «роутер перезагрузился» |
| Память через 30 мин | `vpn`, `tail /tmp/health.log` | sb= ~10–25 MB, свободно 60+ MB |

После: `./scripts/pull-from-router.sh`, запись в `docs/history.md`, обновить версии в `CLAUDE.md` и `architecture.md`.

## Откат

1. **Что-то одно не работает** (служба, настройка) — поправить по снимку в `mirror/` и бэкапам.
2. **Система загрузилась, но всё плохо** — прошить стоковый 25.12.2 с сохранением настроек (`sysupgrade` по LAN),
   затем `apk add` пакетов из `mirror/etc/apk/world`; либо восстановить полный бэкап через LuCI.
3. **Не загружается** — аварийное восстановление U-Boot (раскладка `ubootmod`): с зажатой reset при включении →
   страница восстановления/TFTP → `…ubootmod-initramfs-recovery.itb` → дальше как п. 2. Нужен физический доступ и
   кабель к LAN-порту.

## Риски

- Сервер сборки (`sysupgrade.openwrt.org`) недоступен или долго собирает — перенести или запасной путь.
- Мало RAM для образа в `/tmp` — проверяется на шаге «пробная сборка».
- Новый `dnsmasq` 2.93 / ядро ведут себя иначе — ловится чек-листом; откат п. 2.
- `wpad-basic-mbedtls` обновится → Wi-Fi-клиенты переподключатся (это и так произойдёт при перезагрузке).
