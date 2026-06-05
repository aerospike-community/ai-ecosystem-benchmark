#!/usr/bin/env bash
set -euo pipefail

log() { echo "[$(date -u +%FT%TZ)] postgres: $*"; }

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
NODE_ROLE=$(curl -fsS "${HDR[@]}" "${META}/node-role")
TOPOLOGY=$(curl -fsS "${HDR[@]}" "${META}/topology")
PG_MAJOR=$(curl -fsS "${HDR[@]}" "${META}/postgres-major-version")
PG_DB=$(curl -fsS "${HDR[@]}" "${META}/postgres-db-name")
PG_USER=$(curl -fsS "${HDR[@]}" "${META}/postgres-user")
PG_PASSWORD=$(curl -fsS "${HDR[@]}" "${META}/postgres-password")
REPL_USER=$(curl -fsS "${HDR[@]}" "${META}/replication-user")
REPL_PASSWORD=$(curl -fsS "${HDR[@]}" "${META}/replication-password")
SYNC_COMMIT=$(curl -fsS "${HDR[@]}" "${META}/synchronous-commit")
POSTGRES_CLIENT_CIDRS=$(curl -fsS "${HDR[@]}" "${META}/postgres-client-cidrs")
VPC_CIDR=$(curl -fsS "${HDR[@]}" "${META}/vpc-cidr")

log "role=${NODE_ROLE} topology=${TOPOLOGY}"

if [ -f "${MARKER}" ]; then
  log "Already provisioned; ensuring service is running (no internet required)."
  systemctl start "postgresql@${PG_MAJOR}-main" || true
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
kernel.shmall = 4194304
kernel.shmmax = 17179869184
CONF
sysctl --system >/dev/null

for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
  [ -f "$g" ] && echo performance > "$g" || true
done

apt-get update
apt-get install -y curl gnupg lsb-release ca-certificates

install -d /usr/share/postgresql-common/pgdg
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
  -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
  > /etc/apt/sources.list.d/pgdg.list

apt-get update
apt-get install -y "postgresql-${PG_MAJOR}"
pg_dropcluster --stop "${PG_MAJOR}" main || true

PG_MOUNT="/var/lib/postgresql/${PG_MAJOR}/mnt"
PG_DATA_DIR="${PG_MOUNT}/main"
PG_CONF_DIR="/etc/postgresql/${PG_MAJOR}/main"
mkdir -p "${PG_MOUNT}" "${PG_CONF_DIR}"
chown postgres:postgres "${PG_MOUNT}" "${PG_CONF_DIR}"

NVME_DEV="/dev/nvme0n1"
if [ -b "${NVME_DEV}" ]; then
  if ! blkid "${NVME_DEV}" >/dev/null 2>&1; then
    log "Formatting ${NVME_DEV} as ext4"
    mkfs.ext4 -F -L pg-data "${NVME_DEV}"
  fi
  mount -o noatime "${NVME_DEV}" "${PG_MOUNT}"
  echo "LABEL=pg-data ${PG_MOUNT} ext4 noatime 0 0" >> /etc/fstab
fi
mkdir -p "${PG_DATA_DIR}"
chown -R postgres:postgres "${PG_MOUNT}"
chmod 700 "${PG_DATA_DIR}"

write_postgresql_conf() {
  cat > "${PG_CONF_DIR}/postgresql.conf" <<CONF
data_directory = '${PG_DATA_DIR}'
hba_file = '${PG_CONF_DIR}/pg_hba.conf'
ident_file = '${PG_CONF_DIR}/pg_ident.conf'
external_pid_file = '/var/run/postgresql/${PG_MAJOR}-main.pid'
listen_addresses = '*'
port = 5432
max_connections = 200
password_encryption = scram-sha-256
shared_buffers = 8GB
effective_cache_size = 24GB
work_mem = 16MB
maintenance_work_mem = 2GB
huge_pages = try
wal_level = replica
wal_buffers = 16MB
max_wal_size = 8GB
min_wal_size = 2GB
wal_compression = on
checkpoint_timeout = 15min
checkpoint_completion_target = 0.9
synchronous_commit = ${SYNC_COMMIT}
max_wal_senders = 10
max_replication_slots = 10
hot_standby = on
random_page_cost = 1.1
effective_io_concurrency = 200
log_min_duration_statement = 500ms
log_checkpoints = on
log_lock_waits = on
log_temp_files = 0
log_line_prefix = '%m [%p] %q%u@%d '
cluster_name = '${PG_MAJOR}/main'
CONF
}

write_pg_hba() {
  cat > "${PG_CONF_DIR}/pg_hba.conf" <<CONF
local   all             postgres                                peer
local   all             all                                     peer
host    all             all             127.0.0.1/32            scram-sha-256
host    all             all             ::1/128                 scram-sha-256
CONF

  IFS=',' read -ra CLIENT_CIDRS <<< "${POSTGRES_CLIENT_CIDRS}"
  for cidr in "${CLIENT_CIDRS[@]}"; do
    [ -n "${cidr}" ] && printf 'host    all             all             %-21s scram-sha-256\n' "${cidr}" >> "${PG_CONF_DIR}/pg_hba.conf"
  done

  cat >> "${PG_CONF_DIR}/pg_hba.conf" <<CONF
host    replication     ${REPL_USER}    ${VPC_CIDR}             scram-sha-256
CONF
}

case "${NODE_ROLE}" in
  standalone|primary)
    log "Initializing primary at ${PG_DATA_DIR}"
    PWFILE="/tmp/pgpw.$$"
    printf '%s' "${PG_PASSWORD}" > "${PWFILE}"
    chown postgres:postgres "${PWFILE}"
    chmod 600 "${PWFILE}"
    sudo -u postgres /usr/lib/postgresql/${PG_MAJOR}/bin/initdb \
      --auth-host=scram-sha-256 \
      --auth-local=peer \
      --username=postgres \
      --pwfile="${PWFILE}" \
      -D "${PG_DATA_DIR}"
    rm -f "${PWFILE}"

    write_postgresql_conf
    write_pg_hba

    systemctl enable postgresql
    systemctl restart "postgresql@${PG_MAJOR}-main"

    for _ in $(seq 1 30); do
      sudo -u postgres psql -c 'SELECT 1' >/dev/null 2>&1 && break
      sleep 1
    done

    sudo -u postgres psql <<SQL
CREATE ROLE ${PG_USER} LOGIN PASSWORD '${PG_PASSWORD}';
CREATE DATABASE ${PG_DB} OWNER ${PG_USER};
CREATE ROLE ${REPL_USER} WITH LOGIN REPLICATION PASSWORD '${REPL_PASSWORD}';
SQL
    log "Primary ready. Database ${PG_DB} owned by ${PG_USER}."
    ;;

  replica)
    PRIMARY_IP="${NAME_PREFIX}-postgres-1"
    log "Primary at ${PRIMARY_IP}; waiting for it to accept connections"

    for _ in $(seq 1 120); do
      PGPASSWORD="${REPL_PASSWORD}" pg_isready -h "${PRIMARY_IP}" -U "${REPL_USER}" -d postgres >/dev/null 2>&1 && break
      sleep 5
    done

    rm -rf "${PG_DATA_DIR:?}"/*
    sudo -u postgres env "PGPASSWORD=${REPL_PASSWORD}" \
      /usr/lib/postgresql/${PG_MAJOR}/bin/pg_basebackup \
      -h "${PRIMARY_IP}" -U "${REPL_USER}" -D "${PG_DATA_DIR}" \
      -Fp -Xs -P -R

    write_postgresql_conf
    write_pg_hba

    chown -R postgres:postgres "${PG_DATA_DIR}"
    chmod 700 "${PG_DATA_DIR}"

    systemctl enable postgresql
    systemctl restart "postgresql@${PG_MAJOR}-main"
    log "Replica streaming from ${PRIMARY_IP}."
    ;;
esac

touch "${MARKER}"
signal_ready
log "Postgres topology=${TOPOLOGY} role=${NODE_ROLE} ready."
