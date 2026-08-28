#!/usr/bin/env bash
# Bring the Deno and uv pins in the Dockerfile up to the latest upstream release.
# Dependabot has no way to read these, since they are ARG values rather than a
# manifest. See #323.
set -euo pipefail

DOCKERFILE="${1:-Dockerfile}"

current_deno=$(grep -oE '^ARG DENO_VERSION=v[0-9][0-9.]*' "$DOCKERFILE" | cut -d= -f2)
current_uv=$(grep -oE 'uv==[0-9][0-9.]*' "$DOCKERFILE" | head -1 | cut -d= -f3)

latest_deno=$(curl -fsSL https://api.github.com/repos/denoland/deno/releases/latest | jq -r .tag_name)
latest_uv=$(curl -fsSL https://pypi.org/pypi/uv/json | jq -r .info.version)

for value in "$current_deno" "$current_uv" "$latest_deno" "$latest_uv"; do
  if [ -z "$value" ] || [ "$value" = "null" ]; then
    echo "could not read a version: deno ${current_deno}->${latest_deno} uv ${current_uv}->${latest_uv}" >&2
    exit 1
  fi
done

changed=""

if [ "$current_deno" != "$latest_deno" ]; then
  base="https://github.com/denoland/deno/releases/download/${latest_deno}"
  sha_x86=$(curl -fsSL "${base}/deno-x86_64-unknown-linux-gnu.zip.sha256sum" | awk '{print $1}')
  sha_arm=$(curl -fsSL "${base}/deno-aarch64-unknown-linux-gnu.zip.sha256sum" | awk '{print $1}')
  # Never write a pin without the checksum that proves it
  if [ ${#sha_x86} -ne 64 ] || [ ${#sha_arm} -ne 64 ]; then
    echo "checksums for ${latest_deno} are missing or malformed" >&2
    exit 1
  fi
  sed -i.bak -E \
    -e "s|^ARG DENO_VERSION=.*|ARG DENO_VERSION=${latest_deno}|" \
    -e "s|^ARG DENO_SHA256_X86_64=.*|ARG DENO_SHA256_X86_64=${sha_x86}|" \
    -e "s|^ARG DENO_SHA256_AARCH64=.*|ARG DENO_SHA256_AARCH64=${sha_arm}|" \
    "$DOCKERFILE"
  changed="${changed}deno ${current_deno} to ${latest_deno}
"
fi

if [ "$current_uv" != "$latest_uv" ]; then
  sed -i.bak -E "s|uv==[0-9][0-9.]*|uv==${latest_uv}|" "$DOCKERFILE"
  changed="${changed}uv ${current_uv} to ${latest_uv}
"
fi

rm -f "${DOCKERFILE}.bak"

if [ -z "$changed" ]; then
  echo "pins are current: deno ${current_deno}, uv ${current_uv}"
else
  printf '%s' "$changed"
fi
