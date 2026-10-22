#!/usr/bin/env bash
# Запускается НА хосте стенда (Containerlab): публикует UDP-релей SNMP для каждого узла.
# Платформа опрашивает <clab_host>:<SNMP_RELAY_BASE_PORT + последний октет IP>, а релей
# пересылает пакет на агент SNMP внутри сети Containerlab (UDP не пройдёт через SSH-джамп).
#
# Использование: CLAB_NETWORK=clab_mgmt NODE_IPS="172.20.20.2 172.20.20.4" ./snmp-relays.sh
set -euo pipefail

NETWORK="${CLAB_NETWORK:-clab_mgmt}"
BASE_PORT="${SNMP_RELAY_BASE_PORT:-16000}"
: "${NODE_IPS:?Задайте NODE_IPS — IP узлов Containerlab через пробел}"

for ip in ${NODE_IPS}; do
  octet="${ip##*.}"
  name="snmp-relay-${octet}"
  docker rm -f "${name}" >/dev/null 2>&1 || true
  docker run -d --restart unless-stopped --name "${name}" --network "${NETWORK}" \
    -p "$((BASE_PORT + octet)):161/udp" alpine/socat \
    "UDP4-RECVFROM:161,fork" "UDP4-SENDTO:${ip}:161" >/dev/null
  echo "${name}: udp/$((BASE_PORT + octet)) -> ${ip}:161"
done
