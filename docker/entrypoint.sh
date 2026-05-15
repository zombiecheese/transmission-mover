#!/bin/sh
set -eu

DEFAULT_STATIC_DIR="/opt/default-static"
TARGET_STATIC_DIR="/app/static"

# Seed the mounted static directory on first run only.
if [ -d "$DEFAULT_STATIC_DIR" ] && [ -d "$TARGET_STATIC_DIR" ] && [ -z "$(ls -A "$TARGET_STATIC_DIR")" ]; then
  cp -R "$DEFAULT_STATIC_DIR"/. "$TARGET_STATIC_DIR"/
  echo "Seeded static assets into $TARGET_STATIC_DIR"
fi

exec "$@"
