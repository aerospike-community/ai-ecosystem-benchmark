#!/usr/bin/env bash
set -euo pipefail

log() { echo "[$(date -u +%FT%TZ)] redis: $*"; }

META="http://metadata.google.internal/computeMetadata/v1/instance/attributes"
HDR=(-H "Metadata-Flavor: Google")

NAME_PREFIX=$(curl -fsS "${HDR[@]}" "${META}/name-prefix")
NODE_INDEX=$(curl -fsS "${HDR[@]}" "${META}/node-index")
NODE_ROLE=$(curl -fsS "${HDR[@]}" "${META}/node-role")
TOPOLOGY=$(curl -fsS "${HDR[@]}" "${META}/topology")
QUORUM=$(curl -fsS "${HDR[@]}" "${META}/sentinel-quorum")
MASTER_NAME=$(curl -fsS "${HDR[@]}" "${META}/master-name")

log "node ${NODE_INDEX} role=${NODE_ROLE} topology=${TOPOLOGY}"

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

NVME_DEV="/dev/nvme0n1"
if [ -b "${NVME_DEV}" ]; then
  if ! blkid "${NVME_DEV}" >/dev/null 2>&1; then
    log "Formatting ${NVME_DEV} as ext4"
    mkfs.ext4 -F -L redis-data "${NVME_DEV}"
  fi
  mkdir -p /var/lib/redis
  mount -o noatime "${NVME_DEV}" /var/lib/redis
  echo "LABEL=redis-data /var/lib/redis ext4 noatime 0 0" >> /etc/fstab
fi

apt-get update
apt-get install -y curl gnupg lsb-release ca-certificates

curl -fsSL https://packages.redis.io/gpg | gpg --dearmor -o /usr/share/keyrings/redis-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -cs) main" \
  > /etc/apt/sources.list.d/redis.list

apt-get update
apt-get install -y redis
chown -R redis:redis /var/lib/redis

PRIMARY_IP=""
if [ "${TOPOLOGY}" = "sentinel" ]; then
  PRIMARY_IP="${NAME_PREFIX}-redis-1"
  log "Primary resolved to ${PRIMARY_IP}"
fi

cat > /etc/redis/redis.conf <<CONF
bind 0.0.0.0 -::*
protected-mode no
port 6379
tcp-backlog 4096
tcp-keepalive 60
timeout 0
maxclients 10000
io-threads 4
io-threads-do-reads yes
appendonly yes
appendfsync everysec
no-appendfsync-on-rewrite no
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb
aof-use-rdb-preamble yes
save ""
maxmemory-policy noeviction
dir /var/lib/redis
loglevel notice
logfile /var/log/redis/redis-server.log
daemonize no
supervised systemd
CONF

if [ "${NODE_ROLE}" = "replica" ]; then
  echo "replicaof ${PRIMARY_IP} 6379" >> /etc/redis/redis.conf
fi

systemctl enable redis-server
systemctl restart redis-server

if [ "${TOPOLOGY}" = "sentinel" ]; then
  log "Configuring redis-sentinel with master ${MASTER_NAME}@${PRIMARY_IP}, quorum ${QUORUM}"

  mkdir -p /var/lib/redis-sentinel
  chown redis:redis /var/lib/redis-sentinel

  cat > /etc/redis/sentinel.conf <<CONF
port 26379
bind 0.0.0.0
daemonize no
supervised systemd
protected-mode no
dir /var/lib/redis-sentinel
logfile /var/log/redis/redis-sentinel.log
sentinel monitor ${MASTER_NAME} ${PRIMARY_IP} 6379 ${QUORUM}
sentinel down-after-milliseconds ${MASTER_NAME} 5000
sentinel parallel-syncs ${MASTER_NAME} 1
sentinel failover-timeout ${MASTER_NAME} 30000
CONF

  systemctl enable redis-sentinel || true
  systemctl restart redis-sentinel
fi

log "Redis topology=${TOPOLOGY} role=${NODE_ROLE} ready."
