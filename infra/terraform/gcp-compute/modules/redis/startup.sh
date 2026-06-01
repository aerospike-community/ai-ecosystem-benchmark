#!/usr/bin/env bash
set -euo pipefail

log() { echo "[$(date -u +%FT%TZ)] redis: $*"; }

META="http://metadata.google.internal/computeMetadata/v1/instance/attributes"
GUEST="http://metadata.google.internal/computeMetadata/v1/instance/guest-attributes"
HDR=(-H "Metadata-Flavor: Google")

# Marker so reboots skip the internet-dependent install/provisioning steps.
MARKER=/var/lib/bench-provisioned

# Tell the workflow this node finished provisioning. Uses guest attributes, which
# require no outbound internet. The workflow polls this before removing Cloud NAT.
signal_ready() {
  curl -fsS -X PUT --data "1" "${HDR[@]}" "${GUEST}/bench/ready" >/dev/null 2>&1 || true
}

NAME_PREFIX=$(curl -fsS "${HDR[@]}" "${META}/name-prefix")
NODE_INDEX=$(curl -fsS "${HDR[@]}" "${META}/node-index")
NODE_ROLE=$(curl -fsS "${HDR[@]}" "${META}/node-role")
TOPOLOGY=$(curl -fsS "${HDR[@]}" "${META}/topology")
QUORUM=$(curl -fsS "${HDR[@]}" "${META}/sentinel-quorum")
MASTER_NAME=$(curl -fsS "${HDR[@]}" "${META}/master-name")

log "node ${NODE_INDEX} role=${NODE_ROLE} topology=${TOPOLOGY}"

if [ -f "${MARKER}" ]; then
  log "Already provisioned; ensuring services are running (no internet required)."
  systemctl start redis-stack-server || true
  if [ "${TOPOLOGY}" = "sentinel" ]; then
    systemctl start redis-sentinel || true
  fi
  signal_ready
  exit 0
fi

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
apt-get install -y redis-stack-server
if [ "${TOPOLOGY}" = "sentinel" ]; then
  apt-get install -y redis-sentinel
fi

systemctl stop redis-stack-server || true
systemctl disable redis-stack-server || true
systemctl stop redis-server || true
systemctl disable redis-server || true

id redis >/dev/null 2>&1 || useradd --system --home /var/lib/redis --shell /usr/sbin/nologin redis
mkdir -p /etc/redis /var/lib/redis /var/log/redis
chown -R redis:redis /var/lib/redis /var/log/redis

STACK_MODULE_LOAD_LINES=""

append_stack_module() {
  local module_path="$1"
  local module_name="$2"

  if [ -f "${module_path}" ]; then
    STACK_MODULE_LOAD_LINES+="loadmodule ${module_path}"$'\n'
    log "Redis Stack module enabled: ${module_name}"
  else
    log "Redis Stack module not present, skipping: ${module_name}"
  fi
}

REDISEARCH_MODULE="/opt/redis-stack/lib/redisearch.so"
if [ ! -f "${REDISEARCH_MODULE}" ]; then
  log "RediSearch module not found at ${REDISEARCH_MODULE}"
  exit 1
fi

REDISJSON_MODULE="/opt/redis-stack/lib/rejson.so"
if [ ! -f "${REDISJSON_MODULE}" ]; then
  log "RedisJSON module not found at ${REDISJSON_MODULE}"
  exit 1
fi

append_stack_module "/opt/redis-stack/lib/rediscompat.so" "RedisCompat"
append_stack_module "${REDISJSON_MODULE}" "RedisJSON"
append_stack_module "${REDISEARCH_MODULE}" "RediSearch"
append_stack_module "/opt/redis-stack/lib/redisbloom.so" "RedisBloom"
append_stack_module "/opt/redis-stack/lib/redistimeseries.so" "RedisTimeSeries"

REDIS_CLI="/opt/redis-stack/bin/redis-cli"
if [ ! -x "${REDIS_CLI}" ]; then
  REDIS_CLI="redis-cli"
fi

PRIMARY_IP=""
if [ "${TOPOLOGY}" = "sentinel" ]; then
  PRIMARY_IP="${NAME_PREFIX}-redis-1"
  log "Primary resolved to ${PRIMARY_IP}"
fi

cat > /etc/redis-stack.conf <<CONF
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
${STACK_MODULE_LOAD_LINES%$'\n'}
daemonize no
supervised no
CONF

if [ "${NODE_ROLE}" = "replica" ]; then
  echo "replicaof ${PRIMARY_IP} 6379" >> /etc/redis-stack.conf
fi
chown root:redis /etc/redis-stack.conf
chmod 0640 /etc/redis-stack.conf

cat > /etc/systemd/system/redis-stack-server.service <<'CONF'
[Unit]
Description=Redis Stack Server
After=network.target

[Service]
Type=simple
User=redis
Group=redis
RuntimeDirectory=redis
RuntimeDirectoryMode=0755
ExecStart=/opt/redis-stack/bin/redis-server /etc/redis-stack.conf --daemonize no
ExecStop=/bin/kill -s TERM $MAINPID
Restart=always
RestartSec=5
LimitNOFILE=1000000

[Install]
WantedBy=multi-user.target
CONF

systemctl daemon-reload
systemctl enable redis-stack-server
systemctl restart redis-stack-server

verify_redis_stack_ready() {
  "${REDIS_CLI}" -h 127.0.0.1 -p 6379 FT._LIST >/dev/null 2>&1 || return 1
  "${REDIS_CLI}" -h 127.0.0.1 -p 6379 FT.DROPINDEX __redis_stack_json_probe >/dev/null 2>&1 || true
  "${REDIS_CLI}" -h 127.0.0.1 -p 6379 FT.CREATE __redis_stack_json_probe ON JSON PREFIX 1 __redis_stack_json_probe: SCHEMA '$.id' AS id TAG >/dev/null 2>&1 || return 1
  "${REDIS_CLI}" -h 127.0.0.1 -p 6379 FT.DROPINDEX __redis_stack_json_probe >/dev/null 2>&1 || return 1
}

for _ in $(seq 1 30); do
  if verify_redis_stack_ready; then
    log "Redis Stack search and JSON modules ready."
    break
  fi
  sleep 1
done

verify_redis_stack_ready

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

touch "${MARKER}"
signal_ready
log "Redis topology=${TOPOLOGY} role=${NODE_ROLE} ready."
