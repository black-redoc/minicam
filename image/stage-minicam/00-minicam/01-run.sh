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
install -m 755 files/uv "${ROOTFS_DIR}/tmp/uv"

on_chroot <<'EOF'
adduser --system --group --home /opt/minicam --no-create-home \
	--shell /usr/sbin/nologin minicam

cd /opt/minicam
export UV_PYTHON_INSTALL_DIR=/opt/minicam/.python
export UV_PYTHON_PREFERENCE=only-managed
export UV_CACHE_DIR=/tmp/uv-cache
export UV_LINK_MODE=copy
/tmp/uv python install 3.14
/tmp/uv sync --locked --no-dev --python 3.14
chown -R minicam:minicam /opt/minicam
EOF
rm -f "${ROOTFS_DIR}/tmp/uv"

files="${app}/image/files"
install -m 644 "${app}/deploy/minicam.service" "${ROOTFS_DIR}/etc/systemd/system/minicam.service"
install -d -m 755 "${ROOTFS_DIR}/etc/systemd/system/minicam.service.d"
install -m 644 "${files}/image.conf" "${ROOTFS_DIR}/etc/systemd/system/minicam.service.d/image.conf"
install -m 644 "${files}/minicam-firstboot.service" "${ROOTFS_DIR}/etc/systemd/system/minicam-firstboot.service"
install -m 755 "${files}/minicam-firstboot.sh" "${ROOTFS_DIR}/usr/local/sbin/minicam-firstboot"
install -m 644 "${files}/minicam.avahi.service" "${ROOTFS_DIR}/etc/avahi/services/minicam.service"

on_chroot <<'EOF'
systemctl enable minicam-firstboot.service minicam.service
EOF
