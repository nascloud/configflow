#!/bin/sh
# 内置 Sub-Store 启动脚本：在镜像自带版本和在线更新版本（数据目录）中取较新的一个
set -e

BUILTIN_DIR="${SUB_STORE_BUILTIN_DIR:-/opt/sub-store}"
RUNTIME_DIR="${DATA_DIR:-/data}/sub-store/runtime"
BUNDLE="$BUILTIN_DIR/sub-store.bundle.js"

if [ -f "$RUNTIME_DIR/sub-store.bundle.js" ] && [ -f "$RUNTIME_DIR/VERSION" ]; then
    builtin_version="$(cat "$BUILTIN_DIR/VERSION" 2>/dev/null || true)"
    runtime_version="$(cat "$RUNTIME_DIR/VERSION")"
    newest="$(printf '%s\n%s\n' "$builtin_version" "$runtime_version" | sort -V | tail -n 1)"
    if [ "$runtime_version" != "$builtin_version" ] && [ "$newest" = "$runtime_version" ]; then
        BUNDLE="$RUNTIME_DIR/sub-store.bundle.js"
    fi
fi

mkdir -p "$SUB_STORE_DATA_BASE_PATH"
echo "Starting Sub-Store from $BUNDLE"
exec node "$BUNDLE"
