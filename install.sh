#!/bin/sh
set -eu

releases=https://github.com/pzfreo/draftwright/releases

die() {
    printf 'draftwright installer: %s\n' "$*" >&2
    exit 1
}

if [ "$#" -gt 0 ]; then
    [ "$#" -eq 1 ] && [ "$1" = --help ] || die 'Usage: sh install.sh [--help]'
    cat <<'EOF'
Install draftwright and draftwright-mcp for macOS or Linux (arm64 / x86_64).
Linux uses static binaries; older x86_64 releases require glibc >= 2.35.

Usage: sh install.sh [--help]
  DRAFTWRIGHT_VERSION      Release to install, e.g. v0.6.0 or 0.6.0 (default: latest stable)
  DRAFTWRIGHT_INSTALL_DIR  Destination (default: $HOME/.local/bin); no sudo is used
EOF
    exit 0
fi

os=$(uname -s)
arch=$(uname -m)
case "$os/$arch" in
    Darwin/arm64|Darwin/aarch64) target=aarch64-apple-darwin ;;
    Darwin/x86_64)
        if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || true)" = 1 ]; then
            target=aarch64-apple-darwin
        else
            target=x86_64-apple-darwin
        fi
        ;;
    Linux/x86_64) target=x86_64-unknown-linux-musl ;;
    Linux/aarch64|Linux/arm64) target=aarch64-unknown-linux-musl ;;
    *) die "Unsupported platform: $os/$arch. See $releases or use 'uv tool install draftwright' / 'cargo install draftwright'." ;;
esac

check_glibc() {
    glibc_version=
    if command -v getconf >/dev/null 2>&1; then
        libc=$(getconf GNU_LIBC_VERSION 2>/dev/null || true)
        case "$libc" in 'glibc '*) glibc_version=${libc#glibc } ;; esac
    fi
    if [ -z "$glibc_version" ] && command -v ldd >/dev/null 2>&1; then
        libc=$(ldd --version 2>&1 || true)
        glibc_version=$(printf '%s\n' "$libc" | awk 'NR == 1 && /GLIBC|GNU libc/ { print $NF }')
    fi
    if ! printf '%s\n' "$glibc_version" | awk -F. '
        NF >= 2 && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ {
            if ($1 > 2 || ($1 == 2 && $2 >= 35)) exit 0
        }
        { exit 1 }
    '; then
        die "This release has no static Linux archive. Its GNU build requires glibc >= 2.35 (detected: ${glibc_version:-unknown}). Use a newer release with static binaries or the Python package: uv tool install draftwright."
    fi
}

if command -v curl >/dev/null 2>&1; then
    downloader=curl
elif command -v wget >/dev/null 2>&1; then
    downloader=wget
else
    die 'Install curl or wget first.'
fi
if command -v sha256sum >/dev/null 2>&1; then
    checksum=sha256sum
elif command -v shasum >/dev/null 2>&1; then
    checksum=shasum
else
    die 'Install sha256sum or shasum first; checksum verification is required.'
fi

install_dir=${DRAFTWRIGHT_INSTALL_DIR:-$HOME/.local/bin}
case "$install_dir" in /*) ;; *) install_dir=$(pwd)/$install_dir ;; esac
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/draftwright-install.XXXXXX")
tmp_dir=$(cd "$tmp_dir" && pwd -P)
stage_dir=
trap 'rm -rf "$tmp_dir"; if [ -n "$stage_dir" ]; then rm -rf "$stage_dir"; fi' 0
trap 'exit 1' HUP INT TERM
cd "$tmp_dir"

download() {
    # Only HTTP 404 means an old release lacks this target. Never mask other errors.
    if [ "$downloader" = curl ]; then
        if status=$(curl -fsSL -o "$2" -w '%{http_code}' "$1" 2>download.error); then return 0; fi
        [ "$status" != 404 ] || return 44
        cat download.error >&2
    else
        if wget -q --server-response -O "$2" "$1" 2>download.headers; then return 0; fi
        status=$(awk '/^[[:space:]]*HTTP\// { code = $2 } END { print code }' download.headers)
        [ "$status" != 404 ] || return 44
    fi
    return 1
}

version=${DRAFTWRIGHT_VERSION:-}
if [ -z "$version" ]; then
    if [ "$downloader" = curl ]; then
        latest_url=$(curl -fsSL -o /dev/null -w '%{url_effective}' "$releases/latest") || die 'Could not resolve the latest release.'
    else
        wget -q --server-response -O /dev/null "$releases/latest" 2>latest.headers || die 'Could not resolve the latest release.'
        latest_url=$(awk 'tolower($1) == "location:" { sub("\r$", "", $2); url = $2 } END { print url }' latest.headers)
    fi
    case "$latest_url" in
        "$releases/tag/"*) version=${latest_url#"$releases/tag/"} ;;
        *) die "Could not read a release tag from $latest_url" ;;
    esac
fi
case "$version" in v*) ;; *) version=v$version ;; esac
case "$version" in v[0-9]*) ;; *) die "Invalid release version: $version" ;; esac
case "$version" in *[!a-zA-Z0-9.+-]*) die "Invalid release version: $version" ;; esac

set_asset() {
    name=draftwright-$version-$target
    asset=$name.tar.gz
}
set_asset
printf 'Installing draftwright %s for %s\n' "$version" "$target"
if download "$releases/download/$version/$asset" "$asset"; then
    :
else
    result=$?
    [ "$result" -eq 44 ] || die "Could not download $asset"
    if [ "$os/$arch" = Linux/x86_64 ]; then
        check_glibc
        target=x86_64-unknown-linux-gnu
        set_asset
        printf 'No static archive in %s; falling back to %s\n' "$version" "$target"
        download "$releases/download/$version/$asset" "$asset" || die "Could not download $asset. See $releases"
    else
        die "No archive for $target in $version. See $releases or use 'uv tool install draftwright' / 'cargo install draftwright'."
    fi
fi
download "$releases/download/$version/$asset.sha256" "$asset.sha256" || die "Could not download the checksum for $asset"

# Accept only the single checksum for this archive, never paths from a manifest.
expected=$(awk -v asset="$asset" '
    NR == 1 && NF == 2 && length($1) == 64 && $1 !~ /[^0-9a-fA-F]/ && $2 == asset {
        print tolower($1); valid = 1
    }
    END { if (NR != 1 || !valid) exit 1 }
' "$asset.sha256") || die "Invalid checksum file for $asset"
if [ "$checksum" = sha256sum ]; then
    actual=$(sha256sum "$asset")
else
    actual=$(shasum -a 256 "$asset")
fi
[ "${actual%% *}" = "$expected" ] || die "Checksum mismatch for $asset; nothing installed."

mkdir -p "$install_dir"
install_dir=$(cd "$install_dir" && pwd -P)
# Staging on the destination filesystem makes each final rename atomic.
stage_dir=$(mktemp -d "$install_dir/.draftwright-install.XXXXXX")
tar -xzf "$asset" -C "$stage_dir" "$name/draftwright" "$name/draftwright-mcp"
for binary in draftwright draftwright-mcp; do
    [ -f "$stage_dir/$name/$binary" ] && [ ! -L "$stage_dir/$name/$binary" ] || die "Archive is missing a regular file: $binary"
    [ ! -d "$install_dir/$binary" ] || die "Destination is a directory: $install_dir/$binary"
    chmod 755 "$stage_dir/$name/$binary"
done
for binary in draftwright draftwright-mcp; do
    mv -f "$stage_dir/$name/$binary" "$install_dir/$binary"
done

"$install_dir/draftwright" --version
printf 'Installed draftwright and draftwright-mcp to %s\n' "$install_dir"
case ":${PATH:-}:" in
    *":$install_dir:"*) ;;
    *) printf 'Add %s to your PATH to run draftwright.\n' "$install_dir" ;;
esac
cat <<'EOF'
Shell completions (create the destination directory first):
draftwright completions zsh  > ~/.zfunc/_draftwright                       # zsh
draftwright completions bash > ~/.bash_completions/draftwright.sh          # bash
draftwright completions fish > ~/.config/fish/completions/draftwright.fish # fish
EOF
