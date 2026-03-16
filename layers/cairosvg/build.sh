#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$SCRIPT_DIR/dist"

rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR/python"

docker run --rm -v "$DIST_DIR":/out -v "$SCRIPT_DIR":/build \
  public.ecr.aws/lambda/python:3.12 \
  bash -c "
    yum install -y cairo cairo-devel pango pango-devel gdk-pixbuf2 gdk-pixbuf2-devel libffi-devel &&
    pip install -r /build/requirements.txt -t /out/python &&
    # Copy Cairo shared libs
    mkdir -p /out/lib &&
    cp /usr/lib64/libcairo.so* /out/lib/ &&
    cp /usr/lib64/libpango*.so* /out/lib/ &&
    cp /usr/lib64/libgobject*.so* /out/lib/ &&
    cp /usr/lib64/libgdk_pixbuf*.so* /out/lib/ &&
    cp /usr/lib64/libffi.so* /out/lib/
  "

echo "Layer built at $DIST_DIR"
