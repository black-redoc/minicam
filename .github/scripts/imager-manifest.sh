#!/bin/sh
# Print a Raspberry Pi Imager repository (os_list JSON) listing one minicam
# image. Imager only offers OS customisation (user, Wi-Fi, SSH, hostname)
# for images whose repository entry declares how they apply it; the image
# keeps Raspberry Pi OS's cloud-init, so it declares "cloudinit-rpi" like the
# official Lite image.
#
# Usage: imager-manifest.sh <tag> <image.img> <image.img.xz> <download URL>
# Used by the "Checksum" step in .github/workflows/image.yml.
set -eu

tag=${1:?usage: imager-manifest.sh <tag> <image.img> <image.img.xz> <url>}
img=${2:?}
xz=${3:?}
url=${4:?}

extract_size=$(wc -c <"$img" | tr -d ' ')
extract_sha256=$(sha256sum "$img" | cut -d' ' -f1)
download_size=$(wc -c <"$xz" | tr -d ' ')
download_sha256=$(sha256sum "$xz" | cut -d' ' -f1)
release_date=$(date -u +%Y-%m-%d)

cat <<EOF
{
  "os_list": [
    {
      "name": "minicam $tag",
      "description": "Raspberry Pi OS Lite (64-bit, trixie) with minicam preinstalled",
      "url": "$url",
      "extract_size": $extract_size,
      "extract_sha256": "$extract_sha256",
      "image_download_size": $download_size,
      "image_download_sha256": "$download_sha256",
      "release_date": "$release_date",
      "init_format": "cloudinit-rpi",
      "devices": ["pi5-64bit", "pi4-64bit"],
      "capabilities": []
    }
  ]
}
EOF
