#!/bin/sh
# Check a built minicam image before it is published: minicam is installed
# and enabled, no per-device state or secret is baked in, and the image's
# unit is sandboxed at least as well as deploy/minicam.service alone.
#
# Usage (as root, on an arm64 host): check-image.sh <image.img>
# Used by the "Check image" step in .github/workflows/image.yml.
set -eu

img=${1:?usage: check-image.sh <image.img>}
mnt=$(mktemp -d)
loop=$(losetup --find --show --partscan --read-only "$img")
cleanup() {
    umount "$mnt" 2>/dev/null || true
    losetup --detach "$loop"
    rmdir "$mnt"
}
trap cleanup EXIT
# Partition 2 is the root filesystem (1 is /boot/firmware).
mount -o ro "${loop}p2" "$mnt"

failures=0
fail() {
    echo "FAIL: $*" >&2
    failures=$((failures + 1))
}

app=$mnt/opt/minicam
units=$mnt/etc/systemd/system

[ -d "$app" ] || fail "/opt/minicam is missing"
uid=$(chroot "$mnt" id -u minicam 2>/dev/null || true)
[ -n "$uid" ] && [ "$(stat -c %u "$app")" = "$uid" ] || fail "/opt/minicam is not owned by minicam"
[ "$(chroot "$mnt" getent passwd minicam | cut -d: -f7)" = /usr/sbin/nologin ] ||
    fail "minicam has a login shell"

for state in .env config.toml minicam.db; do
    [ ! -e "$app/$state" ] || fail "/opt/minicam/$state is in the image"
done

[ -x "$app/.venv/bin/uvicorn" ] || fail "/opt/minicam/.venv/bin/uvicorn is missing"
# Native arm64 host: run the image's own Python on its own packages.
chroot "$mnt" /opt/minicam/.venv/bin/python -c \
    'import av, fastapi, numpy, onnxruntime, uvicorn, wsproto' ||
    fail "the image's .venv cannot import minicam's dependencies"

for unit in minicam.service minicam-firstboot.service; do
    [ -L "$units/multi-user.target.wants/$unit" ] || fail "$unit is not enabled"
done
[ -f "$units/minicam.service.d/image.conf" ] || fail "the image.conf drop-in is missing"
[ -f "$mnt/etc/avahi/services/minicam.service" ] || fail "the Avahi service file is missing"
cmp -s "$units/minicam.service" "$app/deploy/minicam.service" ||
    fail "minicam.service differs from deploy/minicam.service"

# Exposure scores (0 = most locked down, 10 = least).
exposure() {
    systemd-analyze security --offline=true "$@" 2>/dev/null |
        sed -n 's/^→ Overall exposure level for .*: \([0-9.]*\).*/\1/p'
}
image_score=$(exposure --root="$mnt" minicam.service)
manual_score=$(exposure "$app/deploy/minicam.service")
echo "systemd-analyze security: image $image_score, deploy/minicam.service $manual_score"
if [ -z "$image_score" ] || [ -z "$manual_score" ]; then
    fail "could not read the exposure scores"
elif awk "BEGIN { exit !($image_score > $manual_score) }"; then
    fail "the image's unit is less sandboxed ($image_score > $manual_score)"
fi

if [ "$failures" -gt 0 ]; then
    echo "$failures check(s) failed" >&2
    exit 1
fi
echo "image OK"
