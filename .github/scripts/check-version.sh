#!/bin/sh
# Fail unless a release tag (vX.Y.Z) matches the version in pyproject.toml,
# so every published image says which minicam it contains.
#
# Usage: check-version.sh <tag> [pyproject.toml]
# Used by the "Check version" step in .github/workflows/image.yml.
set -eu

tag=${1:?usage: check-version.sh <tag> [pyproject.toml]}
pyproject=${2:-pyproject.toml}

# The first `version = "..."` line is the [project] table's.
version=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$pyproject" | head -n 1)

case $tag in
v[0-9]*.[0-9]*.[0-9]*) ;;
*)
    echo "tag '$tag' is not vX.Y.Z" >&2
    exit 1
    ;;
esac

if [ "$tag" != "v$version" ]; then
    echo "tag '$tag' does not match pyproject.toml version '$version'" >&2
    exit 1
fi
echo "version $version"
