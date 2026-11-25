#!/usr/bin/env bash
#
# Rebuild the macOS (Apple Silicon) binaries shipped in this repo:
#
#   openocd/bin/openocd + openocd/share/openocd   from src/
#   gdb/bin/gdb + gdb/share/gdb                   from gdb/src/ (GDB 18.1)
#
# Homebrew dylibs are copied into <dir>/lib and the binaries are pointed at
# them, so the result runs on a Mac without Homebrew.
#
#   ./build_macos.sh [openocd] [gdb]        (default: both)
#
# Build deps: brew install autoconf automake libtool pkgconf texinfo \
#                          libusb capstone jimtcl libjaylink gmp mpfr zstd
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$ROOT/.build"
JOBS="$(sysctl -n hw.ncpu)"
BREW="$(brew --prefix)"

# GNU binutils' ar/ranlib, if first on PATH, write archives ld64 rejects.
export AR=/usr/bin/ar RANLIB=/usr/bin/ranlib

# Copy every non-system dylib <exe> depends on (recursively) into <libdir>,
# rewrite load commands to @rpath, and re-sign everything ad-hoc.
bundle_dylibs() {
    local exe="$1" libdir="$2"
    rm -rf "$libdir"
    mkdir -p "$libdir"
    local queue=("$exe") f dep name
    while ((${#queue[@]})); do
        f="${queue[0]}"
        queue=("${queue[@]:1}")
        while read -r dep; do
            case "$dep" in
                /usr/lib/* | /System/* | @*) continue ;;
            esac
            name="$(basename "$dep")"
            if [[ ! -e "$libdir/$name" ]]; then
                cp -L "$dep" "$libdir/$name"
                chmod u+w "$libdir/$name"
                install_name_tool -id "@rpath/$name" "$libdir/$name" 2>/dev/null
                queue+=("$libdir/$name")
            fi
            install_name_tool -change "$dep" "@rpath/$name" "$f" 2>/dev/null
        done < <(otool -L "$f" | tail -n +2 | awk '{print $1}')
    done
    install_name_tool -add_rpath "@executable_path/../lib" "$exe" 2>/dev/null
    for f in "$libdir"/*.dylib "$exe"; do
        codesign --force --sign - "$f" 2>/dev/null
    done
    if otool -L "$exe" "$libdir"/*.dylib | grep -q "$BREW"; then
        echo "error: $exe still references $BREW" >&2
        exit 1
    fi
}

build_openocd() {
    echo "==> openocd"
    (cd "$ROOT/src" && { [[ -x configure ]] || ./bootstrap nosubmodule; })
    rm -rf "$BUILD/openocd"
    mkdir -p "$BUILD/openocd"
    cd "$BUILD/openocd"
    # jimtcl.pc pulls in readline and openssl (keg-only); use the openssl
    # libjim itself links against so only one copy gets bundled.
    local ssl
    ssl="$(otool -L "$BREW/opt/jimtcl/lib/libjim.dylib" | awk '/libssl/{print $1}')"
    ssl="$(dirname "$ssl")/pkgconfig"
    PKG_CONFIG_PATH="$BREW/opt/readline/lib/pkgconfig:$ssl:${PKG_CONFIG_PATH:-}" \
        "$ROOT/src/configure" --prefix=/opt/qcom-openocd --disable-werror \
        --enable-eud --enable-jlink --enable-ftdi --enable-dummy \
        --disable-doxygen-html MAKEINFO=true >configure.log
    make -j"$JOBS" >make.log
    make install DESTDIR="$BUILD/openocd/stage" MAKEINFO=true >install.log

    local out="$ROOT/openocd"
    rm -rf "$out/share/openocd" "$out/lib"
    mkdir -p "$out/bin" "$out/share"
    cp "$BUILD/openocd/stage/opt/qcom-openocd/bin/openocd" "$out/bin/openocd"
    cp -R "$BUILD/openocd/stage/opt/qcom-openocd/share/openocd" "$out/share/openocd"
    bundle_dylibs "$out/bin/openocd" "$out/lib"
    "$out/bin/openocd" --version 2>&1 | head -n 1
}

build_gdb() {
    echo "==> gdb"
    rm -rf "$BUILD/gdb"
    mkdir -p "$BUILD/gdb/build"
    cd "$BUILD/gdb/build"
    "$ROOT/gdb/src/configure" --prefix=/opt/qcom-gdb \
        --target=aarch64-elf --enable-targets=all --program-prefix= \
        --disable-werror --disable-nls --without-python \
        --disable-sim --disable-binutils --disable-ld --disable-gas \
        --disable-gprof --disable-gprofng --disable-gold --disable-inprocess-agent \
        --with-gmp="$BREW/opt/gmp" --with-mpfr="$BREW/opt/mpfr" >configure.log
    make -j"$JOBS" all-gdb >make.log
    make install-gdb DESTDIR="$BUILD/gdb/stage" >install.log

    local out="$ROOT/gdb"
    # keep gdb/bin/apss.gdb and friends; only the binary is rebuilt
    rm -rf "$out/bin/gdb" "$out/share" "$out/lib"
    mkdir -p "$out/bin" "$out/share"
    cp "$BUILD/gdb/stage/opt/qcom-gdb/bin/gdb" "$out/bin/gdb"
    cp -R "$BUILD/gdb/stage/opt/qcom-gdb/share/gdb" "$out/share/gdb"
    bundle_dylibs "$out/bin/gdb" "$out/lib"
    "$out/bin/gdb" --version | head -n 1
}

targets=("$@")
((${#targets[@]})) || targets=(openocd gdb)
for t in "${targets[@]}"; do
    case "$t" in
        openocd) build_openocd ;;
        gdb) build_gdb ;;
        *) echo "unknown target: $t" >&2; exit 1 ;;
    esac
done
