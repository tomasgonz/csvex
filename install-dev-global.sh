#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"
TARGET_BIN_DIR="${TARGET_BIN_DIR:-/usr/local/bin}"
PARENT_BIN_DIR="$(dirname "${TARGET_BIN_DIR}")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is required." >&2
    exit 1
fi

if [ ! -d "${VENV_DIR}" ]; then
    echo "Creating virtual environment at ${VENV_DIR}"
    python3 -m venv "${VENV_DIR}"
fi

echo "Installing csvex in editable mode"
"${VENV_DIR}/bin/pip" install -e "${REPO_ROOT}"

privileged_prefix=()
needs_privileged_write=0
if [ -d "${TARGET_BIN_DIR}" ]; then
    if [ ! -w "${TARGET_BIN_DIR}" ]; then
        needs_privileged_write=1
    fi
elif [ ! -w "${PARENT_BIN_DIR}" ]; then
    needs_privileged_write=1
fi

if [ "${needs_privileged_write}" -eq 1 ]; then
    if command -v sudo >/dev/null 2>&1; then
        privileged_prefix=(sudo)
    else
        echo "Error: ${TARGET_BIN_DIR} is not writable and sudo is not available." >&2
        exit 1
    fi
fi

"${privileged_prefix[@]}" mkdir -p "${TARGET_BIN_DIR}"

echo "Linking commands into ${TARGET_BIN_DIR}"
"${privileged_prefix[@]}" ln -sf "${VENV_DIR}/bin/csvex" "${TARGET_BIN_DIR}/csvex"
"${privileged_prefix[@]}" ln -sf "${VENV_DIR}/bin/c" "${TARGET_BIN_DIR}/c"

echo
echo "Installed commands:"
echo "  ${TARGET_BIN_DIR}/csvex -> ${VENV_DIR}/bin/csvex"
echo "  ${TARGET_BIN_DIR}/c -> ${VENV_DIR}/bin/c"
echo
echo "You can now run 'csvex' or 'c' from anywhere."
echo "Code changes in ${REPO_ROOT} take effect immediately."
echo "Rerun this script only after changing dependencies or CLI entry points."
