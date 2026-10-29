#!/bin/bash -e
# Install minicam into the image: code in /opt/minicam owned by the system
# user `minicam`, Python and .venv built now (the device needs no network or
# compiler to start), and the systemd units enabled.
#
# Inputs staged by .github/workflows/image.yml (not in git):
#   files/minicam.tar  `git archive` of the tagged tree
#   files/uv           the workflow's pinned uv binary (arm64)

app="${ROOTFS_DIR}/opt/minicam"

install -d -m 755 "${app}"
tar -xf files/minicam.tar -C "${app}"
# Not in /tmp: on_chroot mounts an empty tmpfs over the rootfs /tmp.
build_dir="${ROOTFS_DIR}/var/tmp/minicam-build"
install -d -m 755 "${build_dir}"
install -m 755 files/uv "${build_dir}/uv"

on_chroot <<'EOF'
adduser --system --group --home /opt/minicam --no-create-home \
	--shell /usr/sbin/nologin minicam

cd /opt/minicam
export UV_PYTHON_INSTALL_DIR=/opt/minicam/.python
export UV_PYTHON_PREFERENCE=only-managed
export UV_CACHE_DIR=/tmp/uv-cache
export UV_LINK_MODE=copy
/var/tmp/minicam-build/uv python install 3.14
/var/tmp/minicam-build/uv sync --locked --no-dev --python 3.14
chown -R minicam:minicam /opt/minicam
EOF
rm -rf "${build_dir}"

files="${app}/image/files"
install -m 644 "${app}/deploy/minicam.service" "${ROOTFS_DIR}/etc/systemd/system/minicam.service"
install -d -m 755 "${ROOTFS_DIR}/etc/systemd/system/minicam.service.d"
install -m 644 "${files}/image.conf" "${ROOTFS_DIR}/etc/systemd/system/minicam.service.d/image.conf"
install -m 644 "${files}/minicam-firstboot.service" "${ROOTFS_DIR}/etc/systemd/system/minicam-firstboot.service"
install -m 755 "${files}/minicam-firstboot.sh" "${ROOTFS_DIR}/usr/local/sbin/minicam-firstboot"
install -m 644 "${files}/minicam.avahi.service" "${ROOTFS_DIR}/etc/avahi/services/minicam.service"

# Cloudflare Tunnel from minicam-mobile: root helper, started when minicam
# writes /opt/minicam/.tunnel/request.json (ADR 0008).
tunnel="${app}/deploy/tunnel"
install -m 755 "${tunnel}/minicam-tunnel" "${ROOTFS_DIR}/usr/local/sbin/minicam-tunnel"
for unit in minicam-tunnel.path minicam-tunnel.service minicam-cloudflared.service; do
	install -m 644 "${tunnel}/${unit}" "${ROOTFS_DIR}/etc/systemd/system/${unit}"
done

on_chroot <<'EOF'
install -d -m 700 -o minicam -g minicam /opt/minicam/.tunnel
systemctl enable minicam-firstboot.service minicam.service minicam-tunnel.path
EOF
