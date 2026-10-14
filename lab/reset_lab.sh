#!/usr/bin/env bash
set -u

START_TIME=$(date +%s)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "${SCRIPT_DIR}/golden_configs" ]; then
  CONFIG_DIR="${SCRIPT_DIR}/golden_configs"
elif [ -d "${SCRIPT_DIR}/lab/golden_configs" ]; then
  CONFIG_DIR="${SCRIPT_DIR}/lab/golden_configs"
else
  CONFIG_DIR="${SCRIPT_DIR}/golden_configs"
fi

# --- 1. Поиск топологии ---
SELECTED_TOPO=""
if [ $# -ge 1 ] && [ -f "$1" ]; then
  SELECTED_TOPO="$1"
else
  shopt -s nullglob
  TOPO_FILES=(*.clab.yaml *.clab.yml ../*.clab.yaml ../*.clab.yml)
  [ ${#TOPO_FILES[@]} -eq 0 ] && TOPO_FILES=(*.yaml *.yml)
  shopt -u nullglob

  if [ ${#TOPO_FILES[@]} -eq 0 ]; then
    echo "[-] Ошибка: Файл топологии (*.clab.yaml) не найден!"
    exit 1
  elif [ ${#TOPO_FILES[@]} -eq 1 ]; then
    SELECTED_TOPO="${TOPO_FILES[0]}"
  else
    echo "Найдено несколько топологий. Выберите нужную:"
    select topo in "${TOPO_FILES[@]}"; do
      if [ -n "${topo:-}" ]; then
        SELECTED_TOPO="$topo"
        break
      fi
    done
  fi
fi

echo "=============================================="
echo " [*] Lab Fast Reset started (Target: <= 10s)"
echo " [*] Использована топология: ${SELECTED_TOPO}"
echo " [*] Папка golden_configs:   ${CONFIG_DIR}"
echo "=============================================="

PARSE_OUTPUT=$(python3 -c '
import sys, yaml
try:
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    print("LAB_NAME=" + str(data.get("name", "")))
    nodes = data.get("topology", {}).get("nodes", {})
    for node, info in nodes.items():
        kind = info.get("kind", "") if isinstance(info, dict) else ""
        print("NODE=" + str(node) + ":" + str(kind))
except Exception as e:
    sys.exit(1)
' "${SELECTED_TOPO}")

LAB_NAME=$(echo "${PARSE_OUTPUT}" | grep '^LAB_NAME=' | cut -d'=' -f2)

if [ -z "${LAB_NAME}" ]; then
  echo "[-] Не удалось определить имя лабы из ${SELECTED_TOPO}"
  exit 1
fi

AUTH_MAP=$(python3 -c '
import os, yaml
inv_file = next((f for f in ["./intent/inventory.yaml", "../intent/inventory.yaml", "./inventory.yaml"] if os.path.exists(f)), None)
if inv_file:
    with open(inv_file, "r", encoding="utf-8") as f:
        inv = yaml.safe_load(f) or {}
    profiles = inv.get("auth_profiles", {})
    for d in inv.get("devices", []):
        h = d.get("hostname", "").split(".")[0].replace("-", "").replace("_", "").lower()
        prof = profiles.get(d.get("auth_profile", ""), {})
        u = prof.get("username", "admin")
        p = prof.get("password", "")
        print(f"AUTH_{h}={u}:{p}")
' 2>/dev/null)

get_node_credentials() {
  local node="$1"
  local norm_name=$(echo "${node}" | tr -d '_-' | tr '[:upper:]' '[:lower:]')
  local creds=$(echo "${AUTH_MAP}" | grep "^AUTH_${norm_name}=" | head -n1 | cut -d'=' -f2-)
  local user=$(echo "${creds}" | cut -d':' -f1)
  local pass=$(echo "${creds}" | cut -d':' -f2-)
  [ -z "${user}" ] && user="admin"
  [ -z "${pass}" ] && pass="F5ga6jfkig1"
  echo "${user}:${pass}"
}

# --- 2. Функции восстановления ---

# Arista cEOS
reset_arista() {
  local node="$1"
  local cfg_file="$2"
  local container="clab-${LAB_NAME}-${node}"
  local full_cfg="${CONFIG_DIR}/${cfg_file}"

  if docker inspect "${container}" > /dev/null 2>&1; then
    docker cp "${full_cfg}" "${container}:/mnt/flash/startup-config"
    docker exec "${container}" Cli -p 15 -c "configure replace flash:startup-config force" > /dev/null 2>&1
    docker exec "${container}" Cli -p 15 -c "write memory" > /dev/null 2>&1
    echo "[+] Arista ${node}: восстановлена из ${cfg_file}"
  fi
}

# Huawei CE12800 (VRP)
reset_huawei() {
  local node="$1"
  local cfg_file="$2"
  local container="clab-${LAB_NAME}-${node}"
  local full_cfg="${CONFIG_DIR}/${cfg_file}"
  local creds=$(get_node_credentials "${node}")
  local user=$(echo "$creds" | cut -d':' -f1)
  local pass=$(echo "$creds" | cut -d':' -f2)

  if docker inspect "${container}" > /dev/null 2>&1; then
    docker exec -i "${container}" python3 -W ignore -c '
import telnetlib, sys, time

user = sys.argv[1]
pwd = sys.argv[2]
node_name = sys.argv[3]
cfg_name = sys.argv[4]

lines = [l.strip() for l in sys.stdin.read().splitlines() if l.strip()]

target_as = None
for l in lines:
    if l.startswith("bgp "):
        target_as = l.split()[1]
        break

tn = telnetlib.Telnet("127.0.0.1", 5000, timeout=10)
tn.write(b"\r\n\r\n")
time.sleep(0.3)
tn.read_very_eager()

def send_cmd(cmd, wait=0.04):
    tn.write(cmd.encode("utf-8") + b"\r\n")
    time.sleep(wait)
    out = tn.read_very_eager().decode("utf-8", errors="ignore")
    if any(q in out for q in ["[Y/N]", "(y/n)", "[Y/N/C]", "Continue?"]):
        tn.write(b"y\r\n")
        time.sleep(0.05)
        tn.read_very_eager()
    return out

send_cmd("return", wait=0.1)
send_cmd("screen-length 0 temporary", wait=0.1)
send_cmd("system-view", wait=0.1)

# 1. AAA и SSH
send_cmd("aaa")
send_cmd(f"local-user {user} password cipher {pwd}")
send_cmd(f"local-user {user} privilege level 15")
send_cmd(f"local-user {user} service-type ssh")
send_cmd("quit")
send_cmd(f"ssh user {user} authentication-type password")
send_cmd(f"ssh user {user} service-type stelnet")
send_cmd("stelnet server enable")
send_cmd("ssh authentication-type default password")
send_cmd("user-interface vty 0 4")
send_cmd("authentication-mode aaa")
send_cmd("protocol inbound all")
send_cmd("quit")

# 2. Очистка паразитных ACL
send_cmd("undo acl name CHAOS_VRP_ACL")

# 3. Полный пересоздание BGP для чистоты от старых пиров
tn.write(b"display current-configuration | include ^bgp \r\n")
time.sleep(0.3)
bgp_out = tn.read_very_eager().decode("utf-8", errors="ignore")
for l in bgp_out.splitlines():
    if l.strip().startswith("bgp "):
        running_as = l.strip().split()[1]
        tn.write(f"undo bgp {running_as}\r\n".encode("utf-8"))
        time.sleep(0.3)
        tn.write(b"y\r\n")
        time.sleep(0.3)
        tn.write(b"commit\r\n")
        time.sleep(0.8)
        tn.read_very_eager()

in_block = False
in_bgp = False

for i, line in enumerate(lines):
    if line.startswith("!") or line.startswith("device board"):
        continue

    if line == "#":
        next_cmd = ""
        for j in range(i + 1, len(lines)):
            nl = lines[j]
            if nl and not nl.startswith("!") and nl != "#":
                next_cmd = nl
                break
        if next_cmd.startswith("ipv4-family"):
            continue

        if in_bgp:
            send_cmd("return", wait=0.08)
            send_cmd("system-view", wait=0.08)
            in_bgp = False
            in_block = False
        elif in_block:
            send_cmd("quit")
            in_block = False
        continue

    if line.startswith("bgp "):
        in_bgp = True
        in_block = True

    if line.startswith("interface "):
        if in_block:
            send_cmd("quit")
        in_block = True
        send_cmd(line)
        send_cmd("undo description")
        send_cmd("undo ip address")
        if not line.startswith("interface LoopBack"):
            send_cmd("undo shutdown")
        continue

    if line.startswith("acl name "):
        if in_block:
            send_cmd("quit")
        acl_id = line.split()[2]
        send_cmd(f"undo acl name {acl_id}")
        in_block = True

    elif any(line.startswith(prefix) for prefix in ["acl ", "route-policy "]):
        if in_block:
            send_cmd("quit")
        in_block = True

    send_cmd(line)

send_cmd("commit", wait=1.0)
send_cmd("return", wait=0.1)
send_cmd("save", wait=0.5)
tn.write(b"\r\n")
time.sleep(1.0)
tn.close()
print(f"[+] Huawei {node_name}: восстановлен из {cfg_name} (user: {user})")
' "${user}" "${pass}" "${node}" "${cfg_file}" < "${full_cfg}"
  fi
}

# Cisco c8000v (IOS-XE)
reset_cisco() {
  local node="$1"
  local cfg_file="$2"
  local full_cfg="${CONFIG_DIR}/${cfg_file}"
  local container="clab-${LAB_NAME}-${node}"
  local creds=$(get_node_credentials "${node}")
  local user=$(echo "$creds" | cut -d':' -f1)
  local pass=$(echo "$creds" | cut -d':' -f2)

  if docker inspect "${container}" > /dev/null 2>&1; then
    docker exec -i "${container}" python3 -W ignore -c '
import telnetlib, sys, time

user = sys.argv[1]
pwd = sys.argv[2]
node_name = sys.argv[3]
cfg_name = sys.argv[4]

cfg_lines = [l.strip() for l in sys.stdin.read().splitlines() if l.strip()]

tn = telnetlib.Telnet("127.0.0.1", 5000, timeout=10)
tn.write(b"\r\n\r\n")
time.sleep(0.5)

for _ in range(5):
    idx, match, text = tn.expect([br"[Uu]sername:", br"[Pp]assword:", br"#", br">"], timeout=2)
    if idx == 0:
        tn.write(f"{user}\r\n".encode("utf-8"))
    elif idx == 1:
        tn.write(f"{pwd}\r\n".encode("utf-8"))
    elif idx == 2:
        break
    elif idx == 3:
        tn.write(b"enable\r\n")
        idx2, _, _ = tn.expect([br"[Pp]assword:", br"#"], timeout=2)
        if idx2 == 0:
            tn.write(f"{pwd}\r\n".encode("utf-8"))
        break

tn.write(b"\r\nterminal length 0\r\nconf t\r\n")
time.sleep(0.4)
tn.write(f"username {user} privilege 15 password 0 {pwd}\r\nenable password {pwd}\r\n".encode("utf-8"))
tn.write(b"no ip access-list extended CHAOS_CISCO_ACL\r\n")

# Полный сброс BGP процесса перед чистой накатной заливкой
tn.write(b"no router bgp 65000\r\n")
tn.write(b"no router bgp 65001\r\n")
tn.write(b"no router bgp 65002\r\n")
time.sleep(0.3)

for line in cfg_lines:
    if line.startswith("!") or line.startswith("#") or line == "end" or line.startswith("Building") or line.startswith("Current"):
        continue
    if line.startswith("ip access-list extended "):
        tn.write(f"no {line}\r\n".encode("utf-8"))
        time.sleep(0.05)
    elif line.startswith("interface ") and not line.endswith("GigabitEthernet1"):
        tn.write(line.encode("utf-8") + b"\r\n")
        tn.write(b"no description\r\nno ip address\r\n")
        continue
    tn.write(line.encode("utf-8") + b"\r\n")
    if line == "exit-address-family":
        tn.write(b"exit\r\n")
    time.sleep(0.015)

tn.write(b"\r\nend\r\nwr\r\n")
time.sleep(2.0)
tn.close()
print(f"[+] Cisco {node_name}: восстановлена из {cfg_name}")
' "${user}" "${pass}" "${node}" "${cfg_file}" < "${full_cfg}"
  fi
}

# --- 3. Параллельный запуск ---
PIDS=()
while IFS= read -r entry; do
  [ -z "$entry" ] && continue
  node=$(echo "$entry" | cut -d':' -f1)
  kind=$(echo "$entry" | cut -d':' -f2)
  cfg_file="${node}.cfg"

  case "${kind,,}" in
    *arista*|*ceos*) reset_arista "${node}" "${cfg_file}" & PIDS+=($!) ;;
    *huawei*|*vrp*)  reset_huawei "${node}" "${cfg_file}" & PIDS+=($!) ;;
    *cisco*)         reset_cisco "${node}" "${cfg_file}" & PIDS+=($!) ;;
  esac
done < <(echo "${PARSE_OUTPUT}" | grep '^NODE=' | cut -d'=' -f2-)

for pid in "${PIDS[@]:-}"; do
  wait "$pid" 2>/dev/null || true
done

echo "[*] Синхронизация флеш-памяти коммутаторов (2 сек)..."
sleep 2

ELAPSED=$(( $(date +%s) - START_TIME ))
echo "=============================================="
echo "[+] Все устройства сброшены за ${ELAPSED}с"
echo "=============================================="
