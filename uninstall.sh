#!/usr/bin/env bash
# Remove netriage from this machine.
#
#   sudo bash uninstall.sh              remove the command and the package
#   sudo bash uninstall.sh --purge      also remove the project directory
#   sudo bash uninstall.sh --purge-logs also remove the saved reports
#
# Saved reports live in ~/netriage/netriage_logs and are kept by default: they
# are the record of what the network looked like, and reinstalling does not
# bring them back.
set -euo pipefail

PURGE=0
PURGE_LOGS=0
for arg in "$@"; do
    case "$arg" in
        --purge)      PURGE=1 ;;
        --purge-logs) PURGE_LOGS=1 ;;
        -h|--help)    sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 1 ;;
    esac
done

removed=0
say() { printf '  %s\n' "$1"; }
as_root() {
    if [ -w "$(dirname "$1")" ]; then "$@"; else sudo "$@"; fi
}

# Ask Python where its user installs live, rather than guessing the layout.
PY_USER_SITE=""
PY_USER_SCRIPTS=""
if command -v python3 >/dev/null 2>&1; then
    PY_USER_SITE="$(python3 -c 'import site; print(site.getusersitepackages())' 2>/dev/null || true)"
    PY_USER_SCRIPTS="$(python3 -c 'import sysconfig; print(sysconfig.get_path("scripts", scheme="posix_user"))' 2>/dev/null || true)"
fi

echo "removing the command"
candidates="/usr/local/bin/netriage $HOME/.local/bin/netriage $HOME/bin/netriage"
[ -n "$PY_USER_SCRIPTS" ] && candidates="$candidates $PY_USER_SCRIPTS/netriage"
for path in $candidates; do
    [ -e "$path" ] || continue
    as_root rm -f "$path"
    say "removed $path"
    removed=1
done

echo "removing the package"
shopt -s nullglob
dirs=(
    /usr/local/lib/python*/dist-packages/netriage
    /usr/local/lib/python*/site-packages/netriage
    /usr/local/lib/python*/dist-packages/netriage-*.dist-info
    /usr/local/lib/python*/site-packages/netriage-*.dist-info
    "$HOME"/.local/lib/python*/site-packages/netriage
    "$HOME"/.local/lib/python*/site-packages/netriage-*.dist-info
)
[ -n "$PY_USER_SITE" ] && dirs+=("$PY_USER_SITE/netriage" "$PY_USER_SITE"/netriage-*.dist-info)
for dir in "${dirs[@]}"; do
    [ -e "$dir" ] || continue
    as_root rm -rf "$dir"
    say "removed $dir"
    removed=1
done
shopt -u nullglob

echo "removing PATH entries added by the installer"
for rc in "$HOME/.profile" "$HOME/.bashrc" "$HOME/.zprofile" "$HOME/.zshrc"; do
    [ -f "$rc" ] || continue
    grep -q 'added by the netriage installer' "$rc" || continue
    cp "$rc" "$rc.netriage-uninstall.bak"
    # drop the marker line and the five lines that follow it
    awk '
        /# added by the netriage installer/ { skip = 6; next }
        skip > 0 { skip--; next }
        { print }
    ' "$rc.netriage-uninstall.bak" > "$rc"
    rm -f "$rc.netriage-uninstall.bak"
    say "cleaned $rc"
    removed=1
done

if [ "$PURGE" -eq 1 ]; then
    echo "removing the project directory"
    for dir in "$HOME/netriage-src" "$HOME/netriage"; do
        [ -d "$dir" ] || continue
        # never delete saved reports by accident
        if [ -d "$dir/netriage_logs" ] && [ "$PURGE_LOGS" -eq 0 ]; then
            say "kept $dir/netriage_logs (use --purge-logs to delete it)"
            keep="$HOME/netriage-logs-kept"
            mkdir -p "$keep"
            mv "$dir/netriage_logs/"* "$keep/" 2>/dev/null || true
            say "moved saved reports to $keep"
        fi
        rm -rf "$dir"
        say "removed $dir"
        removed=1
    done
fi

if [ "$PURGE_LOGS" -eq 1 ]; then
    echo "removing saved reports"
    rm -rf "$HOME/netriage/netriage_logs"
    say "removed ~/netriage/netriage_logs"
    rmdir "$HOME/netriage" 2>/dev/null || true
    removed=1
fi

echo
if command -v netriage >/dev/null 2>&1; then
    echo "warning: 'netriage' is still on PATH at $(command -v netriage)" >&2
    echo "         another copy exists (pipx? a venv?). Check with:" >&2
    echo "         python3 -c 'import netriage; print(netriage.__file__)'" >&2
    exit 1
fi

if [ "$removed" -eq 0 ]; then
    echo "nothing to remove: netriage was not installed here"
else
    echo "done. Open a new shell so it forgets the cached command path"
    echo "      (or run: rehash)"
fi
