#!/bin/sh
# Run the test suite.
#
# The tests need homeassistant, voluptuous, franklinwh and pytest. A running
# Home Assistant container already has all four, so by default we copy the
# integration plus the tests into it and run there — no installs, works offline.
# Override with FWH_CONTAINER=<name>. Set DOCKER=podman if you use podman.
#
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
$DOCKER cp "$REPO/custom_components" "$CONTAINER:$DEST/custom_components" >/dev/null
$DOCKER cp "$REPO/tests" "$CONTAINER:$DEST/tests" >/dev/null
$DOCKER exec -w "$DEST" "$CONTAINER" python -m pytest tests -q "$@"
