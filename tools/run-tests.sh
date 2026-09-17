#!/bin/sh
# Run the test suite.
#
# The tests need homeassistant, voluptuous, franklinwh and pytest. A running
# Home Assistant container already has all four, so by default we copy the
# integration plus the tests into it and run there — no installs, works offline.
# Override with FWH_CONTAINER=<name>. Set DOCKER=podman if you use podman.
#
# Note: never run pytest with the repo root as the working directory. This repo
# ships select.py at the top level (hacs.json content_in_root), which shadows
# Python's stdlib `select` module and breaks the interpreter on import. The
# tests are run from a neutral directory, and tests/conftest.py imports the
# integration as the package `franklin_wh` via a symlink instead.
set -eu

DOCKER="${DOCKER:-docker}"
CONTAINER="${FWH_CONTAINER:-ix-home-assistant-home-assistant-1}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/tmp/fwh-src

if ! $DOCKER inspect "$CONTAINER" >/dev/null 2>&1; then
    echo "Container '$CONTAINER' not found. Set FWH_CONTAINER to a running" >&2
    echo "Home Assistant container, or run the tests in CI (see" >&2
    echo ".github/workflows/tests.yaml) which pip-installs the dependencies." >&2
    exit 1
fi

$DOCKER exec "$CONTAINER" sh -c "rm -rf $DEST && mkdir -p $DEST"
for f in __init__.py sensor.py select.py number.py modbus.py switch.py \
         config_flow.py manifest.json strings.json; do
    $DOCKER cp "$REPO/$f" "$CONTAINER:$DEST/$f" >/dev/null
done
$DOCKER cp "$REPO/tests" "$CONTAINER:$DEST/tests" >/dev/null

# -w /tmp keeps the repo root off sys.path (see note above).
$DOCKER exec -w /tmp "$CONTAINER" python -m pytest "$DEST/tests" -q "$@"
