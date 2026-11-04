# Единая база знаний для Google NotebookLM: CROC-DREAM NetOps Platform

> Этот файл подготовлен как главный источник данных (Primary Source) для загрузки в **Google NotebookLM** команды **CROC DREAM** на хакатоне «Телеком Будущего» (КРОК & МТУСИ).

---

## 1. Резюме проекта (Executive Summary)
**CROC-DREAM-NetOps-Platform** — это промышленная веб-платформа управления и мониторинга мультивендорной сетевой инфраструктуры ЦОД и корпоративных сетей.
Платформа решает проблему импортозамещения и гетерогенности оборудования (совмещение Cisco, Arista, Huawei с отечественными Eltex, Yadro), исключая ручной труд сетевых инженеров за счет:
1. **Декларативного Intent-Based управления (IaC)** с валидацией через Git.
2. **Безопасного двухфазного деплоя (Safe Two-Phase Deployment)** по архитектурной модели **«Аннушки» (Yandex Cloud)**: Pre-flight checks -> Semantic AST Diff -> Commit Confirmed -> Automated Post-flight verification -> Auto Rollback.
3. **AI Copilot & AI Guard**: LLM-ассистент оценивает риски конфигурационных патчей, автоматически диагностирует инциденты и локализует первопричины (Root Cause Analysis) по логам auditd/rsyslog и телеметрии.
4. **Комплексного мониторинга**: телеметрия серверов и коммутаторов (CPU, RAM, Disk, Temp, BGP) + метрики прикладных сервисов телефонии (VoIP / SIP MOS, Jitter, Packet Loss).
5. **Автоинвентаризации и интерактивной 2D/3D карте сети (Network Digital Twin)**.

---

## 2. Архитектурный базис (SDSM + RFC 7938 + Annushka)

### Сетевая фабрика:
- **Underlay**: eBGP Clos (Spines = AS 65000, Leaves = AS 65101/65102), p2p линки `/31`, BFD (детектирование аварий < 300 мс), Jumbo MTU 9000-9216.
- **Overlay**: MP-BGP EVPN (RFC 7432) + VXLAN (RFC 8365), Distributed Anycast Gateway (Symmetric IRB) с одинаковым Virtual MAC/IP для бесшовной миграции хостов и контейнеров.

### Конвейер NetOps:
- **SSoT (Single Source of Truth)**: Спецификации оборудования в YAML (`intent/devices/`, `intent/inventory.yaml`).
- **Синтез**: Jinja2-шаблоны нормализуют синтаксис 6 вендоров (Cisco IOS-XE, Arista EOS, Huawei VRP, Juniper Junos, Eltex MES, Yadro Kornfe).
- **Hierarchical Diff Engine**: Генерация минимального набора команд с контекстом блоков без лишнего флаппинга портов.
- **Детектирование дрейфа (Drift Detection)**: Периодический аудит (Celery Beat) соответствия живого состояния сети эталону в Git.

---

## 3. Стенд и физическая проверка
Развернут на сервере `198.51.100.10:221` под управлением Docker и Containerlab:
- `spine1` (Cisco C8000v / IOS-XE 17.3.8a) — SSH `:2211`
- `spine2` (Arista cEOSLab 4.32.0F) — SSH `:2212`
- `leaf1` (Huawei CloudEngine CE12800) — SSH `:2221`
- `leaf2` (Huawei CloudEngine CE12800) — SSH `:2222`
- `host-a` (Alpine Linux) — SSH `:2231`
- `host-b` (Alpine Linux) — SSH `:2232`
Все eBGP пиры находятся в состоянии `Established`, анонсируют префиксы клиентских сетей `10.10.11.0/24` и `10.10.12.0/24`.
