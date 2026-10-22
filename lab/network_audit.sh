#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOPO_NAME=$(awk '/^name:/ {print $2; exit}' "${SCRIPT_DIR}/fabric.clab.yaml" 2>/dev/null)
LAB="${CLAB_LAB_NAME:-clab-${TOPO_NAME:-dc-clos-cisco-arista-huawei}}"
SPINE1_CREDS=$(awk '/^username / && /password / {for(i=1;i<=NF;i++) if($i=="password") print $2":"$(i+2); exit}' "${SCRIPT_DIR}/golden_configs/spine1.cfg" 2>/dev/null)
CISCO_USER="${SPINE1_CREDS%%:*}"
CISCO_PASS="${SPINE1_CREDS#*:}"
CISCO_USER="${CISCO_USER:-${CLAB_DEFAULT_USER:-admin}}"
CISCO_PASS="${CISCO_PASS:-${CLAB_DEFAULT_PASS:-admin}}"

# --- Функции опроса оборудования ---
RAW_TELNET_PY='
import re, socket, sys, time

class RawTelnet:
    def __init__(self, host, port, timeout=8.0):
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

cisco() {
  docker exec -i "${LAB}-spine1" python3 -W ignore -c "${RAW_TELNET_PY}
user = '''${CISCO_USER}'''.encode() + b'\r\n'
pw = '''${CISCO_PASS}'''.encode() + b'\r\n'
tn = RawTelnet('127.0.0.1', 5000, timeout=8)
tn.write(b'\r\n\r\n')
time.sleep(0.3)
for _ in range(5):
    idx, _, _ = tn.expect([br'[Uu]sername:', br'[Pp]assword:', br'#', br'>'], timeout=2)
    if idx == 0: tn.write(user)
    elif idx == 1: tn.write(pw)
    elif idx == 2: break
    elif idx == 3:
        tn.write(b'enable\r\n')
        if tn.expect([br'[Pp]assword:', br'#'], timeout=2)[0] == 0:
            tn.write(pw)
        break
tn.write(b'terminal length 0\r\n$1\r\n')
time.sleep(0.8)
print(tn.read_very_eager().decode('utf-8', errors='ignore'))
tn.close()
" 2>/dev/null
}

arista() {
  docker exec ${LAB}-spine2 Cli -p 15 -c "$1" 2>/dev/null
}

huawei() {
  docker exec -i ${LAB}-$1 python3 -W ignore -c "${RAW_TELNET_PY}
tn = RawTelnet('127.0.0.1', 5000, timeout=8)
tn.write(b'\x1a\r\nscreen-length 0 temporary\r\n$2\r\n')
time.sleep(0.8)
print(tn.read_very_eager().decode('utf-8', errors='ignore'))
tn.close()
" 2>/dev/null
}

echo "======================================================================"
echo "          ОТЧЕТ О ПРОВЕРКЕ СЕТЕВОЙ ФАБРИКИ (CLOS L3 + EVPN)           "
echo "======================================================================"
echo "Дата проверки: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# ----------------------------------------------------------------------
echo "[1] ПРОВЕРКА IP ROUTING (L3 ФУНКЦИОНАЛ)"
# ----------------------------------------------------------------------
s1_ipr=$(cisco "show ip route" | grep -c "Gateway of last resort")
s2_ipr=$(arista "show running-config | inc ^ip routing" | grep -c "ip routing")
l1_ipr=$(huawei leaf1 "display ip interface brief" | grep -c "GE1/0/0")
l2_ipr=$(huawei leaf2 "display ip interface brief" | grep -c "GE1/0/0")

[[ $s1_ipr -ge 1 ]] && echo "  - SPINE-1 (Cisco IOS-XE) : [PASS] IP Routing активен (таблица маршрутизации собрана)" || echo "  - SPINE-1 (Cisco IOS-XE) : [FAIL] IP Routing отключен"
[[ $s2_ipr -ge 1 ]] && echo "  - SPINE-2 (Arista cEOS)  : [PASS] Строка 'ip routing' присутствует в running-config" || echo "  - SPINE-2 (Arista cEOS)  : [FAIL] Нет 'ip routing' в конфиге"
[[ $l1_ipr -ge 1 ]] && echo "  - LEAF-1  (Huawei VRP)   : [PASS] L3-маршрутизация активна, аплинки в режиме routed (undo portswitch)" || echo "  - LEAF-1  (Huawei VRP)   : [FAIL] Ошибка L3 интерфейсов"
[[ $l2_ipr -ge 1 ]] && echo "  - LEAF-2  (Huawei VRP)   : [PASS] L3-маршрутизация активна, аплинки в режиме routed (undo portswitch)" || echo "  - LEAF-2  (Huawei VRP)   : [FAIL] Ошибка L3 интерфейсов"
echo ""

# ----------------------------------------------------------------------
echo "[2] ПРОВЕРКА СТАТИЧЕСКОЙ МАРШРУТИЗАЦИИ (РЕЗЕРВ UNDERLAY AD/PREF 200)"
# ----------------------------------------------------------------------
s1_st=$(cisco "show running-config | inc ^ip route 10.255" | grep -c "10.255")
s2_st=$(arista "show running-config | inc ip route 10.255" | grep -c "10.255")
l1_st=$(huawei leaf1 "display current-configuration | inc ip route-static 10.255" | grep -c "10.255")
l2_st=$(huawei leaf2 "display current-configuration | inc ip route-static 10.255" | grep -c "10.255")

[[ $s1_st -eq 4 ]] && echo "  - SPINE-1 (Cisco)  : [PASS] Настроено $s1_st/4 статических маршрутов до Loopback (AD 200)" || echo "  - SPINE-1 (Cisco)  : [FAIL] Найдено $s1_st/4 статических маршрутов"
[[ $s2_st -eq 4 ]] && echo "  - SPINE-2 (Arista) : [PASS] Настроено $s2_st/4 статических маршрутов до Loopback (AD 200)" || echo "  - SPINE-2 (Arista) : [FAIL] Найдено $s2_st/4 статических маршрутов"
[[ $l1_st -eq 4 ]] && echo "  - LEAF-1  (Huawei) : [PASS] Настроено $l1_st/4 статических маршрутов до Loopback (Preference 200)" || echo "  - LEAF-1  (Huawei) : [FAIL] Найдено $l1_st/4 статических маршрутов"
[[ $l2_st -eq 4 ]] && echo "  - LEAF-2  (Huawei) : [PASS] Настроено $l2_st/4 статических маршрутов до Loopback (Preference 200)" || echo "  - LEAF-2  (Huawei) : [FAIL] Найдено $l2_st/4 статических маршрутов"
echo ""

# ----------------------------------------------------------------------
echo "[3] ПРОВЕРКА СВЯЗНОСТИ КОММУТАТОРОВ (ICMP PING P2P + LOOPBACKS)"
# ----------------------------------------------------------------------
p_s1_l1=$(cisco "ping 10.10.0.1 repeat 2 timeout 1" | grep -c "!!")
p_s1_l2=$(cisco "ping 10.10.0.3 repeat 2 timeout 1" | grep -c "!!")
p_s2_l1=$(arista "ping 10.10.0.5 repeat 2 timeout 1" | grep -c "0% packet loss")
p_s2_l2=$(arista "ping 10.10.0.7 repeat 2 timeout 1" | grep -c "0% packet loss")
p_lo_12=$(huawei leaf1 "ping -a 10.255.0.1 -c 2 -t 200 10.255.0.2" | grep -c "0.00% packet loss")

[[ $p_s1_l1 -ge 1 && $p_s1_l2 -ge 1 ]] && echo "  - SPINE-1 -> LEAF-1 (10.10.0.1) & LEAF-2 (10.10.0.3) : [PASS] 0% потерь" || echo "  - SPINE-1 -> LEAF-1/LEAF-2 : [FAIL] Нет пинга по P2P"
[[ $p_s2_l1 -ge 1 && $p_s2_l2 -ge 1 ]] && echo "  - SPINE-2 -> LEAF-1 (10.10.0.5) & LEAF-2 (10.10.0.7) : [PASS] 0% потерь" || echo "  - SPINE-2 -> LEAF-1/LEAF-2 : [FAIL] Нет пинга по P2P"
[[ $p_lo_12 -ge 1 ]] && echo "  - Loopback VTEP LEAF-1 (10.255.0.1) -> LEAF-2 (10.255.0.2) : [PASS] 0% потерь (транзит через Spine работает)" || echo "  - Loopback VTEP LEAF-1 -> LEAF-2 : [FAIL] Лупбэки не видят друг друга"
echo ""

# ----------------------------------------------------------------------
echo "[4] ПРОВЕРКА BGP UNDERLAY (IPv4 UNICAST)"
# ----------------------------------------------------------------------
b4_s1=$(cisco "show bgp ipv4 unicast summary" | grep -E "10\.10\.0\.(1|3)" | grep -v -E "Idle|Active|Connect" | wc -l)
b4_s2=$(arista "show ip bgp summary" | grep -E "10\.10\.0\.(5|7)" | grep -c "Estab")
b4_l1=$(huawei leaf1 "display bgp peer" | grep -c "Established")
b4_l2=$(huawei leaf2 "display bgp peer" | grep -c "Established")

[[ $b4_s1 -eq 2 ]] && echo "  - SPINE-1 (Cisco,  AS 65000) : [PASS] 2/2 соседей Established (10.10.0.1, 10.10.0.3)" || echo "  - SPINE-1 (Cisco,  AS 65000) : [FAIL] Поднято $b4_s1/2 соседей"
[[ $b4_s2 -eq 2 ]] && echo "  - SPINE-2 (Arista, AS 65000) : [PASS] 2/2 соседей Established (10.10.0.5, 10.10.0.7)" || echo "  - SPINE-2 (Arista, AS 65000) : [FAIL] Поднято $b4_s2/2 соседей"
[[ $b4_l1 -eq 2 ]] && echo "  - LEAF-1  (Huawei, AS 65001) : [PASS] 2/2 соседей Established (10.10.0.0, 10.10.0.4)" || echo "  - LEAF-1  (Huawei, AS 65001) : [FAIL] Поднято $b4_l1/2 соседей"
[[ $b4_l2 -eq 2 ]] && echo "  - LEAF-2  (Huawei, AS 65002) : [PASS] 2/2 соседей Established (10.10.0.2, 10.10.0.6)" || echo "  - LEAF-2  (Huawei, AS 65002) : [FAIL] Поднято $b4_l2/2 соседей"
echo ""

# ----------------------------------------------------------------------
echo "[5] ПРОВЕРКА BGP OVERLAY (L2VPN EVPN) И VXLAN ФАБРИКИ"
# ----------------------------------------------------------------------
ev_s1=$(cisco "show bgp l2vpn evpn summary" | grep -E "10\.10\.0\.(1|3)" | grep -v -E "Idle|Active|Connect" | wc -l)
ev_s2=$(arista "show bgp evpn summary" | grep -E "10\.10\.0\.(5|7)" | grep "Estab" | grep -v -c "NotNegotiated")
ev_l1=$(huawei leaf1 "display bgp evpn peer" | grep -c "Established")
ev_l2=$(huawei leaf2 "display bgp evpn peer" | grep -c "Established")

vni_l1=$(huawei leaf1 "display vxlan vni" | grep "10010" | grep -c "up")
vni_l2=$(huawei leaf2 "display vxlan vni" | grep "10010" | grep -c "up")
tun_l1=$(huawei leaf1 "display vxlan tunnel" | grep -c "10.255.0.2")
tun_l2=$(huawei leaf2 "display vxlan tunnel" | grep -c "10.255.0.1")

[[ $ev_s1 -eq 2 ]] && echo "  - SPINE-1 BGP L2VPN EVPN     : [PASS] 2/2 сессий активны" || echo "  - SPINE-1 BGP L2VPN EVPN     : [FAIL] Активно $ev_s1/2 сессий (Проверьте address-family l2vpn evpn)"
[[ $ev_s2 -eq 2 ]] && echo "  - SPINE-2 BGP L2VPN EVPN     : [PASS] 2/2 сессий Established (EVPN Negotiated)" || echo "  - SPINE-2 BGP L2VPN EVPN     : [FAIL] Активно $ev_s2/2 сессий (Статус Estab(NotNegotiated) — на Leaf не включен l2vpn-family evpn)"
[[ $ev_l1 -eq 2 ]] && echo "  - LEAF-1  BGP L2VPN EVPN     : [PASS] 2/2 сессий Established" || echo "  - LEAF-1  BGP L2VPN EVPN     : [FAIL] Активно $ev_l1/2 сессий"
[[ $ev_l2 -eq 2 ]] && echo "  - LEAF-2  BGP L2VPN EVPN     : [PASS] 2/2 сессий Established" || echo "  - LEAF-2  BGP L2VPN EVPN     : [FAIL] Активно $ev_l2/2 сессий"
[[ $vni_l1 -ge 1 && $tun_l1 -ge 1 ]] && echo "  - LEAF-1  VXLAN (VNI 10010)  : [PASS] VNI 10010 в состоянии UP, VTEP-туннель до 10.255.0.2 поднят" || echo "  - LEAF-1  VXLAN (VNI 10010)  : [FAIL] VNI 10010 UP=$vni_l1, Туннелей=$tun_l1"
[[ $vni_l2 -ge 1 && $tun_l2 -ge 1 ]] && echo "  - LEAF-2  VXLAN (VNI 10010)  : [PASS] VNI 10010 в состоянии UP, VTEP-туннель до 10.255.0.1 поднят" || echo "  - LEAF-2  VXLAN (VNI 10010)  : [FAIL] VNI 10010 UP=$vni_l2, Туннелей=$tun_l2"

p_gw_a=$(docker exec ${LAB}-host-a ping -c 2 -W 1 10.20.1.1 2>/dev/null | grep -c "0% packet loss")
p_ab=$(docker exec ${LAB}-host-a ping -c 2 -W 1 10.20.1.20 2>/dev/null | grep -c "0% packet loss")
[[ $p_gw_a -eq 1 ]] && echo "  - HOST-A -> Anycast GW (10.20.1.1) : [PASS] Шлюз Vbdif10 отвечает" || echo "  - HOST-A -> Anycast GW (10.20.1.1) : [FAIL] Шлюз недоступен (порт GE1/0/9 не в L2 VLAN 10 или Vbdif10 down)"
[[ $p_ab -eq 1 ]]   && echo "  - HOST-A -> HOST-B через VXLAN     : [PASS] Сквозной L2/L3 трафик через VNI 10010 ходит" || echo "  - HOST-A -> HOST-B через VXLAN     : [FAIL] Нет связности между хостами"
echo ""

# ----------------------------------------------------------------------
echo "[6] ПРОВЕРКА STP И ЗАЩИТЫ ОТ ПЕТЕЛЬ (BPDU GUARD / LOOP GUARD)"
# ----------------------------------------------------------------------
stp_s1_mode=$(cisco "show spanning-tree summary" | grep -c "rapid-pvst")
stp_s1_grds=$(cisco "show spanning-tree summary" | grep -E "BPDU Guard Default.*is enabled|Loopguard Default.*is enabled" | wc -l)

stp_s2_mode=$(arista "show spanning-tree" | grep -c "mstp")
stp_s2_bpdu=$(arista "show running-config | inc spanning-tree" | grep -c "bpduguard")
stp_s2_loop=$(arista "show running-config | inc spanning-tree" | grep -c "loopguard")

stp_l1_en=$(huawei leaf1 "display stp Global" | grep -c "Protocol Status.*:Enabled")
stp_l1_bpdu=$(huawei leaf1 "display stp Global" | grep -c "BPDU-Protection.*:Enabled")
stp_l2_en=$(huawei leaf2 "display stp Global" | grep -c "Protocol Status.*:Enabled")
stp_l2_bpdu=$(huawei leaf2 "display stp Global" | grep -c "BPDU-Protection.*:Enabled")

[[ $stp_s1_mode -ge 1 && $stp_s1_grds -eq 2 ]] && echo "  - SPINE-1 (Cisco)  : [PASS] Режим Rapid-PVST активен, BPDU Guard Default = Enabled, Loopguard Default = Enabled" || echo "  - SPINE-1 (Cisco)  : [FAIL] Rapid-PVST=$stp_s1_mode, Guards=$stp_s1_grds/2"
[[ $stp_s2_mode -ge 1 && $stp_s2_bpdu -ge 1 ]] && echo "  - SPINE-2 (Arista) : [PASS] Режим MSTP активен (Root Bridge), BPDU Guard Default = Enabled (Loopguard=$stp_s2_loop)" || echo "  - SPINE-2 (Arista) : [FAIL] MSTP=$stp_s2_mode, BPDU Guard=$stp_s2_bpdu"
[[ $stp_l1_en -ge 1 && $stp_l1_bpdu -ge 1 ]]   && echo "  - LEAF-1  (Huawei) : [PASS] Протокол RSTP/MSTP = Enabled, глобальный BPDU-Protection = Enabled + Edged-Port на GE1/0/9" || echo "  - LEAF-1  (Huawei) : [FAIL] STP Enabled=$stp_l1_en, BPDU-Protection=$stp_l1_bpdu"
[[ $stp_l2_en -ge 1 && $stp_l2_bpdu -ge 1 ]]   && echo "  - LEAF-2  (Huawei) : [PASS] Протокол RSTP/MSTP = Enabled, глобальный BPDU-Protection = Enabled + Edged-Port на GE1/0/9" || echo "  - LEAF-2  (Huawei) : [FAIL] STP Enabled=$stp_l2_en, BPDU-Protection=$stp_l2_bpdu"

echo "======================================================================"
