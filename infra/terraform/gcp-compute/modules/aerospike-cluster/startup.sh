#!/usr/bin/env bash
set -euo pipefail

log() { echo "[$(date -u +%FT%TZ)] aerospike: $*"; }

META="http://metadata.google.internal/computeMetadata/v1/instance/attributes"
HDR=(-H "Metadata-Flavor: Google")

NAME_PREFIX=$(curl -fsS "${HDR[@]}" "${META}/name-prefix")
NODE_INDEX=$(curl -fsS "${HDR[@]}" "${META}/node-index")
CLUSTER_SIZE=$(curl -fsS "${HDR[@]}" "${META}/cluster-size")
VERSION=$(curl -fsS "${HDR[@]}" "${META}/aerospike-version")
TOOLS_VERSION=$(curl -fsS "${HDR[@]}" "${META}/aerospike-tools-version")
NAMESPACE=$(curl -fsS "${HDR[@]}" "${META}/aerospike-namespace")
REPLICATION_FACTOR=$(curl -fsS "${HDR[@]}" "${META}/replication-factor")
LOCAL_SSD_COUNT=$(curl -fsS "${HDR[@]}" "${META}/local-ssd-count")
PARTITIONS_PER_SSD=$(curl -fsS "${HDR[@]}" "${META}/device-partitions-per-ssd")
COMMIT_TO_DEVICE=$(curl -fsS "${HDR[@]}" "${META}/commit-to-device")
FEATURES_B64=$(curl -fsS "${HDR[@]}" "${META}/features-conf-base64")

log "node ${NODE_INDEX}/${CLUSTER_SIZE}, Aerospike EE ${VERSION}"

echo never > /sys/kernel/mm/transparent_hugepage/enabled || true
echo never > /sys/kernel/mm/transparent_hugepage/defrag || true

cat > /etc/sysctl.d/99-bench.conf <<'CONF'
net.core.somaxconn = 4096
net.ipv4.tcp_tw_reuse = 1
vm.swappiness = 1
fs.file-max = 1000000
CONF
sysctl --system >/dev/null

for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
  [ -f "$g" ] && echo performance > "$g" || true
done

apt-get update
apt-get install -y curl gnupg lsb-release python3 ca-certificates parted

TGZ_URL="https://download.aerospike.com/artifacts/aerospike-server-enterprise/${VERSION}/aerospike-server-enterprise_${VERSION}_tools-${TOOLS_VERSION}_ubuntu22.04_x86_64.tgz"
curl -fsSL -o /tmp/aerospike.tgz "${TGZ_URL}"
mkdir -p /tmp/aerospike
tar -xzf /tmp/aerospike.tgz -C /tmp/aerospike --strip-components=1
apt-get install -y /tmp/aerospike/aerospike-server-enterprise_*.deb /tmp/aerospike/aerospike-tools_*.deb

mkdir -p /etc/aerospike
echo "${FEATURES_B64}" | base64 -d > /etc/aerospike/features.conf
chmod 640 /etc/aerospike/features.conf
chown root:aerospike /etc/aerospike/features.conf || true

DEVICE_LINES=""
for i in $(seq 0 $((LOCAL_SSD_COUNT - 1))); do
  SSD="/dev/nvme0n$((i + 1))"
  log "Partitioning ${SSD} into ${PARTITIONS_PER_SSD} equal parts"
  wipefs -af "${SSD}" || true
  parted -s "${SSD}" mklabel gpt
  step=$((100 / PARTITIONS_PER_SSD))

  for p in $(seq 1 "${PARTITIONS_PER_SSD}"); do
    start=$(((p - 1) * step))
    end=$((p * step))
    if [ "${p}" -eq "${PARTITIONS_PER_SSD}" ]; then end=100; fi
    parted -s "${SSD}" mkpart "as-${i}-${p}" "${start}%" "${end}%"
  done
  partprobe "${SSD}" || true

  for p in $(seq 1 "${PARTITIONS_PER_SSD}"); do
    for _ in $(seq 1 30); do
      [ -b "${SSD}p${p}" ] && break
      sleep 1
    done
    DEVICE_LINES="${DEVICE_LINES}        device ${SSD}p${p}"$'\n'
    chown root:aerospike "${SSD}p${p}" || true
  done
done

SEED_LINES=""
for i in $(seq 1 "${CLUSTER_SIZE}"); do
  SEED_LINES="${SEED_LINES}        mesh-seed-address-port ${NAME_PREFIX}-aerospike-${i} 3002"$'\n'
done

COMMIT_LINE=""
if [ "${COMMIT_TO_DEVICE}" = "true" ]; then
  COMMIT_LINE="        commit-to-device true"
fi

cat > /etc/aerospike/aerospike.conf <<CONF
service {
    cluster-name bench
    feature-key-file /etc/aerospike/features.conf
    proto-fd-max 15000
}

logging {
    console {
        context any info
    }
}

network {
    service {
        address any
        port 3000
    }
    heartbeat {
        mode mesh
        port 3002
${SEED_LINES%$'\n'}
        interval 150
        timeout 10
    }
    fabric {
        port 3001
    }
    info {
        port 3003
    }
}

namespace ${NAMESPACE} {
    replication-factor ${REPLICATION_FACTOR}
    default-ttl 0
    nsup-period 120

    storage-engine device {
${DEVICE_LINES%$'\n'}
${COMMIT_LINE}
    }
}
CONF

systemctl enable aerospike
systemctl start aerospike

log "Aerospike started."
