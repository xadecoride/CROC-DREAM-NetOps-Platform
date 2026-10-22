# Инженерные скрипты и диагностика (`scripts/`)

Каталог `scripts/` содержит CLI-утилиты для самодиагностики окружения, проверки живого стенда Containerlab и демонстрационной автоматизации.

---

## Список утилит

| Скрипт | За что отвечает и как запускать |
|--------|---------------------------------|
| [`doctor.py`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/scripts/doctor.py) | **Комплексная самодиагностика рабочего места инженера.** Проверяет версии Python/Node, наличие зависимостей, целостность `intent/` и `templates/`, доступность портов API/Frontend и сетевую связность со стендом.<br>Запуск: `python3 scripts/doctor.py` |
| [`verify_live_lab.py`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/scripts/verify_live_lab.py) | **Проверка доступности живого стенда Containerlab по SSH.** Опрашивает все 4 узла (`spine-1`, `spine-2`, `leaf-1`, `leaf-2`), проверяет авторизацию и снимает краткий статус BGP/интерфейсов.<br>Запуск: `python3 scripts/verify_live_lab.py` |
| [`reset_lab.py`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/scripts/reset_lab.py) | **Кроссплатформенный сброс стенда к Golden Baseline.** Восстанавливает эталонные конфигурации как в локальном `lab/running/`, так и на удалённом стенде.<br>Запуск: `python3 scripts/reset_lab.py` |
| [`automate_netops.py`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/scripts/automate_netops.py) | **Сквозной демонстрационный сценарий (CLI-оркестратор).** Проводит полный цикл `Lint → Dry-Run → Diff → Deploy → Drift Scan` через REST API платформы с выводом шагов в консоль.<br>Запуск: `python3 scripts/automate_netops.py` |

> Для smoke-тестирования Docker-стека в CI/CD используется скрипт [`backend/scripts/smoke_stack.py`](file:///home/tarhes/%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D1%8B/app/CROC-DREAM-NetOps-Platform/backend/scripts/smoke_stack.py).
