#!/usr/bin/env bash
# release-gate-vscode.sh
# Build and package the VS Code extension as a release smoke test.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXT_DIR="$ROOT_DIR/vscode-geno"
OUT_DIR="$(mktemp -d)"
PYTHON_BIN="${PYTHON:-python3}"
trap 'rm -rf "$OUT_DIR"' EXIT

if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: node is required for the VS Code extension release gate" >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm is required for the VS Code extension release gate" >&2
  exit 1
fi

pushd "$EXT_DIR" >/dev/null

echo "Installing VS Code extension dependencies from package-lock.json..."
npm ci

echo "Running VS Code extension TypeScript tests..."
npm test

VERSION="$(node -p "require('./package.json').version")"
VSIX_PATH="$OUT_DIR/geno-$VERSION.vsix"

echo "Packaging VS Code extension..."
npm run package -- --no-dependencies --out "$VSIX_PATH"

if [ ! -f "$VSIX_PATH" ]; then
  echo "ERROR: expected VSIX package was not created: $VSIX_PATH" >&2
  exit 1
fi

"$PYTHON_BIN" - <<'PY' "$VSIX_PATH"
import sys
import zipfile

vsix_path = sys.argv[1]
with zipfile.ZipFile(vsix_path) as archive:
    names = set(archive.namelist())
required = {
    "extension/package.json",
    "extension/out/extension.js",
    "extension/out/lspStatus.js",
    "extension.vsixmanifest",
}
missing = sorted(required - names)
if missing:
    raise SystemExit(
        "ERROR: packaged VSIX is missing expected files: " + ", ".join(missing)
    )
forbidden = {
    "extension/out/lspStatus.test.js",
    "extension/out/shellEscape.test.js",
    "extension/scripts/check-node-version.js",
}
present = sorted(forbidden & names)
if present:
    raise SystemExit(
        "ERROR: packaged VSIX includes test or build-only files: " + ", ".join(present)
    )
print(f"VS Code extension package smoke OK: {vsix_path}")
PY

popd >/dev/null
