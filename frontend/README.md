# NetOps Frontend — документация клиентского ядра

Добро пожаловать в клиентское ядро платформы **CROC DREAM NetOps Platform**. Этот документ написан для разработчиков и сетевых инженеров команды: он объясняет, как устроен фронтенд, за что отвечает каждый каталог и модуль, как работают темы оформления (`Dark` / `Light`), локализация (`RU` / `EN`), ролевая модель (`RBAC`) и потоковая телеметрия.

Стек: **React 19.2 · TypeScript 5.9 (`strict`, `0 any`) · Vite 8 · Tailwind CSS 3.4 · Lucide Icons · Playwright E2E · Oxlint**.

---

## Оглавление

1. [Быстрый старт (за 1 минуту)](#1-быстрый-старт-за-1-минуту)
2. [Общая архитектура и потоки данных](#2-общая-архитектура-и-потоки-данных)
3. [Структура каталогов](#3-структура-каталогов)
4. [Подробное описание каждого модуля](#4-подробное-описание-каждого-модуля)
   - [4.1. Ядро приложения (`src/api.ts`, `src/App.tsx`, `src/i18n.ts`, `src/constants.ts`)](#41-ядро-приложения)
   - [4.2. Каркас и атомарная дизайн-система (`src/components/layout/`)](#42-каркас-и-атомарная-дизайн-система-srccomponentslayout)
   - [4.3. Рабочие экраны и вкладки (`src/components/views/`)](#43-рабочие-экраны-и-вкладки-srccomponentsviews)
   - [4.4. Модальные окна, дроверы и визарды (`src/components/modals/`)](#44-модальные-окна-дроверы-и-визарды-srccomponentsmodals)
   - [4.5. Визуализация графа и прогнозов (`src/components/charts/`)](#45-визуализация-графа-и-прогнозов-srccomponentscharts)
5. [Дизайн-система, темы (`Dark` / `Light`) и локализация (`RU` / `EN`)](#5-дизайн-система-темы-dark--light-и-локализация-ru--en)
6. [Матрица ролевого доступа (RBAC во фронтенде)](#6-матрица-ролевого-доступа-rbac-во-фронтенде)
7. [Горячие клавиши NOC-оператора](#7-горячие-клавиши-noc-оператора)
8. [Проверка качества, сборка и E2E-тесты](#8-проверка-качества-сборка-и-e2e-тесты)

---

## 1. Быстрый старт (за 1 минуту)

### Вариант А — Локальная разработка с хот-релоадом

Убедитесь, что бэкенд запущен на `http://127.0.0.1:8001` (или переопределите `VITE_API_URL`), после чего выполните:

```bash
cd frontend
npm install
npm run dev
```

Откройте в браузере:
- **Основной интерфейс NOC**: http://127.0.0.1:5173
- **Принудительный запуск визарда первичной настройки («Матрёшка»)**: http://127.0.0.1:5173/?setup=1

Vite автоматически проксирует запросы `/api/v1/*` и WebSocket-соединения на локальный бэкенд (`127.0.0.1:8001`).

### Вариант Б — Продакшн-сборка и проверка типов

```bash
npm --prefix frontend run build      # Строгая проверка tsc -b + сборка чанков Vite
npx --prefix frontend oxlint frontend/src/  # Статический анализ (0 warnings, 0 errors)
```

---

## 2. Общая архитектура и потоки данных

Фронтенд спроектирован по принципу **Zero-Page-Reload Single Page Application (SPA)** с агрессивным разделением бандлов (`React.lazy` + `Suspense`) и гибридной синхронизацией состояния:

```
┌───────────────────────────────────────────────────────────────────────────┐
│                         App.tsx (Root Orchestrator)                       │
│  • Тема (dark/light) · Локаль (ru/en) · Роль (viewer/operator/admin/owner)│
│  • Фоновый пуллинг (8s) с глубоким сравнением ссылок (areDevicesEqual)     │
│  • Одноразовые WebSocket-билеты (/ws/ticket -> /ws/stream)                │
└───────────────┬───────────────────────────────┬───────────────────────────┘
                │                               │
                ▼                               ▼
┌───────────────────────────────┐ ┌─────────────────────────────────────────┐
│   Eager Shell (мгновенный)    │ │   Lazy-Loaded Chunks (по требованию)    │
│ • Header / Sidebar / MobileNav│ │ • views/* (DevicePage, DiffViewer, ...) │
│ • Dashboard + TopologyMap (2D)│ │ • modals/* (WelcomeModal, ShareModal)   │
│ • CommandPalette (Ctrl+K)     │ │ • charts/Topology3D (3D Canvas/WebGL)   │
└───────────────┬───────────────┘ └─────────────────────┬───────────────────┘
                │                                       │
                └───────────────────┬───────────────────┘
                                    ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                   src/api.ts (Strict Typed HTTP/WS Client)                │
│  • Единая обёртка request<T>() с таймаутами (AbortSignal.timeout)         │
│  • Автоматическая подстановка Bearer JWT + обработка 401/403/503          │
│  • 100% строгая типизация ответов API (0 типов any во всём проекте)       │
└───────────────────────────────────────────────────────────────────────────┘
```

### Ключевые инженерные решения:
1. **Защита от лишних перерисовок (`Zero-Flicker Polling`)**: функции `areDevicesEqual()` и `areJobsEqual()` в [`App.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/App.tsx) проверяют поля объектов при каждом 8-секундном цикле опроса. Если данные не изменились, ссылка на массив сохраняется, и дочерние мемоизированные компоненты (`TopologyMap`, `Dashboard`) не перерисовываются.
2. **Изоляция тяжёлых чанков**: 13 вторичных экранов и модальных окон загружаются лениво через `React.lazy()`. Начальный экран NOC загружает только каркас и `Dashboard`.
3. **Семантические CSS-токены**: переключение темы (`dark` / `light`) мгновенно меняет CSS-переменные на корневом `<html>` без пересоздания дерева компонентов.

---

## 3. Структура каталогов

Все 58 исходных файлов в `frontend/src/` строго распределены по функциональным слоям и модулям:

```
frontend/
├── index.html                  # Точка входа HTML, шрифты Inter + JetBrains Mono
├── package.json                # Зависимости и скрипты сборки
├── tsconfig.app.json           # Строгий конфиг TypeScript (strict: true)
├── vite.config.ts              # Конфиг сборщика Vite, manualChunks и dev-proxy
├── README.md                   # Этот документ (руководство по модулям фронтенда)
├── ARCHITECTURE.md             # Диаграммы последовательностей (Mermaid) и матрица хуков
├── tests/
│   └── audit_all_views_e2e.py  # Сквозной Playwright E2E-аудит всех вкладок и модалок
└── src/
    ├── main.tsx                # Монтирование React 19 createRoot в DOM
    ├── App.tsx                 # Корневой оркестратор состояния, роутинга вкладок и модальных окон
    ├── api.ts                  # Реэкспорт модульного API-клиента (./api/*)
    ├── api/                    # Модульный типизированный HTTP/SSE клиент и доменные типы
    │   ├── client.ts           # Класс NetOpsApiClient и точка входа api
    │   ├── http.ts             # Низкоуровневая обёртка fetch с таймаутами и обработкой ошибок
    │   ├── auth.ts             # Управление токенами, сессионными cookies, CSRF и Client ID
    │   ├── runtimeConfig.ts    # Конфигурация ролевых портов консолей и URL
    │   ├── standsApi.ts        # Методы управления мульти-стендами (/api/v1/stands/*)
    │   ├── deviceJobApi.ts     # Методы CRUD устройств, пайплайнов и стриминга задач
    │   ├── types.ts            # Базовые интерфейсы домена (Device, Job, Forecast, Intent)
    │   └── domainTypes.ts      # Расширенные типы (Stands, Bootstrap, Users, Collectors)
    ├── hooks/                  # Выделенные React-хуки
    │   ├── useNetopsPolling.ts # Адаптивный пуллинг (1.5s/5s) с защитой от лишних перерисовок
    │   └── useKeyboardShortcuts.ts # Глобальные клавиатурные сокращения и стек закрытия по Esc
    ├── locales/                # Двуязычные словари локализации
    │   ├── ru.ts               # Русский словарь интерфейса
    │   └── en.ts               # Английский словарь интерфейса
    ├── constants.ts            # Словари платформ, ролей узлов и метаданные навигации
    ├── i18n.ts                 # Точка входа интернационализации (RU / EN)
    ├── index.css               # Семантические CSS-переменные тем (Dark / Light) и утилиты
    └── components/
        ├── layout/             # Каркас приложения и атомарные UI-примитивы
        │   ├── Header.tsx
        │   ├── Sidebar.tsx
        │   ├── MobileNav.tsx
        │   ├── CommandPalette.tsx
        │   ├── AppOverlays.tsx
        │   ├── ToastNotification.tsx
        │   └── ui.tsx
        ├── views/              # Основные полноэкранные рабочие области и их подмодули
        │   ├── Dashboard.tsx (+ DashboardRightColumn.tsx)
        │   ├── DeviceList.tsx
        │   ├── DevicePage.tsx (+ device/DeviceOverviewSection.tsx, DeviceForecastTab.tsx, DeviceInterfacesAndDanger.tsx, DeviceTerminalAndCopilot.tsx, DeviceTableRow.tsx)
        │   ├── DiffViewer.tsx (+ DiffViewerFallbacks.tsx)
        │   ├── DriftView.tsx
        │   ├── IntentEditor.tsx
        │   ├── JobsView.tsx
        │   ├── ChaosLabView.tsx
        │   └── EmergencyHub.tsx (+ EmergencyAuditSection.tsx)
        ├── modals/             # Модальные окна, визарды и боковые панели
        │   ├── WelcomeModal.tsx (+ welcome/WelcomeSteps1To3.tsx, WelcomeSteps4And5.tsx, WelcomeStepStyles.ts)
        │   ├── ShareModal.tsx (+ share/ShareUsersTable.tsx, ShareStaticViewer.tsx, ShareIntegrationsTab.tsx, PublicConsolesBanner.tsx)
        │   ├── StandsModal.tsx
        │   ├── AccessCodeModal.tsx
        │   ├── DryRunModal.tsx
        │   ├── AddDeviceModal.tsx (+ add_device/AddDeviceStep2.tsx)
        │   ├── DeviceDrawer.tsx (+ device_drawer/DeviceDrawerMetricsTab.tsx)
        │   ├── DeviceDetailModal.tsx
        │   ├── CopilotPanel.tsx
        │   └── ShortcutsModal.tsx
        └── charts/             # Графы топологии L1–L3, коммутация и графики прогнозов
            ├── TopologyMap.tsx (+ topologyLayout.ts, TopologyToolbar.tsx, TopologyNodePopover.tsx, TopologyCommutationPanel.tsx, TopologyCheckpointBar.tsx)
            ├── TopologyMacroFleetView.tsx
            └── ForecastChart.tsx
```

---

## 4. Подробное описание каждого модуля

### 4.1. Ядро приложения (`src/`, `src/api/`, `src/hooks/`, `src/locales/`)

| Файл / Пакет | За что отвечает и как работает |
|------|--------------------------------|
| [`src/main.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/main.tsx) | Точка инициализации React 19 (`createRoot`). Оборачивает `<App />` в `<StrictMode>` и подключает глобальную таблицу стилей `index.css`. |
| [`src/App.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/App.tsx) | Главный контроллер состояния: хранит активную вкладку (`activeTab`), список стендов (`stands`), активный стенд (`activeStandSlug`), устройства (`devices`), задачи (`jobs`), статус аварийной заморозки (`Factory Freeze`), тему (`dark`/`light`), язык (`ru`/`en`) и текущую роль пользователя (`viewer`/`operator`/`admin`/`owner`). |
| [`src/api/*`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/api/client.ts) | Модульный слой взаимодействия с бэкендом (`/api/v1/*`): `client.ts` агрегирует методы `standsApi.ts`, `deviceJobApi.ts`, `auth.ts` и `http.ts`; строгие контракты разделены между `types.ts` и `domainTypes.ts`. |
| [`src/hooks/*`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/hooks/useNetopsPolling.ts) | `useNetopsPolling.ts` реализует адаптивный опрос бэкенда с глубоким сравнением ссылок (`areDevicesEqual`, `areJobsEqual`); `useKeyboardShortcuts.ts` управляет хоткеями и приоритетным стеком `Escape`. |
| [`src/constants.ts`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/constants.ts) | Единый источник констант отображения: человекочитаемые названия платформ (`PLATFORM_NAMES`: Cisco IOS-XE, Arista EOS, Huawei VRP, Juniper Junos, Eltex MES/ESR, YADRO KORNFE, Linux Server), ролей устройств (`ROLE_NAMES`) и вкладок (`TAB_DEFINITIONS`). |
| [`src/i18n.ts`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/i18n.ts) и `src/locales/*` | Двуязычный словарь интерфейса (`locales/ru.ts` и `locales/en.ts`). Обеспечивает мгновенное переключение языка во всех компонентах без перезагрузки страницы. |
| [`src/index.css`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/index.css) | Определяет CSS-переменные цветовой палитры для `:root` (Dark Mode по умолчанию) и `html.theme-light` (Light Mode), а также переопределения контрастности по стандартам Vercel Geist и Yandex Gravity UI. |

---

### 4.2. Каркас и атомарная дизайн-система (`src/components/layout/`)

Модули этого каталога формируют постоянную оболочку интерфейса и предоставляют переиспользуемые визуальные блоки.

| Модуль | За что отвечает | Ключевые возможности |
|--------|-----------------|----------------------|
| [`Header.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/layout/Header.tsx) | Верхняя панель управления NOC | Отображает пульс фабрики, селектор активного стенда и кнопку `StandsModal`, переключатель контура (`ONLINE SSH` / `OFFLINE LAB`), `Factory Freeze`, кнопки `Dry-Run`, `Share/Access`, `AI Copilot`, переключатель языка (`RU`/`EN`), темы (`Dark`/`Light`) и текущей роли оператора. |
| [`Sidebar.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/layout/Sidebar.tsx) | Левая панель навигации | Группирует экраны по доменам («Мониторинг», «Автоматизация», «Надёжность»), показывает счётчики активных дрейфов и задач, адаптирует вкладки в соответствии с ролью (`RBAC`) и позволяет сворачивать панель в компактный режим. |
| [`MobileNav.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/layout/MobileNav.tsx) | Мобильная навигация | Обеспечивает адаптивную работу на планшетах и смартфонах дежурных инженеров: нижний док быстрых вкладок и выдвижное бургер-меню. |
| [`CommandPalette.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/layout/CommandPalette.tsx) | Глобальная палитра команд (`Ctrl+K`) | Мгновенный поиск по узлам фабрики (по hostname, IP, платформе, роли), быстрый переход между экранами и запуск операций с клавиатуры. |
| [`AppOverlays.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/layout/AppOverlays.tsx) и [`ToastNotification.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/layout/ToastNotification.tsx) | Оверлеи и уведомления | Оркестрирует все лениво подгружаемые модальные окна (`Suspense`) и всплывающие тост-уведомления. |
| [`ui.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/layout/ui.tsx) | Атомарная библиотека компонентов | Содержит `BentoCard`, `StatusBadge`, `OperStatusBadge`, `IntentStatusBadge`, `JobStatusBadge`, `Sparkline`, `StateTimeline`, `Modal` и др. |

---

### 4.3. Рабочие экраны и вкладки (`src/components/views/`)

Каждый файл в `views/` представляет собой самостоятельную рабочую область, открываемую при выборе соответствующего раздела в `Sidebar` или `CommandPalette`.

| Модуль | Вкладка | За что отвечает и как работает |
|--------|---------|--------------------------------|
| [`Dashboard.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/views/Dashboard.tsx) (+ `DashboardRightColumn.tsx`) | `dashboard` | Главный экран NOC в стиле Bento Grid. Объединяет: (1) KPI-карточки здоровья фабрики, (2) интерактивную карту топологии `TopologyMap`, (3) панель `AI Pre-Flight Guard`, (4) радар прогнозов Statistical Forecast, (5) ленту последних пайплайнов и быстрый запуск `Golden Baseline` / `Remediate All`. |
| [`DeviceList.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/views/DeviceList.tsx) (+ `device/DeviceTableRow.tsx`) | `devices` | Табличный реестр всех сетевых устройств и серверов фабрики. Поддерживает фильтрацию по статусу/платформе/роли, индикацию живучести (`UP`/`DOWN` + резервный бэкап), точечный `Ping/SSH Probe`, синхронизацию `inventory.yaml` и переход в глубокую диагностику узла. |
| [`DevicePage.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/views/DevicePage.tsx) (+ `device/*`) | `device-detail` | Полноэкранный диагностический паспорт узла, разбитый на модули: `DeviceOverviewSection` (паспорт, живучесть, ИИ-диагноз сбоя и восстановление из бэкапа), `DeviceForecastTab` (симулятор What-If и прогноз), `DeviceInterfacesAndDanger` (интерфейсы, соседи BGP и опасная зона), `DeviceTerminalAndCopilot` (живой SSH CLI и контекстный ИИ). |
| [`IntentEditor.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/views/IntentEditor.tsx) | `intent` | Интерактивный редактор YAML-намерений (`intent/devices/*.yaml`) с мгновенной проверкой Pre-Flight Lint и запуском Dry-Run. |
| [`DiffViewer.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/views/DiffViewer.tsx) (+ `DiffViewerFallbacks.tsx`) | `diff` | Двухпанельный инспектор конфигурационных различий (`running` vs `candidate`) на базе Monaco Editor с легковесным фоллбэком и вызовом LLM-анализатора (`POST /jobs/{id}/explain`). |
| [`DriftView.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/views/DriftView.tsx) | `drift` | Центр контроля конфигурационного дрейфа. Показывает какие узлы отклонились от эталонного Git Source of Truth, визуализирует diff-сниппеты и позволяет запустить точечное или массовое авто-восстановление (`Remediate`). |
| [`JobsView.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/views/JobsView.tsx) | `jobs` | Журнал оркестрации пайплайнов (`DRY_RUN`, `DEPLOY`, `DRIFT_SCAN`, `DRIFT_REMEDIATE`) с SSE-стримингом живых логов по шагам и экспортом отчёта соответствия в Markdown. |
| [`ChaosLabView.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/views/ChaosLabView.tsx) | `chaos` | Стенд Chaos Engineering для проверки устойчивости сети (инъекция несанкционированного CLI-дрейфа, падение BGP-сессии, перегрузка аплинка и сброс к эталону). |
| [`EmergencyHub.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/views/EmergencyHub.tsx) (+ `EmergencyAuditSection.tsx`) | `emergency` | Аварийный пульт управления (`Out-of-Band Console`), доступный только ролям `admin` и `owner`: прямой мульти-вендорный CLI-терминал, экстренный патч, двухступенчатый сброс стенда (**Soft Reset** и **Hard Reset**) и неизменяемый журнал аудита. |

---

### 4.4. Модальные окна, дроверы и визарды (`src/components/modals/`)

Модули из `modals/` открываются поверх текущего экрана для выполнения целевых сценариев настройки, проверки и управления доступом.

| Модуль | За что отвечает и как работает |
|--------|--------------------------------|
| [`WelcomeModal.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/modals/WelcomeModal.tsx) (+ `welcome/*`) | **5-слойный визард первичной инициализации («Матрёшка»)**: (1) Владелец и Telegram-бот, (2) Публикация и AI-каскад, (3) Политики и тайминги, (4) GitHub Source of Truth (`PAT` + `Webhook`), (5) Сервер и авто-обнаружение узлов Containerlab по SSH. |
| [`ShareModal.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/modals/ShareModal.tsx) (+ `share/*`) | **Центр управления доступом (RBAC & IAM) и интеграциями**: включает блок многопользовательского статического QR-доступа для жюри (`ShareStaticViewer.tsx`), таблицу персональных токенов с изоляцией стендов (`ShareUsersTable.tsx`), баннер ролевых консолей (`PublicConsolesBanner.tsx`) и вкладку интеграций (`ShareIntegrationsTab.tsx`). |
| [`StandsModal.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/modals/StandsModal.tsx) | **Менеджер лабораторных и боевых стендов (`Multi-Stand Hub`)**. Позволяет добавлять новые стенды с зашифрованными паролями Jump-хостов, переключать активный стенд, запускать SSH-обнаружение узлов (`Discover`), сохранять резервные копии (`Backup`) и синхронизировать все стенды с диском. |
| [`AccessCodeModal.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/modals/AccessCodeModal.tsx) | **Экран входа для внешних участников и жюри**. Принимает персональные токены (`crk_*`), статические токены жюри (`inv_*`) или 6-значные коды доступа. |
| [`DryRunModal.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/modals/DryRunModal.tsx) | **Модальное окно Pre-Flight Dry-Run (`Plan`)**. Позволяет выбрать целевые устройства, оценить Blast Radius и запустить расчёт диффа без записи в сеть. |
| [`AddDeviceModal.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/modals/AddDeviceModal.tsx) (+ `add_device/*`) | **Двухшаговый визард регистрации нового устройства**. Выполняет живое зондирование (`POST /devices/probe`) IP-адреса и SSH-порта, определяет платформу/характеристики и добавляет узел в выбранный стенд. |
| [`DeviceDrawer.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/modals/DeviceDrawer.tsx) (+ `device_drawer/*`) | **Боковая шторка быстрой инспекции узла**. Открывается при клике по узлу без ухода с главного экрана: показывает метрики, статус живучести, интерфейсы, соседей BGP и текущий конфиг. |
| [`DeviceDetailModal.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/modals/DeviceDetailModal.tsx) | **Инспектор декларативного YAML-интента узла** с быстрым просмотром интерфейсов и BGP-соседей. |
| [`CopilotPanel.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/modals/CopilotPanel.tsx) | **Боковая панель ИИ-ассистента (`NetOps Copilot`)** с контекстом активного экрана/стенда и кнопками действий в 1 клик (`Action Chips`). |
| [`ShortcutsModal.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/modals/ShortcutsModal.tsx) | **Справочник горячих клавиш (`?`)** и матрица ролевых прав. |

---

### 4.5. Визуализация графа и прогнозов (`src/components/charts/`)

Модули визуализации отвечают за рендеринг топологии фабрики CLOS, L1–L3 коммутацию и предиктивную аналитику временных рядов.

| Модуль | За что отвечает и как работает |
|--------|--------------------------------|
| [`TopologyMap.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/charts/TopologyMap.tsx) (+ `topologyLayout.ts`, `TopologyToolbar.tsx`, `TopologyNodePopover.tsx`) | **Интерактивная карта топологии CLOS-фабрики (L1/L2/L3)**. Отрисовывает иерархический SVG-граф (`Border` ↔ `Spine` ↔ `Leaf` ↔ `Server`), анимирует загрузку линков, подсвечивает точки отказа (SPOF) и позволяет моделировать падение узлов (`What-If Failure Simulation`). |
| [`TopologyCommutationPanel.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/charts/TopologyCommutationPanel.tsx) и [`TopologyCheckpointBar.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/charts/TopologyCheckpointBar.tsx) | **Панель L1–L3 коммутации и контрольных точек топологии**. Позволяет соединять/переключать порты устройств, настраивать подсети/VLAN/BGP на линках и сохранять/откатывать чекпоинты топологии. |
| [`TopologyMacroFleetView.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/charts/TopologyMacroFleetView.tsx) | **Макро-обзор мульти-подовой фабрики (`Macro Fleet View`)** для масштабирования на десятки и сотни стендов с агрегацией здоровья каждого пода. |
| [`ForecastChart.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/components/charts/ForecastChart.tsx) | **График предиктивной аналитики Statistical Forecast**. Отрисовывает исторический ряд метрики (`cpu_pct` или `uplink_util_pct`), горизонт прогноза, коридор доверительного интервала (`p10`–`p90`), порог SLA и маркеры сетевых событий (`DEPLOY`, `DRIFT_DETECTED`, `REMEDIATE`, `CHAOS`). |

---

## 5. Дизайн-система, темы (`Dark` / `Light`) и локализация (`RU` / `EN`)

Платформа построена по стандартам корпоративных систем мониторинга (**Vercel Geist / Datadog / Yandex Gravity UI**):

1. **Две полноценные темы оформления (`Dark` и `Light`)**:
   - **Dark Mode (`:root`)**: глубокий обсидиановый фон `#050507`, карточки `#0c0d12`, приподнятые поверхности `#12141c`, текст `#f4f4f5`, акценты `cyan-400` / `emerald-400`.
   - **Light Mode (`html.theme-light`)**: чистый инженерный светлый фон `#f8fafc`, карточки `#ffffff`, поверхности `#f1f5f9`, высококонтрастный текст `#0f172a` (Slate-900).
   - Выбранная тема сохраняется в `localStorage['netops_theme']` и доступна для переключения как в `Header`, так и на всех шагах начального визарда `WelcomeModal`.
2. **Мгновенная двуязычность (`RU` / `EN`)**:
   - Переключатель языка в `Header` и в `WelcomeModal` сохраняет выбор в `localStorage['netops_locale']` и реактивно переводит все подписи, статусы и подсказки.

---

## 6. Матрица ролевого доступа (RBAC во фронтенде)

Фронтенд синхронизирует текущую роль из JWT-токена (`GET /api/v1/users/me`) и адаптирует доступные действия:

| Действие / Экран | `viewer` (Наблюдатель) | `operator` (Инженер NOC) | `admin` (Администратор) | `owner` (Владелец платформы) |
|------------------|:----------------------:|:------------------------:|:-----------------------:|:----------------------------:|
| Просмотр дашборда, топологии, Diff, задач и прогнозов | ✅ | ✅ | ✅ | ✅ |
| Запуск `Dry-Run (Plan)` и ИИ-объяснения рисков | ✅ | ✅ | ✅ | ✅ |
| Выкатка изменений (`Apply`), `Rollback`, `Remediate` | ❌ | ✅ | ✅ | ✅ |
| Добавление и редактирование узлов (`AddDeviceModal`) | ❌ | ✅ | ✅ | ✅ |
| Инъекция сбоев в `Chaos Lab` | ❌ | ✅ | ✅ | ✅ |
| Аварийный пульт `Emergency Hub` (прямой CLI, Soft/Hard Reset, Freeze) | ❌ | ❌ | ✅ | ✅ |
| Управление ролями, выпуск токенов и визард «Матрёшка» (`ShareModal`, `WelcomeModal`) | ❌ | ❌ | ✅ (чтение/токены) | ✅ (полный контроль) |

---

## 7. Горячие клавиши NOC-оператора

Для скоростной работы дежурной смены без использования мыши в [`App.tsx`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/frontend/src/App.tsx) реализованы глобальные хоткеи (отключаются, когда фокус находится в `<input>` или `<textarea>`):

| Комбинация | Действие |
|------------|----------|
| `Ctrl + K` / `Cmd + K` | Открыть глобальную палитру команд (`CommandPalette`) |
| `?` | Открыть шпаргалку по горячим клавишам (`ShortcutsModal`) |
| `Esc` | Закрыть активное модальное окно, дровер или палитру |
| `g` затем `d` | Перейти на **Главный дашборд (`Dashboard`)** |
| `g` затем `n` | Перейти в **Реестр устройств (`DeviceList`)** |
| `g` затем `j` | Перейти в **Историю задач (`JobsView`)** |
| `g` затем `c` | Перейти в **Инспектор изменений (`DiffViewer`)** |
| `g` затем `f` | Перейти в **Контроль дрейфа (`DriftView`)** |
| `g` затем `s` | Перейти в **Chaos Lab (`ChaosLabView`)** |
| `g` затем `e` | Перейти в **Аварийный пульт (`EmergencyHub`)** |

---

## 8. Проверка качества, сборка и E2E-тесты

Перед каждым коммитом во фронтенд выполняйте три быстрые проверки:

```bash
# 1. Строгая проверка типов TypeScript (0 any) и продакшн-сборка чанков
npm --prefix frontend run build

# 2. Сверхбыстрый линтер Oxlint (проверка правил React Hooks, неиспользуемых переменных и типизации)
npx --prefix frontend oxlint frontend/src/

# 3. Сквозной E2E-аудит всех вкладок и модальных окон через Playwright (при запущенных серверах)
backend/.venv/bin/python frontend/tests/audit_all_views_e2e.py
```
