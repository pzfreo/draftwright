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
Install draftwright and draftwright-mcp for macOS arm64 or Linux x86_64 (glibc >= 2.35).

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
    Linux/x86_64) target=x86_64-unknown-linux-gnu ;;
    *) die "Unsupported platform: $os/$arch. See $releases or use 'uv tool install draftwright' / 'cargo install draftwright'." ;;
esac

if [ "$os" = Linux ]; then
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
        die "Linux requires glibc >= 2.35 (detected: ${glibc_version:-unknown}). A static Linux build is planned; meanwhile use the Python package: uv tool install draftwright."
    fi
fi

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
mkdir -p "$install_dir"
install_dir=$(cd "$install_dir" && pwd -P)
# Staging on the destination filesystem makes each final rename atomic.
tmp_dir=$(mktemp -d "$install_dir/.draftwright-install.XXXXXX")
trap 'rm -rf "$tmp_dir"' 0
trap 'exit 1' HUP INT TERM
cd "$tmp_dir"

download() {
    if [ "$downloader" = curl ]; then
        curl -fsSL -o "$2" "$1" || die "Could not download $1"
    else
        wget -q -O "$2" "$1" || die "Could not download $1"
    fi
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

name=draftwright-$version-$target
asset=$name.tar.gz
printf 'Installing draftwright %s for %s\n' "$version" "$target"
download "$releases/download/$version/$asset" "$asset"
download "$releases/download/$version/$asset.sha256" "$asset.sha256"

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

tar -xzf "$asset" "$name/draftwright" "$name/draftwright-mcp"
for binary in draftwright draftwright-mcp; do
    [ -f "$name/$binary" ] && [ ! -L "$name/$binary" ] || die "Archive is missing a regular file: $binary"
    [ ! -d "$install_dir/$binary" ] || die "Destination is a directory: $install_dir/$binary"
    chmod 755 "$name/$binary"
done
for binary in draftwright draftwright-mcp; do
    mv -f "$name/$binary" "$install_dir/$binary"
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
