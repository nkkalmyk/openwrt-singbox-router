# Домашний роутер: OpenWrt + sing-box

Рабочая папка, в которой (вместе с ИИ-агентами — Claude Code и другими) обслуживается домашний роутер:
документация, снимок настроек и скрипты автоматики.

- **Железо:** Xiaomi AX3000T (MT7981, 2 ядра, 234 MB RAM + zram), OpenWrt 25.12, стоит за роутером провайдера.
- **sing-box 1.13 в режиме TUN:** российские сайты идут напрямую, всё остальное — через VPN (VLESS + Reality,
  две подписки от разных провайдеров, автовыбор живой ноды).
- **Раздельный DNS** внутри sing-box: российские домены — к Яндексу напрямую, остальные — Cloudflare DoH через VPN;
  три уровня резерва в dnsmasq.
- **Автоматика устойчивости:** ежечасное обновление подписок с откатом, сторож sing-box, безопасный перезапуск,
  постоянный журнал событий, сторож Tailscale. Всё на busybox-шелле и python3.
- **Tailscale** на роутере: удалённый доступ и exit node, трафик Tailscale идёт мимо sing-box.

## Что где

| Путь | Что |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Точка входа для агентов: доступ, золотые правила, открытые вопросы (`AGENTS.md` — ссылка на него) |
| [docs/setup.md](docs/setup.md) | **Как собрать такое же самому:** пакеты, какой файл куда, что подставить, первый запуск |
| [docs/architecture.md](docs/architecture.md) | Как всё устроено: путь трафика, правила, DNS, автоматика, «что будет, если…» |
| [docs/runbook.md](docs/runbook.md) | Типовые задачи и что делать, если что-то сломалось |
| [docs/history.md](docs/history.md) | Что и почему меняли, принятые решения |
| [docs/resilience-audit.md](docs/resilience-audit.md) | Аудит устойчивости: найденные дыры и как их закрыли |
| [mirror/](mirror/) | Снимок файлов с роутера (пути как на роутере): скрипты в `usr/bin`, общая библиотека `usr/lib/sb-common.sh`, конфиги |
| [scripts/](scripts/) | `pull-from-router.sh` — снять снимок с роутера; `router-dnstest.py` — проверить DNS-правила временным sing-box |
| [publish/publish.py](publish/publish.py) | Как собирается эта публичная копия |

## Скрипты

На роутере (busybox `sh` и python3; лежат в `/usr/bin`, в репозитории — в [mirror/usr/bin/](mirror/usr/bin/)):

| Скрипт | Что делает | Кто запускает |
|---|---|---|
| [vpn](mirror/usr/bin/vpn) | Одна команда для человека: состояние, ноды, куда идёт сайт, ручные списки, перезапуск, журнал, скорость | руками |
| [sb-ping](mirror/usr/bin/sb-ping) | Ноды и задержки, ручной выбор ноды, маршрут сайта, правка ручных списков, журнал событий | руками, через `vpn` |
| [update-vless.py](mirror/usr/bin/update-vless.py) | Качает подписки (через VPN, при неудаче напрямую), фильтрует ноды, пересобирает группы и правила обхода | `apply-vless.sh` |
| [apply-vless.sh](mirror/usr/bin/apply-vless.sh) | Применяет подписки: проверка конфига → безопасный перезапуск → проверка туннеля → откат, если связи нет | cron, раз в час |
| [sb-watchdog.sh](mirror/usr/bin/sb-watchdog.sh) | Сторож sing-box: поднимает упавший, чинит зависший, уводит с мёртвых нод, обновляет подписки при блокировке | cron, раз в минуту |
| [sb-restart.sh](mirror/usr/bin/sb-restart.sh) | Единственный правильный перезапуск sing-box: под общей блокировкой, с проверками после | руками, сторож, подъём WAN |
| [sb-common.sh](mirror/usr/lib/sb-common.sh) | Общая библиотека: проверки интернета, туннеля, маршрутизации и нод; блокировка; постоянный журнал | подключают все |
| [ts-watchdog.sh](mirror/usr/bin/ts-watchdog.sh) | Сторож Tailscale (опрос через локальный сокет, без запуска тяжёлого `tailscale`) | cron, раз в 2 мин |
| [health.sh](mirror/usr/bin/health.sh) | Строка в `/tmp/health.log`: память, conntrack, Wi-Fi-клиенты, память sing-box, проверка VPN и прямого доступа | cron, раз в 5 мин |
| [update-rulesets.sh](mirror/usr/bin/update-rulesets.sh) | Обновляет базы geoip-ru / geosite-category-ru, ставит новую, только если она проверена | cron, раз в неделю |
| [router-backup.sh](mirror/usr/bin/router-backup.sh) | Полный бэкап `sysupgrade -b`, хранит два | cron, раз в неделю |
| [router-speedtest.sh](mirror/usr/bin/router-speedtest.sh) | Замер скорости через VPN и напрямую, приём и отдача, загрузка CPU | `vpn speed` |
| [router-report](mirror/usr/bin/router-report) | Отчёт о здоровье за N часов: текущие проверки, провалы VPN и провайдера, память, журнал событий, итог | cron, 2 раза в день; `vpn report` |
| [mem](mirror/usr/bin/mem) | Память по процессам: своя (RssAnon) и код (RssFile) | руками |
| [sb-route-test](mirror/usr/bin/sb-route-test) | Открывает список сайтов и показывает, через какую ноду и по какому правилу ушло каждое соединение | руками |

Рядом: службы [init.d/sing-box](mirror/etc/init.d/sing-box) (лимиты памяти Go, безопасный триггер на подъём WAN) и
[init.d/tailscale](mirror/etc/init.d/tailscale), расписание [crontabs/root](mirror/etc/crontabs/root),
пример [config.json](mirror/etc/sing-box/config.json) sing-box.

На Mac ([scripts/](scripts/)): [pull-from-router.sh](scripts/pull-from-router.sh) снимает снимок настроек и бэкап,
[router-dnstest.py](scripts/router-dnstest.py) проверяет DNS-правила временным sing-box, не трогая боевой.

Как этим пользоваться на роутере:

```sh
vpn                      # сводка: интернет, VPN (страна выхода и нода), sing-box, Tailscale, последние события
vpn nodes                # ноды с задержками
vpn use 3 / vpn auto     # включить ноду №3 / вернуть автовыбор
vpn site example.com     # куда пойдёт сайт — напрямую или через VPN — и по какому правилу
vpn direct example.ru    # этот сайт всегда напрямую (vpn proxy … — всегда через VPN, vpn del … — убрать)
vpn update               # обновить ноды из подписок (перезапуск, только если они изменились)
vpn restart              # безопасно перезапустить sing-box (~6 с без интернета)
vpn log                  # что делала автоматика
vpn report 24            # отчёт о здоровье за сутки с итогом
vpn speed                # замер скорости через VPN и напрямую
```

## Это очищенная копия

Оригинал живёт в приватной папке; сюда он попадает через [publish/publish.py](publish/publish.py), который
заменяет личное и не даёт опубликовать копию, если что-то осталось:

- адреса домашней сети и Tailscale заменены на `LAN_IP`, `LAN_NET`, `WAN_IP`, `UPSTREAM_IP`, `TS_IP` и т.п.
  Остались только общеизвестные адреса (публичные DNS, `127.0.0.1`, служебные и документационные диапазоны);
- VPN-провайдеры названы `P1` (основной) и `P2` (запасной), в том числе в префиксах тегов нод `P1-…`/`P2-…`;
- в [config.json](mirror/etc/sing-box/config.json) вместо 38 настоящих нод — две примерные на каждого провайдера
  (`203.0.113.x`, нулевой UUID, заглушки ключей Reality), секрет Clash API — `CHANGE_ME`,
  ссылки подписок в `update-vless.py` — заглушки;
- ручные списки сайтов (`rule-set/my-*.json`) пустые, статические DHCP-аренды убраны, MAC-адреса замаскированы;
- бэкапы (`backups/`) не публикуются вовсе.

Поэтому скрипты отсюда нельзя просто скопировать на свой роутер: подставь свои подписки, адреса и списки.
Используй как пример и справочник; пошагово — [docs/setup.md](docs/setup.md).
