# Шаблоны конфигураций (`templates/`) — Multi-Vendor Jinja2 Engine

Каталог `templates/` содержит модульные шаблоны **Jinja2**, которые преобразуют вендор-независимый YAML-интент из [`intent/devices/`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/intent/devices) в целевой синтаксис CLI конкретной сетевой ОС.

Рендеринг выполняется модулем [`backend/netops/network/rendering.py`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/backend/netops/network/rendering.py) в режиме `StrictUndefined` (обращение к несуществующей переменной немедленно останавливает пайплайн с ошибкой до выхода в сеть).

---

## Поддерживаемые платформы

```
templates/
├── README.md          # Этот документ
├── cisco_iosxe/       # Cisco IOS-XE (Catalyst 8000V / CSR1000v)
├── arista_eos/        # Arista EOS (cEOS / vEOS)
├── huawei_vrp/        # Huawei CloudEngine VRP (CE12800)
├── eltex_mes/         # Eltex MES (коммутаторы доступа/агрегации)
├── eltex_esr/         # Eltex ESR (сервисные маршрутизаторы / Border)
├── juniper_junos/     # Juniper Junos (vMX / vQFX)
├── yadro_kornfe/      # YADRO KORNFE (SONiC CLI)
└── linux_server/      # Linux Server (POSIX / iproute2: base.j2, interfaces.j2)
```

---

## Порядок сборки шаблонов

Для каждого устройства рендерер последовательно склеивает секционные файлы внутри папки соответствующей платформы `<platform>/`:

| Порядок | Файл | За что отвечает |
|:-------:|------|-----------------|
| **1** | `base.j2` | Базовая системная конфигурация (`hostname`, базовые сервисы). Учётные записи и management-интерфейс сюда **не включаются**, чтобы не затрагивать OOB-управление. |
| **2** | `interfaces.j2` | L3/L2-интерфейсы из `intent.interfaces`, описания (`description`), IPv4-адреса и маски, состояние порта (`no shutdown` / `undo shutdown` / `shutdown`), а также автоматическая генерация `Loopback0` из `bgp.router_id`, если он не задан явно. |
| **3** | `acls.j2` | Списки контроля доступа (`ip access-list extended` / `acl name`) и правила фильтрации пакетов (`permit`/`deny`) из `intent.acls`. |
| **4** | `bgp.j2` | Конфигурация маршрутизации BGP (`router bgp <asn>` / `bgp <asn>`), `router-id`, пиры (`neighbor ... remote-as`), описания соседей, анонсируемые сети и EVPN/VXLAN VNI. |

---

## Переменные контекста Jinja2

В каждый шаблон передаются два строго типизированных объекта из [`backend/netops/intent/models.py`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/backend/netops/intent/models.py):

- `device` (`InventoryDevice`): `hostname`, `platform`, `role`, `management_ip`, `site`.
- `intent` (`DeviceIntent`):
  - `intent.interfaces`: список интерфейсов, где `iface.ipv4_address` — объект `ipaddress.IPv4Interface` (доступны `.ip`, `.netmask`, `.with_prefixlen`).
  - `intent.bgp`: настройки BGP (`asn`, `router_id`, `neighbors`, `networks`, где `net` — `IPv4Network` с `.network_address`, `.netmask`, `.prefixlen`, `.hostmask`).
  - `intent.acls`: список ACL и правил.
