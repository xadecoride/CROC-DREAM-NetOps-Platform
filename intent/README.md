# Intent Repository (`intent/`) — Git Source of Truth

Каталог `intent/` является **единым источником правды (Source of Truth)** для сетевой фабрики CLOS. Любое изменение целевого состояния сети начинается здесь: бэкенд валидирует файлы через Pydantic и `pre-flight lint` (`backend/netops/intent/`) до первого подключения к оборудованию.

---

## Структура каталога

```
intent/
├── README.md                  # Этот документ
├── inventory.yaml             # Реестр устройств фабрики (по умолчанию для локального/Docker контура)
├── platforms/                 # Декларативные YAML-профили 8 платформ (CLI-команды, парсеры, порты gNMI/SNMP/Exporter)
│   ├── arista_eos.yaml
│   ├── cisco_iosxe.yaml
│   ├── eltex_esr.yaml
│   ├── eltex_mes.yaml
│   ├── huawei_vrp.yaml
│   ├── juniper_junos.yaml
│   ├── linux_server.yaml
│   └── yadro_kornfe.yaml
├── stands/                    # Индивидуальные подкаталоги стендов (например, croc-dc1-pod1/devices/*.yaml)
└── devices/                   # Целевые декларативные профили каждого узла по умолчанию (YAML)
    ├── spine-1.croc.lab.yaml  # Cisco IOS-XE (BGP ASN 65000, Router ID 10.255.0.1)
    ├── spine-2.croc.lab.yaml  # Arista EOS   (BGP ASN 65000, Router ID 10.255.0.2)
    ├── leaf-1.croc.lab.yaml   # Huawei VRP   (BGP ASN 65101, Router ID 10.255.0.11)
    └── leaf-2.croc.lab.yaml   # Huawei VRP   (BGP ASN 65102, Router ID 10.255.0.12)
```

---

## За что отвечает каждый файл

| Файл / Каталог | Назначение и правила валидации |
|------|--------------------------------|
| [`inventory.yaml`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/intent/inventory.yaml) | Список управляемых устройств (`hostname`, `management_ip`, `management_port`, `platform`, `role`, `auth_profile`, `rack`, `unit`). **Важно:** пароли здесь хранить запрещено — `auth_profile` ссылается на ключ в переменной окружения `NETOPS_AUTH_PROFILES` или настройки стенда. |
| [`platforms/*.yaml`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/intent/platforms) | Декларативные спецификации оборудования (`PlatformProfile`): сигнатуры SSH-баннеров, CLI-команды снятия телеметрии, приоритет протоколов (`gnmi`, `node_exporter`, `snmp`, `ssh_cli`) и правила фильтрации диффа. |
| `devices/<hostname>.yaml` и `stands/<slug>/devices/*.yaml` | Декларативное описание L3/L2-интерфейсов (`interfaces`), процесса BGP (`bgp`: `asn`, `router_id`, `neighbors`, `evpn`, `vxlan_vni`) и списков контроля доступа (`acls`). Имя файла обязано строго совпадать с `hostname` из `inventory.yaml`. |
| [`backend/netops/network/diff_filters.yaml`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/backend/netops/network/diff_filters.yaml) | Правила очистки `running-config` перед сравнением: `mask_secrets` (скрытие хэшей паролей), `drop_line_patterns` (удаление меток времени и баннеров), `drop_blocks` (служебные сертификаты и OAM VPN) и `managed_sections` (сравниваются только управляемые секции, чтобы не затереть management-интерфейс и SSH/VTY). |

---

## Что проверяет Pre-Flight Linter (`GET /api/v1/intent/lint`)

Перед запуском любого `Dry-Run` модуль [`backend/netops/intent/lint.py`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/backend/netops/intent/lint.py) выполняет межузловую проверку всей фабрики:

1. **Строгая схема (`extra="forbid"`)**: любая опечатка в названии поля YAML отклоняется с указанием файла и строки.
2. **Корректность IPv4 и масок**: адрес интерфейса не может совпадать с адресом сети или broadcast (кроме `/31` и `/32`).
3. **Уникальность по фабрике**: проверяется отсутствие дублирующихся `router_id`, `management_ip` и IPv4-адресов интерфейсов между разными устройствами.
4. **Диапазоны BGP ASN**: проверка `1..4294967295` (RFC 6793) для локального и удалённых AS.
