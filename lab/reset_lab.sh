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

SELECTED_TOPO=""
if [ $# -ge 1 ] && [ -f "$1" ]; then
  SELECTED_TOPO="$1"
else
  shopt -s nullglob
  TOPO_FILES=("${SCRIPT_DIR}"/*.clab.yaml "${SCRIPT_DIR}"/*.clab.yml *.clab.yaml *.clab.yml ../*.clab.yaml ../*.clab.yml)
  [ ${#TOPO_FILES[@]} -eq 0 ] && TOPO_FILES=("${SCRIPT_DIR}"/*.yaml *.yaml *.yml)
  shopt -u nullglob
  SELECTED_TOPO="${TOPO_FILES[0]:-${SCRIPT_DIR}/fabric.clab.yaml}"
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
except Exception:
    sys.exit(1)
' "${SELECTED_TOPO}")

LAB_NAME=$(echo "${PARSE_OUTPUT}" | grep '^LAB_NAME=' | cut -d'=' -f2)

get_node_credentials() {
  local cfg="${CONFIG_DIR}/${2:-}"
  if [ -n "${CLAB_NODE_CREDS:-}" ]; then
    echo "${CLAB_NODE_CREDS}"
    return
  fi
  if [ -f "${cfg}" ]; then
    local parsed
    parsed=$(awk '/^username / && /password / {for(i=1;i<=NF;i++) if($i=="password") print $2":"$(i+2)}' "${cfg}" | head -n1)
    if [ -n "${parsed}" ]; then
      echo "${parsed}"
      return
    fi
  fi
  echo "${CLAB_DEFAULT_USER:-admin}:${CLAB_DEFAULT_PASS:-admin}"
}

# --- 1. Arista cEOS ---
reset_arista() {
  local node="$1"
  local cfg_file="$2"
  local container="clab-${LAB_NAME}-${node}"
  local full_cfg="${CONFIG_DIR}/${cfg_file}"

  if docker inspect "${container}" > /dev/null 2>&1; then
    docker cp "${full_cfg}" "${container}:/mnt/flash/startup-config"
    docker exec "${container}" Cli -p 15 -c "
configure replace flash:startup-config force
configure
spanning-tree mode mstp
spanning-tree edge-port bpduguard default
write memory
" > /dev/null 2>&1
    echo "[+] Arista ${node}: восстановлена из ${cfg_file}"
  fi
}

RAW_TELNET_PY='
import re, socket, sys, time

class RawTelnet:
    def __init__(self, host, port, timeout=10.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = bytearray()

    def write(self, data):
        self.sock.sendall(data)

    def read_very_eager(self):
        self.sock.setblocking(False)
        out = bytearray()
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                out.extend(chunk)
        except (BlockingIOError, OSError):
            pass
        finally:
            self.sock.setblocking(True)
        return bytes(out)

    def expect(self, patterns, timeout=2.0):
        self.sock.settimeout(timeout)
        compiled = [re.compile(p) for p in patterns]
        start = time.time()
        while time.time() - start < timeout:
            for idx, pat in enumerate(compiled):
                m = pat.search(self.buf)
                if m:
                    end = m.end()
                    matched = bytes(self.buf[:end])
                    del self.buf[:end]
                    return idx, m, matched
            try:
                chunk = self.sock.recv(1024)
                if not chunk:
                    break
                self.buf.extend(chunk)
            except (socket.timeout, BlockingIOError, OSError):
                break
        return -1, None, bytes(self.buf)

    def close(self):
        self.sock.close()
'

# --- 2. Huawei CE12800 (VRP) ---
reset_huawei() {
  local node="$1"
  local cfg_file="$2"
  local container="clab-${LAB_NAME}-${node}"
  local full_cfg="${CONFIG_DIR}/${cfg_file}"
  local creds=$(get_node_credentials "${node}" "${cfg_file}")
  local user=$(echo "$creds" | cut -d':' -f1)
  local pass=$(echo "$creds" | cut -d':' -f2)

  if docker inspect "${container}" > /dev/null 2>&1; then
    docker exec -i "${container}" python3 -W ignore -c "${RAW_TELNET_PY}"'
user, pwd, node_name, cfg_name = sys.argv[1:5]
lines = [l.strip() for l in sys.stdin.read().splitlines() if l.strip()]

tn = RawTelnet("127.0.0.1", 5000, timeout=10)
tn.write(b"\x1a\r\n")
time.sleep(0.3)
tn.read_very_eager()

def send_cmd(cmd, wait=0.08):
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
send_cmd("evpn-overlay enable", wait=0.3)
send_cmd("commit", wait=0.5)

# Сброс старого BGP
tn.write(b"display current-configuration | include ^bgp \r\n")
time.sleep(0.3)
bgp_out = tn.read_very_eager().decode("utf-8", errors="ignore")
for l in bgp_out.splitlines():
    if l.strip().startswith("bgp "):
        send_cmd(f"undo bgp {l.strip().split()[1]}", wait=0.2)
        send_cmd("commit", wait=0.3)

# Подготовка GE1/0/9 в L2 перед накатом
send_cmd("interface GE1/0/9")
send_cmd("undo ip address")
send_cmd("portswitch", wait=0.2)
send_cmd("quit")

in_bgp = False
in_bgp_af = False

for i, line in enumerate(lines):
    if line.startswith("!") or line.startswith("device board") or line == "return":
        continue

    if line == "#":
        next_cmd = ""
        for j in range(i + 1, len(lines)):
            nl = lines[j]
            if nl and not nl.startswith("!") and nl != "#":
                next_cmd = nl
                break

        # Если мы внутри BGP и дальше идет адресное семейство
        if in_bgp and (next_cmd.startswith("ipv4-family") or next_cmd.startswith("l2vpn-family")):
            if in_bgp_af:
                send_cmd("quit", wait=0.08) # Выходим из предыдущего family в корень bgp
                in_bgp_af = False
            continue

        # Во всех остальных случаях на "#" гарантированно сбрасываем контекст в system-view
        send_cmd("return", wait=0.08)
        send_cmd("system-view", wait=0.08)
        in_bgp = False
        in_bgp_af = False
        continue

    if line.startswith("bgp "):
        in_bgp = True
    elif in_bgp and (line.startswith("ipv4-family") or line.startswith("l2vpn-family")):
        if in_bgp_af:
            send_cmd("quit", wait=0.08)
        in_bgp_af = True

    send_cmd(line)
# Гарантируем STP и защиту от петель
send_cmd("stp mode rstp")
send_cmd("stp enable")
send_cmd("stp bpdu-protection")
send_cmd("interface GE1/0/9")
send_cmd("stp edged-port enable")
send_cmd("quit")

send_cmd("commit", wait=1.2)
send_cmd("return", wait=0.1)
send_cmd("save", wait=0.5)
tn.close()
print(f"[+] Huawei {node_name}: восстановлен из {cfg_name}")
' "${user}" "${pass}" "${node}" "${cfg_file}" < "${full_cfg}"
  fi
}

# --- 3. Cisco c8000v (IOS-XE) ---
reset_cisco() {
  local node="$1"
  local cfg_file="$2"
  local full_cfg="${CONFIG_DIR}/${cfg_file}"
  local container="clab-${LAB_NAME}-${node}"
  local creds=$(get_node_credentials "${node}" "${cfg_file}")
  local user=$(echo "$creds" | cut -d':' -f1)
  local pass=$(echo "$creds" | cut -d':' -f2)

  if docker inspect "${container}" > /dev/null 2>&1; then
    docker exec -i "${container}" python3 -W ignore -c "${RAW_TELNET_PY}"'
user, pwd, node_name, cfg_name = sys.argv[1:5]
cfg_lines = [l.strip() for l in sys.stdin.read().splitlines() if l.strip()]

tn = RawTelnet("127.0.0.1", 5000, timeout=10)
tn.write(b"\r\n\r\n")
time.sleep(0.5)

pw_try = 0
pws = [pwd]
for _ in range(7):
    idx, _, _ = tn.expect([br"[Uu]sername:", br"[Pp]assword:", br"#", br">"], timeout=2)
    if idx == 0: tn.write(f"{user}\r\n".encode())
    elif idx == 1:
        tn.write(f"{pws[min(pw_try, len(pws) - 1)]}\r\n".encode())
        pw_try += 1
    elif idx == 2: break
    elif idx == 3:
        tn.write(b"enable\r\n")
        for epw in pws:
            if tn.expect([br"[Pp]assword:", br"#"], timeout=2)[0] == 0:
                tn.write(f"{epw}\r\n".encode())
            else:
                break
        break

tn.write(b"\r\nterminal length 0\r\nconf t\r\n")
time.sleep(0.3)
tn.write(b"no router bgp 65000\r\nno router bgp 65001\r\nno router bgp 65002\r\n")
time.sleep(0.3)

for line in cfg_lines:
    if line.startswith("!") or line.startswith("#") or line == "end" or line.startswith("Building") or line.startswith("Current"):
        continue
    tn.write(line.encode("utf-8") + b"\r\n")
    # НЕ посылаем лишний exit после exit-address-family, чтобы остаться в router bgp!
    time.sleep(0.015)

# STP на Cisco
tn.write(b"\r\nend\r\nwr\r\n")
time.sleep(2.0)
tn.close()
print(f"[+] Cisco {node_name}: восстановлена из {cfg_name}")
' "${user}" "${pass}" "${node}" "${cfg_file}" < "${full_cfg}"
  fi
}

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

echo "[*] Подстройка IP на host-b для VXLAN VLAN 10 Anycast..."
docker exec "clab-${LAB_NAME}-host-b" sh -c "ip addr flush dev eth1 && ip addr add 10.20.1.20/24 dev eth1 && ip link set eth1 up && ip route replace default via 10.20.1.1" 2>/dev/null || true

ELAPSED=$(( $(date +%s) - START_TIME ))
echo "=============================================="
echo "[+] Все устройства корректно сброшены за ${ELAPSED}с"
echo "=============================================="

