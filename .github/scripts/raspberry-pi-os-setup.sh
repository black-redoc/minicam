#!/bin/sh
# Turn a Debian trixie arm64 container into a Raspberry Pi OS (64-bit)
# userland: Raspberry Pi OS is Debian plus the Raspberry Pi apt repository,
# whose packages replace some of Debian's. Also creates the unprivileged
# `pi` user the tests run as, like the app does on the device.
#
# Used by the raspberry-pi-os job in .github/workflows/ci.yml.
set -eu

# The repository's signing key comes from Raspberry Pi OS's own keyring
# package, pinned by checksum like the CI's actions are pinned by commit.
# (raspberrypi.gpg.key on the same server still carries a SHA-1 binding
# signature, which trixie's apt rejects.) If Raspberry Pi drops this version
# from the pool, bump both values from dists/trixie/main/binary-arm64/Packages.
KEYRING_DEB=raspberrypi-archive-keyring_2025.1+rpt1_all.deb
KEYRING_SHA256=2e727149d7acb8cc7f604e66d0049161039c8aa1eaf1175e54f9e69d963d60e4
RASPI_ARCHIVE=https://archive.raspberrypi.com/debian

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git

curl -fsSL -o "/tmp/$KEYRING_DEB" \
    "$RASPI_ARCHIVE/pool/main/r/raspberrypi-archive-keyring/$KEYRING_DEB"
echo "$KEYRING_SHA256  /tmp/$KEYRING_DEB" | sha256sum -c -
dpkg -i "/tmp/$KEYRING_DEB"

cat > /etc/apt/sources.list.d/raspi.sources <<EOF
Types: deb
URIs: $RASPI_ARCHIVE/
Suites: trixie
Components: main
Signed-By: /usr/share/keyrings/raspberrypi-archive-keyring.pgp
EOF

apt-get update
# Pick up every package Raspberry Pi OS ships in place of Debian's.
apt-get dist-upgrade -y --no-install-recommends

useradd --create-home pi
