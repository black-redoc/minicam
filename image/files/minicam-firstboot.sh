#!/bin/sh
# First boot of the minicam image: create this device's own .env (a fresh
# JWT secret, never shipped in the public image) and give the Pi a unique
# hostname so several devices on one LAN don't collide on raspberrypi.local.
#
# Run by minicam-firstboot.service before minicam.service, only while
# /opt/minicam/.env does not exist. MINICAM_ROOT points it at another root
# directory (tests); by default it works on the running system.
set -eu

root=${MINICAM_ROOT:-}
app=$root/opt/minicam
default_hostname=raspberrypi

write_env() {
    env_file=$app/.env
    if [ -e "$env_file" ]; then
        return
    fi
    secret=$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')
    tmp=$env_file.tmp
    # 0600 from the start: the secret never sits in a world-readable file.
    (
        umask 077
        sed \
            -e "s/^MINICAM_JWT_SECRET=.*/MINICAM_JWT_SECRET=$secret/" \
            -e "s/^MINICAM_TRUSTED_PROXIES=.*/MINICAM_TRUSTED_PROXIES=127.0.0.1/" \
            "$app/.env.example" >"$tmp"
        printf '\n# Set by the minicam image on first boot.\n' >>"$tmp"
        printf 'MINICAM_SETUP_CLAIM=led\nMINICAM_WIFI_PROVISIONING=nm\n' >>"$tmp"
    )
    if [ "$(id -u)" = 0 ]; then
        chown minicam:minicam "$tmp"
    fi
    mv "$tmp" "$env_file"
}

# Last 4 hex digits of the first wired (else any) interface's MAC address.
mac_suffix() {
    for iface in "$root"/sys/class/net/eth* "$root"/sys/class/net/end* "$root"/sys/class/net/*; do
        [ -r "$iface/address" ] || continue
        case ${iface##*/} in lo) continue ;; esac
        mac=$(cat "$iface/address")
        [ "$mac" = 00:00:00:00:00:00 ] && continue
        echo "$mac" | tr -d ':' | tail -c 5 | tr -d '\n'
        return
    done
    od -An -tx1 -N2 /dev/urandom | tr -d ' \n'
}

rename_host() {
    current=$(cat "$root/etc/hostname" 2>/dev/null || true)
    # A hostname chosen in Raspberry Pi Imager is the user's: keep it.
    if [ "$current" != "$default_hostname" ]; then
        return
    fi
    new=minicam-$(mac_suffix)
    echo "$new" >"$root/etc/hostname"
    sed "s/^127\.0\.1\.1[[:space:]].*/127.0.1.1	$new/" "$root/etc/hosts" >"$root/etc/hosts.tmp"
    cat "$root/etc/hosts.tmp" >"$root/etc/hosts"
    rm "$root/etc/hosts.tmp"
    if [ -z "$root" ]; then
        hostname "$new" || echo "hostname $new applies on next boot" >&2
        # Avahi reads the hostname at start; announce minicam-XXXX.local now.
        systemctl try-restart avahi-daemon.service || true
    fi
}

write_env
rename_host
