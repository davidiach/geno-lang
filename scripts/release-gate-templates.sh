#!/usr/bin/env bash
# release-gate-templates.sh
# Scaffold every init template and verify it passes check + test.
# Exit non-zero on any failure so this can gate a release.

set -euo pipefail

PYTHON_BIN="${PYTHON:-python3}"
TMPDIR=$(mktemp -d)
FAILED=0

cleanup() {
  rm -rf "$TMPDIR"
}
trap cleanup EXIT

for template in minimal cli web api lib; do
  echo "=== Template: $template ==="

  cd "$TMPDIR"
  "$PYTHON_BIN" -m geno init "$template" --template "$template" 2>&1
  if [ $? -ne 0 ]; then
    echo "ERROR: geno init --template $template failed"
    FAILED=$((FAILED + 1))
    continue
  fi

  DIR="$TMPDIR/$template"

  # Find the main file
  if [ -f "$DIR/Main.geno" ]; then
    MAIN="$DIR/Main.geno"
  elif [ -f "$DIR/Lib.geno" ]; then
    MAIN="$DIR/Lib.geno"
  else
    echo "ERROR: No Main.geno or Lib.geno in template $template"
    FAILED=$((FAILED + 1))
    continue
  fi

  echo "  check..."
  "$PYTHON_BIN" -m geno check "$MAIN" 2>&1 || { echo "ERROR: check failed for $template"; FAILED=$((FAILED + 1)); continue; }

  echo "  test..."
  "$PYTHON_BIN" -m geno test "$MAIN" 2>&1 || { echo "ERROR: test failed for $template"; FAILED=$((FAILED + 1)); continue; }

  # Browser templates: build and verify game loop bootstrap
  if [ "$template" = "web" ]; then
    echo "  build (browser)..."
    OUT="$TMPDIR/${template}_out.html"
    "$PYTHON_BIN" -m geno build "$MAIN" --single-file -o "$OUT" 2>&1 || { echo "ERROR: build failed for $template"; FAILED=$((FAILED + 1)); continue; }

    if ! grep -q "requestAnimationFrame" "$OUT"; then
      echo "ERROR: built web output missing requestAnimationFrame game loop"
      FAILED=$((FAILED + 1))
      continue
    fi
    echo "  browser bootstrap verified"
  fi

  echo "  PASSED"
done

if [ "$FAILED" -gt 0 ]; then
  echo "ERROR: $FAILED template(s) failed"
  exit 1
fi

echo "All templates passed."
