#!/usr/bin/env bash
set -euo pipefail

log() { echo "[$(date -u +%FT%TZ)] client: $*"; }

GUEST="http://metadata.google.internal/computeMetadata/v1/instance/guest-attributes"
HDR=(-H "Metadata-Flavor: Google")

# Marker so reboots skip the internet-dependent install/provisioning steps.
MARKER=/var/lib/bench-client-provisioned
WORKDIR=/srv/benchmarks

signal_ready() {
  curl -fsS -X PUT --data "1" "${HDR[@]}" "${GUEST}/bench/ready" >/dev/null 2>&1 || true
}

if [ -f "${MARKER}" ]; then
  log "Already provisioned."
  signal_ready
  exit 0
fi

apt-get update
apt-get install -y \
  build-essential \
  ca-certificates \
  curl \
  git \
  python-is-python3 \
  python3 \
  python3-pip \
  python3-venv \
  rsync \
  unzip

curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
for bin in /usr/local/bin/uv /usr/local/bin/uvx; do
  [ -f "${bin}" ] && chmod 0755 "${bin}"
done

mkdir -p "${WORKDIR}"
chmod 1777 "${WORKDIR}"

uv --version
python3 --version

touch "${MARKER}"
signal_ready
log "Ready for benchmark project uploads in ${WORKDIR}."
