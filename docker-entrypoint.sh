#!/bin/sh
set -eu

# Bind mounts are attached after the image is built, so their ownership cannot
# be fixed by the Dockerfile's build-time chown.
chown -R appuser:appuser /app/data /app/diagnostics

exec gosu appuser "$@"
