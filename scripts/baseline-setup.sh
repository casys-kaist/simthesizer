#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

REPO_URL="https://github.com/casys-kaist/LLMServingSim"
REPO_COMMIT="${REPO_COMMIT:-9e3f0ece7cab1393f176a74d38601ef07103f2d3}"
TARGET_DIR="${ROOT_DIR}/extern/llmservingsim"
ASTRA_DIR="${TARGET_DIR}/astra-sim"
PATCH_DIR="${ROOT_DIR}/patches"

if [[ -e "${TARGET_DIR}" && ! -d "${TARGET_DIR}/.git" ]]; then
  echo "error: ${TARGET_DIR} exists but is not a git repository" >&2
  exit 1
fi

mkdir -p "${ROOT_DIR}/extern"

if [[ ! -d "${TARGET_DIR}/.git" ]]; then
  git clone --recursive "${REPO_URL}" "${TARGET_DIR}"
fi

git -C "${TARGET_DIR}" fetch --all --tags --prune
if ! git -C "${TARGET_DIR}" rev-parse --verify --quiet "${REPO_COMMIT}^{commit}" >/dev/null; then
  echo "error: configured REPO_COMMIT does not exist in ${REPO_URL}: ${REPO_COMMIT}" >&2
  exit 1
fi
git -C "${TARGET_DIR}" checkout --detach "${REPO_COMMIT}"
git -C "${TARGET_DIR}" submodule sync --recursive
git -C "${TARGET_DIR}" submodule update --init --recursive

if [[ ! -d "${PATCH_DIR}" ]]; then
  echo "error: patch directory not found: ${PATCH_DIR}" >&2
  exit 1
fi

mapfile -d '' patch_files < <(find "${PATCH_DIR}" -type f -name '*.patch' -print0 | sort -z)

if [[ ${#patch_files[@]} -eq 0 ]]; then
  echo "No patches found under ${PATCH_DIR}"
  exit 0
fi

for patch in "${patch_files[@]}"; do
  rel_patch="${patch#"${ROOT_DIR}/"}"
  if git -C "${TARGET_DIR}" apply --check "${patch}" >/dev/null 2>&1; then
    git -C "${TARGET_DIR}" apply "${patch}"
    echo "applied: ${rel_patch}"
    continue
  fi

  if git -C "${TARGET_DIR}" apply -R --check "${patch}" >/dev/null 2>&1; then
    echo "skipped (already applied): ${rel_patch}"
    continue
  fi

  echo "error: failed to apply patch: ${rel_patch}" >&2
  exit 1
done

if ! command -v protoc >/dev/null 2>&1; then
  echo "error: protoc is required but not found in PATH" >&2
  echo "hint: install protobuf compiler (e.g. apt: protobuf-compiler)" >&2
  exit 1
fi

(
  cd "${ASTRA_DIR}"
  bash build/astra_analytical/build.sh -l
  bash build/astra_analytical/build.sh -t congestion_unaware
)

echo "LLMServingSim is ready at ${TARGET_DIR} (commit ${REPO_COMMIT})"
