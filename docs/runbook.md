# Runbook — типовые задачи

Все команды выполняются с Mac. `ssh openwrt-ts` работает откуда угодно, `ssh openwrt` — только из дома.
Перед изменениями перечитай «Золотые правила» в [../CLAUDE.md](../CLAUDE.md).

Для человека есть короткая команда `vpn` (справка — `vpn help`, таблица — в README.md). Ниже — то, что под ней,
плюс то, что нужно агенту.

## Посмотреть состояние

```bash
ssh openwrt-ts 'vpn'                             # сводка: интернет, VPN (страна выхода), sing-box, Tailscale, события
ssh openwrt-ts 'sb-ping'                         # ноды и задержки, что выбрано в proxy/auto/gemini
ssh openwrt-ts 'tail -12 /tmp/health.log'        # раз в 5 мин: mem, ct, sta, sb=RSS, proxy=код/время DNS, direct
ssh openwrt-ts 'sb-ping events'                  # постоянный журнал автоматики: перезапуски, откаты, сторожа
ssh openwrt-ts 'vpn report 24'                   # готовый отчёт за N часов с итогом «всё хорошо / есть на что посмотреть»
ssh openwrt-ts 'logread -e sb-watchdog -e apply-vless -e sb-restart -e ts-watchdog -e update-rulesets | tail -30'
ssh openwrt-ts '/usr/bin/sb-restart.sh'          # просто перезапустить sing-box (безопасно, ~6 с без интернета)
ssh openwrt-ts 'logread -e sing-box | tail -20'  # ошибки самого sing-box
ssh openwrt-ts 'mem'                             # свободная память и топ-10 процессов по RSS
./scripts/pull-from-router.sh                    # снимок в mirror/ + STATE.txt + свежий бэкап
```

## Уведомления в Telegram

Роутер сам пишет в Telegram, только когда что-то не так: VPN не работает 10+ мин и когда снова заработал; у провайдера
не было интернета 5+ мин (сообщение придёт, когда интернет вернётся); роутер перезагрузился; важные события
автоматики (откат конфига, автоперезагрузка, Tailscale требует входа, sing-box упал). Короткие сбои, с которыми
автоматика справилась сама, не присылает.

```bash
ssh -t openwrt-ts 'vpn tg setup'   # подключить: спросит токен от @BotFather, попросит написать боту, пришлёт проверку
ssh openwrt-ts 'vpn tg'            # включены ли, сколько сообщений ждёт отправки
ssh openwrt-ts 'vpn tg test'       # проверочное сообщение
ssh openwrt-ts 'vpn tg off'        # выключить (on — включить обратно)
```
- Настройки: `/etc/router-notify.conf` (токен — секрет; в mirror/ не снимается, в sysupgrade.conf есть).
  Состояние и очередь: `/root/.notify/`. Пороги и список пересылаемых событий — в начале `/usr/bin/router-notify`.
- Отправка сначала идёт обычным путём (через VPN), не получилось — напрямую через провайдера. Нет связи — сообщение
  ждёт в очереди (хранятся 30 последних) и уйдёт позже.
- Если роутер умер целиком, он ничего не напишет. Для этого есть отложенная идея «внешний пульс» в CLAUDE.md.

**Команды роутеру из Telegram** (тому же боту): `/status`, `/nodes`, `/gemini`, `/report`, `/log`, `/site ozon.ru`,
`/auto`, `/update`, `/speed`, `/restart` и `/reboot` (оба с подтверждением `/restart_yes` / `/reboot_yes` в течение
2 мин), `/help`. Отвечает только чату из `TG_CHAT`, остальные молча игнорирует. Сообщения старше 2 мин и всё,
что пришло, пока бот не работал, не выполняются, иначе `/reboot_yes` повторился бы после перезагрузки.
- Служба `/etc/init.d/router-bot` (procd, respawn) запускает `router-notify --bot`: long polling `getUpdates`
  на 50 с, сначала через VPN, иначе напрямую. Лог: `logread -e router-bot`. `vpn tg` показывает, работает ли она.
- Новая команда: добавить ветку в `bot_run()` в `/usr/bin/router-notify`, строку в `BOT_HELP` и в `setMyCommands`.
  Аргументы проверять по белому списку символов (как в `/site`).
- `jq` на роутере собран без регулярных выражений: `gsub`/`test`/`match` не работают, вместо них `split`/`join`.

## Автоотчёт дважды в день

- **На роутере:** cron в 07:45 и 19:45 мск запускает `router-report --save`. Отчёт за 12 ч пишется в `/root/reports.log`
  (хранятся 14 последних, смотреть `vpn report all`). Итог бывает трёх видов: «всё хорошо»; «сейчас всё работает; за 12 ч были сбои, автоматика справилась» (пункты `~`);
  «ПРОБЛЕМА СЕЙЧАС» (пункты `!`). Событие `router-report` в журнале (`vpn log`) пишется только при проблеме сейчас.
  Прошедший сбой виден в отчёте ещё 12 ч: отчёт всегда смотрит на последние 12 ч.
- Задачи Claude на Mac больше нет: пользователь убрал её 2026-10-04 за ненадобностью. Её промпт остался в
  `~/.claude/scheduled-tasks/router-health-check/SKILL.md`, если понадобится вернуть.
- Пороги «есть на что посмотреть» — в начале `/usr/bin/router-report`. Нормальный фон (это не тревога): перезапуски
  в :07/:37 из-за смены адресов у P1 и единичные короткие ночные провалы VPN, которые сторож чинит сам.

## Замерить скорость

```bash
ssh openwrt-ts 'vpn speed'        # = /usr/bin/router-speedtest.sh
```
~1 мин, не около :07/:37. Приём через VPN (Cloudflare, 3×25 MB), напрямую (Selectel, mirror.yandex), отдача через VPN
и напрямую, CPU роутера во время каждого замера. Ориентиры на 2026-10-03: VPN ~90, напрямую ~95–100,
отдача ~10–12 Мбит/с (потолок провайдера). Мерится с самого роутера — устройства по Wi-Fi могут получать меньше.

## «Сайт не открывается / открывается не так»

```bash
ssh openwrt-ts 'sb-ping route example.com'       # куда идёт сайт и каким правилом
ssh openwrt-ts 'sb-ping add-direct example.com'  # всегда напрямую (домен со всеми поддоменами; можно IP/подсеть/URL)
ssh openwrt-ts 'sb-ping add-proxy example.com'   # всегда через VPN
ssh openwrt-ts 'sb-ping del example.com'         # убрать из ручных списков
ssh openwrt-ts 'sb-ping lists'                   # что в списках
```
Применяется за пару секунд без перезапуска (и для маршрутизации, и для DNS). Старый ответ DNS может жить
в кэше dnsmasq/sing-box до истечения TTL — маршрутизации это не мешает, она смотрит на домен.

Российский сервис «ругается на VPN» (так было с одним банком) — ищи, какие его запросы уходят в VPN:
у банка может быть подсеть, которой нет в geoip-ru. Смотри активные соединения
через Clash API (`/connections`, секрет в `/etc/sing-box/.clash-secret`) и добавляй домены/подсети в `add-direct`.

Gemini не открывается — сначала `sb-ping`: живы ли P1-ноды (`gemini-auto`). Через P2 Gemini не работает.
Остальной Google идёт через `auto` и может попасть на P2 — это сознательное решение пользователя.

## Переключить ноду вручную

```bash
ssh openwrt-ts 'vpn nodes'        # номера живых нод (= sb-ping)
ssh openwrt-ts 'vpn use 3'        # proxy → нода №3 (номер может «уехать» после перетеста — проверь)
ssh openwrt-ts 'vpn auto'         # вернуть автоматический выбор
ssh openwrt-ts 'vpn gemini'       # ноды для Gemini (только P1, своя нумерация) (= sb-ping gemini)
ssh openwrt-ts 'vpn gemini 2'     # gemini-select → нода №2 из этого списка
ssh openwrt-ts 'vpn gemini auto'  # вернуть gemini-auto
```
Ручной выбор не опасен: если выбранная нода умрёт, сторож вернёт автовыбор (`proxy` — через 2 мин, если VPN не
работает; Gemini — при проверке раз в 5 мин, нода должна не ответить дважды подряд).

Не все P1-ноды бывают живы одновременно: 2026-10-03 отвечали ~11 из 25, а через сутки снова все 25. Это временные
проблемы локаций у провайдера P1. Например, одна из локаций тогда не отвечала ни со старыми, ни со свежими параметрами из подписки.
Чинить у себя ничего не нужно, автовыбор берёт только живые ноды.

## Обновить подписки сейчас

```bash
ssh openwrt-ts '/usr/bin/apply-vless.sh'
```
Перезапустит sing-box только если серверы реально поменялись. Через VPN не скачалось — скачает напрямую.

## Поменять конфиг sing-box руками

1. Бэкап: `./scripts/pull-from-router.sh` (или скопируй файл в `backups/<дата-событие>/`).
2. Правь **копию** на роутере, проверь: `sing-box check -c /tmp/new.json` — вывод должен быть пустым
   (предупреждения о deprecated тоже считаются проблемой: их удаляют в следующих версиях).
3. Для изменений DNS — прогони `scripts/router-dnstest.py -c /tmp/new.json домены…` (см. ниже).
4. Положи на место и примени: `/usr/bin/apply-vless.sh --force`. Не около :07/:37.
   Запускай отвязанно от SSH: `setsid sh -c '/usr/bin/apply-vless.sh --force > /tmp/apply.log 2>&1' &`,
   потом читай `/tmp/apply.log` — ждёт «Применено, связь есть».
5. Учти: `update-vless.py` при каждом запуске пересобирает группы, правила обхода нод (сразу после
   hijack-dns/sniff) и сохраняет всё остальное как есть.

## Проверить DNS-правила

```bash
scp -O scripts/router-dnstest.py openwrt-ts:/tmp/
ssh openwrt-ts 'python3 /tmp/router-dnstest.py ya.ru vk.com google.com; rm /tmp/router-dnstest.py'
```
Покажет для каждого домена: время ответа, сработавшее правило и сервер (`dns-ru` или `dns-remote`).
Боевой sing-box не трогает. Зарубежные домены **не должны** попадать в `dns-ru`.

## Обновить sing-box

В `/etc/apk/world` стоит `sing-box~1.13` — обновляются только 1.13.x. Для перехода на 1.14 сначала проверь конфиг
бинарником новой версии (как делали для 1.13 — распаковать пакет в `/tmp`, `sing-box check`), потом поменяй строку
на `sing-box~1.14`. Обновление — с остановкой, иначе post-upgrade пакета сделает быстрый перезапуск:
```sh
# на роутере, файлом через setsid, не около :07/:37:
exec 9>/tmp/apply-vless.lock; flock 9     # flock без -n ждёт сколько нужно
/etc/init.d/sing-box stop; sleep 3
apk update && apk upgrade sing-box        # именно upgrade: `apk add` установленный пакет не обновляет
/etc/init.d/sing-box stop                 # post-upgrade его запустил
sing-box check -c /etc/sing-box/config.json
flock -u 9; exec 9>&-
/usr/bin/apply-vless.sh --force
```
Если что-то пойдёт не так между stop и последней строкой — сторож сам поднимет sing-box в течение минуты.
Tailscale (`tailscale~1.98`) — так же: `apk upgrade tailscale`, затем проверь, что `/etc/init.d/tailscale` наш
(GOGC/GOMEMLIMIT), `tailscale debug prefs` — настройки на месте.
После: проверь `GOMEMLIMIT` в `/proc/$(pidof sing-box)/environ` (apk сохраняет наш init-скрипт, пакетный кладёт
в `/etc/init.d/sing-box.apk-new`), удали `*.apk-new` от sing-box, следи за `sb=` в health.log.
Перед мажорным обновлением прочитай https://sing-box.sagernet.org/deprecated/ и https://sing-box.sagernet.org/migration/.

**Откат на 1.12.17:** бинарник в `backups/2026-10-03-pre-dns-migration/sing-box-1.12.17-aarch64.gz`.
Скопировать на роутер в `/tmp`, остановить sing-box, `gunzip -c … > /usr/bin/sing-box`, `chmod 755`,
`apply-vless.sh --force`. Текущий конфиг совместим и с 1.12.

## Бэкап и восстановление

- Полный бэкап сейчас: `ssh openwrt-ts /usr/bin/router-backup.sh` (на роутере хранятся 2) или
  `./scripts/pull-from-router.sh` (заодно скачает его в `backups/auto/`, тоже хранятся 2).
- Восстановление полного бэкапа: LuCI → Система → Резервное копирование → Восстановить (или `sysupgrade -r файл`).
  Включает `/etc/config`, всё из `/etc/sysupgrade.conf`, но не пакеты — после сброса нужно поставить
  sing-box, tailscale, python3, jq, curl, https-dns-proxy, dnsmasq-full (полный список — `mirror/etc/apk/world`).
- Отдельные файлы: из `mirror/` (актуально на момент снимка) или из памятных папок `backups/<дата-событие>/`.

## Нет интернета дома

Сначала — что уже сделала автоматика: `ssh openwrt-ts 'sb-ping events'`. Большинство сбоев она чинит сама
за 1–5 мин (см. таблицу «Что будет, если…» в architecture.md).

Проверки — те же, что у автоматики:
```bash
ssh openwrt-ts '. /usr/lib/sb-common.sh; for t in direct_ok tunnel_ok routing_ok api_ok providers_ok; do $t && echo "$t OK" || echo "$t FAIL"; done'
```
1. `ssh openwrt-ts` не работает — лежит сам роутер или провайдер (Tailscale идёт мимо sing-box).
2. `direct_ok` FAIL — нет интернета у провайдера, роутер трогать бесполезно.
3. `providers_ok` FAIL — мертвы ноды (провайдеры VPN). Сторож через 5 мин сам скачает подписки напрямую;
   можно `apply-vless.sh` руками.
4. `routing_ok`/`api_ok` FAIL или `tunnel_ok` FAIL при живых нодах — поломка у нас: `/usr/bin/sb-restart.sh`
   (сторож сделает это и сам, не чаще раза в 30 мин).
5. На роутере `tunnel_ok` OK, а у устройств нет связи — проверь, есть ли `tun0` во flowtable:
   `nft list flowtable inet fw4 ft`; нет — `fw4 reload`. Перезагрузка — в последнюю очередь.

## Tailscale

Как устроено — в architecture.md, раздел Tailscale. Коротко: настройки хранит сам Tailscale, следит `ts-watchdog.sh`.

```bash
ssh openwrt-ts 'tailscale status'                                   # узлы, кто онлайн
ssh openwrt-ts 'tailscale debug prefs | jq "{WantRunning, RouteAll, AdvertiseRoutes, AutoUpdate}"'
ssh openwrt-ts 'logread -e ts-watchdog | tail'                       # что делал сторож
ssh openwrt-ts 'tailscale set --advertise-exit-node=true'            # менять настройки — только так
```
- **Не использовать** `tailscale up … --reset` и не возвращать перезапуск Tailscale в `rc.local`.
- Сторож пишет «требует входа» — нужен человек: `ssh openwrt` из дома (по LAN) и `tailscale up`, открыть ссылку.
  Если и из дома никак — LuCI `http://LAN_IP`.
- Exit node начинает работать только после одобрения в админке: login.tailscale.com → Machines → openwrt →
  ⋯ → Edit route settings → Use as exit node. Проверка с телефона: выбрать openwrt выходом, открыть 2ip.ru —
  российский домашний IP; google.com открывается.
- Домашний ПК разбудить: `ssh openwrt-ts wakeonlan <MAC>` (MAC — в `/tmp/dhcp.leases` на роутере, пока ПК в сети).
- tailscaled на роутере идёт мимо sing-box (fwmark) — правила по процессу в sing-box не нужны и не сработают.
