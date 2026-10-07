#!/usr/bin/env bash
set -u

START_TIME=$(date +%s)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${SCRIPT_DIR}/golden_configs"

# --- 1. Поиск и выбор файла топологии ---
SELECTED_TOPO=""

if [ $# -ge 1 ] && [ -f "$1" ]; then
  SELECTED_TOPO="$1"
else
  shopt -s nullglob
  TOPO_FILES=(*.clab.yaml *.clab.yml)
  if [ ${#TOPO_FILES[@]} -eq 0 ]; then
    TOPO_FILES=(*.yaml *.yml)
  fi
  shopt -u nullglob

  if [ ${#TOPO_FILES[@]} -eq 0 ]; then
    echo "[-] Error: Topology file (*.clab.yaml) not found!"
    exit 1
  elif [ ${#TOPO_FILES[@]} -eq 1 ]; then
    SELECTED_TOPO="${TOPO_FILES[0]}"
  else
    echo "Found multiple topology files. Please select one:"
    select topo in "${TOPO_FILES[@]}"; do
      if [ -n "${topo:-}" ]; then
        SELECTED_TOPO="$topo"
        break
      else
        echo "Invalid selection. Enter number (1-${#TOPO_FILES[@]}):"
      fi
    done
  fi
fi

echo "=============================================="
echo " [*] Lab Fast Reset started (Target: <= 10s)"
echo " [*] Using topology: ${SELECTED_TOPO}"
echo "=============================================="

# --- 2. Парсинг имени лабы и нод ---
PARSE_OUTPUT=$(python3 -c '
import sys, re

fname = sys.argv[1]
name = ""
nodes = []

try:
    import yaml
    with open(fname) as f:
        data = yaml.safe_load(f)
    name = data.get("name", "")
    for node, info in data.get("topology", {}).get("nodes", {}).items():
        kind = info.get("kind", "") if isinstance(info, dict) else ""
        nodes.append((node, kind))
except Exception:
    in_nodes = False
    cur_node = None
    with open(fname) as f:
        for line in f:
            if line.startswith("name:") and not name:
                name = line.split(":", 1)[1].strip().strip("\"'\''")
            if re.match(r"^\s+nodes:", line):
                in_nodes = True
                continue
            if in_nodes:
                m_node = re.match(r"^\s{4}([a-zA-Z0-9_\-]+):", line)
                if m_node:
                    cur_node = m_node.group(1)
                elif cur_node and re.match(r"^\s+kind:", line):
                    kind = line.split(":", 1)[1].strip().strip("\"'\''")
                    nodes.append((cur_node, kind))
                    cur_node = None
                elif re.match(r"^\S", line) or re.match(r"^\s{2}[a-zA-Z]", line):
                    in_nodes = False

print(f"LAB_NAME={name}")
for n, k in nodes:
    print(f"NODE={n}:{k}")
' "${SELECTED_TOPO}")

LAB_NAME=$(echo "${PARSE_OUTPUT}" | grep '^LAB_NAME=' | cut -d'=' -f2)

if [ -z "${LAB_NAME}" ]; then
  echo "[-] Failed to detect lab name from ${SELECTED_TOPO}"
  exit 1
fi

# --- 3. Функции восстановления ---

# 1. Arista cEOS
reset_arista() {
  local node="$1"
  local cfg_file="$2"
  local container="clab-${LAB_NAME}-${node}"
  local full_cfg="${CONFIG_DIR}/${cfg_file}"

  if [ ! -f "${full_cfg}" ]; then
    echo "[-] Arista ${node}: ${cfg_file} not found in ${CONFIG_DIR}"
    return 1
  fi

  if docker inspect "${container}" > /dev/null 2>&1; then
    docker cp "${full_cfg}" "${container}:/mnt/flash/startup-config"
    docker exec "${container}" Cli -p 15 -c "configure replace flash:startup-config" > /dev/null 2>&1
    docker exec "${container}" Cli -p 15 -c "write memory" > /dev/null 2>&1
    echo "[+] Arista ${node}: restored from ${cfg_file}"
  else
    echo "[-] Arista ${node}: container ${container} not running"
  fi
}

# 2. Huawei CE12800 (VRP8)
reset_huawei() {
  local node="$1"
  local cfg_file="$2"
  local container="clab-${LAB_NAME}-${node}"
  local full_cfg="${CONFIG_DIR}/${cfg_file}"

  if [ ! -f "${full_cfg}" ]; then
    echo "[-] Huawei ${node}: ${cfg_file} not found in ${CONFIG_DIR}"
    return 1
  fi

  if docker inspect "${container}" > /dev/null 2>&1; then
    if docker exec -i "${container}" python3 -W ignore -c "
import telnetlib, sys, time

tn = telnetlib.Telnet('127.0.0.1', 5000, timeout=10)
tn.write(b'\r\nreturn\r\nn\r\nscreen-length 0 temporary\r\nsystem-view\r\n')
time.sleep(0.3)

for line in sys.stdin:
    cmd = line.strip()
    if not cmd or cmd.startswith('!'):
        continue
    if cmd == '#':
        tn.write(b'\r\nreturn\r\nsystem-view\r\n')
        continue
    if cmd.startswith('device board'):
        continue
    tn.write(cmd.encode('utf-8') + b'\r\n')
    time.sleep(0.012)

tn.write(b'\r\nsystem-view\r\ncommit\r\nreturn\r\nsave\r\ny\r\n')
time.sleep(1.5)
tn.close()
" < "${full_cfg}" 2>/tmp/"${node}".err; then
      echo "[+] Huawei ${node}: restored from ${cfg_file}"
    else
      echo "[-] Huawei ${node}: failed"
      [ -s /tmp/"${node}".err ] && sed 's/^/    /' /tmp/"${node}".err
    fi
  else
    echo "[-] Huawei ${node}: container ${container} not running"
  fi
}

# 3. Cisco C8000v / IOS-XE
reset_cisco() {
  local node="$1"
  local cfg_file="$2"
  local container="clab-${LAB_NAME}-${node}"
  local full_cfg="${CONFIG_DIR}/${cfg_file}"

  if [ ! -f "${full_cfg}" ]; then
    echo "[-] Cisco ${node}: ${cfg_file} not found in ${CONFIG_DIR}"
    return 1
  fi

  if docker inspect "${container}" > /dev/null 2>&1; then
    if docker exec -i "${container}" python3 -W ignore -c "
import telnetlib, sys, time, re

cfg_text = sys.stdin.read()

# Пробуем подключиться к telnet-консоли vrnetlab
tn = None
for _ in range(5):
    try:
        tn = telnetlib.Telnet('127.0.0.1', 5000, timeout=5)
        break
    except Exception:
        time.sleep(0.5)

if not tn:
    sys.stderr.write('Could not connect to telnet 127.0.0.1:5000\n')
    sys.exit(1)

# Будим консоль
tn.write(b'\r\n\r\n')
time.sleep(0.5)

# Проверяем состояние консоли (login/enable/#)
for _ in range(4):
    idx, match, text = tn.expect([br'[Uu]sername:', br'[Pp]assword:', br'#', br'>'], timeout=2)
    if idx == 0:
        # Если просит логин, берем первый попавшийся из конфига или пробуем admin/cisco
        u_match = re.search(r'username\s+(\S+)', cfg_text)
        user = u_match.group(1) if u_match else 'admin'
        tn.write(user.encode('utf-8') + b'\r\n')
    elif idx == 1:
        tn.write(b'admin\r\n')
    elif idx == 2:
        break
    elif idx == 3:
        tn.write(b'enable\r\n')
        idx2, _, _ = tn.expect([br'[Pp]assword:', br'#'], timeout=2)
        if idx2 == 0:
            tn.write(b'admin\r\n')
        break

tn.write(b'\r\nterminal length 0\r\nterminal width 0\r\nconfigure terminal\r\n')
time.sleep(0.3)

for line in cfg_text.splitlines():
    cmd = line.strip()
    if not cmd or cmd.startswith('!') or cmd.startswith('Building') or cmd.startswith('Current configuration') or cmd == 'end':
        continue
    # Отправляем все строки как есть, включая username и passwords
    tn.write(cmd.encode('utf-8') + b'\r\n')
    time.sleep(0.015)

# end возвращает в режим '#' из любой глубины (интерфейсы, роутеры и т.д.)
tn.write(b'\r\nend\r\nwrite memory\r\n')
time.sleep(1.5)
tn.close()
" < "${full_cfg}" 2>/tmp/"${node}".err; then
      echo "[+] Cisco ${node}: restored from ${cfg_file}"
    else
      echo "[-] Cisco ${node}: failed"
      [ -s /tmp/"${node}".err ] && sed 's/^/    /' /tmp/"${node}".err
    fi
  else
    echo "[-] Cisco ${node}: container ${container} not running"
  fi
}

# --- 4. Параллельный запуск восстановления ---
PIDS=()

while IFS= read -r entry; do
  [ -z "$entry" ] && continue
  node=$(echo "$entry" | cut -d':' -f1)
  kind=$(echo "$entry" | cut -d':' -f2)
  cfg_file="${node}.cfg"

  case "${kind,,}" in
    *arista*|*ceos*)
      reset_arista "${node}" "${cfg_file}" &
      PIDS+=($!)
      ;;
    *huawei*|*vrp*)
      reset_huawei "${node}" "${cfg_file}" &
      PIDS+=($!)
      ;;
    *cisco*)
      reset_cisco "${node}" "${cfg_file}" &
      PIDS+=($!)
      ;;
    *linux*|*alpine*)
      # Хосты пропускаем
      ;;
    *)
      echo "[?] Warning: Unknown kind '${kind}' for node '${node}', skipping"
      ;;
  esac
done < <(echo "${PARSE_OUTPUT}" | grep '^NODE=' | cut -d'=' -f2-)

for pid in "${PIDS[@]:-}"; do
  wait "$pid"
done

END_TIME=$(date +%s)
ELAPSED=$(( END_TIME - START_TIME ))
echo "=============================================="
echo "[+] All nodes processed in ${ELAPSED}s"
echo "=============================================="
