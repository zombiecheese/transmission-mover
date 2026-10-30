#!/bin/sh
set -eu

DEFAULT_STATIC_DIR="/opt/default-static"
TARGET_STATIC_DIR="/app/static"

# Seed the mounted static directory on first run only.
if [ -d "$DEFAULT_STATIC_DIR" ] && [ -d "$TARGET_STATIC_DIR" ] && [ -z "$(ls -A "$TARGET_STATIC_DIR")" ]; then
  cp -R "$DEFAULT_STATIC_DIR"/. "$TARGET_STATIC_DIR"/
  echo "Seeded static assets into $TARGET_STATIC_DIR"
fi

# When the image ships a different UI than the mounted dir, back up the old files and refresh them.
if [ -d "$DEFAULT_STATIC_DIR" ] && [ -d "$TARGET_STATIC_DIR" ]; then
  IMAGE_HASH="$(cd "$DEFAULT_STATIC_DIR" && find . -type f ! -name '.image-static-hash' | sort | xargs cat | sha256sum | cut -d' ' -f1)"
  MARKER="$TARGET_STATIC_DIR/.image-static-hash"
  if [ ! -f "$MARKER" ] || [ "$(cat "$MARKER")" != "$IMAGE_HASH" ]; then
    BACKUP_DIR="$TARGET_STATIC_DIR/.backup/$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$BACKUP_DIR"
    for f in "$DEFAULT_STATIC_DIR"/*; do
      name="$(basename "$f")"
      [ -e "$TARGET_STATIC_DIR/$name" ] && cp -R "$TARGET_STATIC_DIR/$name" "$BACKUP_DIR/"
    done
    cp -R "$DEFAULT_STATIC_DIR"/. "$TARGET_STATIC_DIR"/
    echo "$IMAGE_HASH" > "$MARKER"
    echo "Refreshed static assets in $TARGET_STATIC_DIR (previous files backed up to $BACKUP_DIR)"
  fi
fi

exec "$@"
