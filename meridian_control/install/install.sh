#!/usr/bin/env bash
# Meridian gateway installer.
#
#   curl -fsSL __MERIDIAN_BASE__/install.sh | bash
#   curl -fsSL __MERIDIAN_BASE__/install.sh | bash -s -- --port 8080 --auth --cloud __MERIDIAN_BASE__
#
# Build the flags on __MERIDIAN_BASE__/install. What this script does:
#   1. finds Python 3.11+ (or, if you agree, installs uv to get one)
#   2. creates a private environment in ~/.meridian/venv (no system packages touched)
#   3. downloads the gateway package from __MERIDIAN_BASE__ and checks its SHA-256
#   4. runs `meridian init` with your flags: it asks for anything missing
#      (model server, Meridian Cloud token), writes the config and starts the gateway
#
# Other modes:  --upgrade (new package, keep config)   --uninstall
# Nothing runs as root unless you choose --service system (it then uses sudo).
set -euo pipefail

BASE="__MERIDIAN_BASE__"

main() {
  local dir="${MERIDIAN_HOME:-$HOME/.meridian}" mode="install" python="" args=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) dir="$2"; args+=("$1" "$2"); shift 2 ;;
      --dir=*) dir="${1#*=}"; args+=("$1"); shift ;;
      --python) python="$2"; shift 2 ;;
      --upgrade) mode="upgrade"; shift ;;
      --uninstall) mode="uninstall"; shift ;;
      -h|--help) usage; exit 0 ;;
      *) args+=("$1"); shift ;;
    esac
  done

  case "$(uname -s)" in
    Linux|Darwin) ;;
    *) fail "Meridian installs on Linux and macOS. On Windows, run this inside WSL." ;;
  esac
  need curl

  if [ "$mode" = "uninstall" ]; then uninstall "$dir"; return; fi

  say "Meridian gateway installer"
  say "  from     $BASE"
  say "  into     $dir"

  local venv="$dir/venv"
  mkdir -p "$dir"
  chmod 700 "$dir"
  if [ ! -x "$venv/bin/python" ]; then
    make_venv "$venv" "$python"
  else
    ok "using the existing environment in $venv"
  fi

  install_package "$venv"

  if [ "$mode" = "upgrade" ]; then
    restart_service
    ok "upgraded; your config is unchanged"
    return
  fi

  step "Setting up the gateway"
  if tty_ok; then
    "$venv/bin/meridian" init ${args[@]+"${args[@]}"} < /dev/tty
  else
    say "  (no terminal to ask questions on: using defaults)"
    "$venv/bin/meridian" init --yes ${args[@]+"${args[@]}"}
  fi
}

usage() {
  sed -n '2,16p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//' || true
  say "Flags are passed to 'meridian init'. See them with: ~/.meridian/venv/bin/meridian init --help"
}

# ── output ─────────────────────────────────────────────────────────────────
say()  { printf '%s\n' "$*"; }
step() { printf '→ %s\n' "$*"; }
ok()   { printf '  ✓ %s\n' "$*"; }
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "'$1' is required. Install it and run this again."; }

tty_ok() { [ -t 1 ] && { : < /dev/tty; } 2>/dev/null; }

ask_yes() {  # ask_yes "question" → 0 for yes (default yes)
  tty_ok || return 1
  local reply
  printf '? %s (Y/n): ' "$1" > /dev/tty
  read -r reply < /dev/tty || return 1
  case "$reply" in [nN]*) return 1 ;; *) return 0 ;; esac
}

# ── python ─────────────────────────────────────────────────────────────────
python_ok() {  # python_ok CMD → usable Python 3.11+
  "$1" -c 'import sys, venv; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

make_venv() {
  local venv="$1" python="$2" candidate
  step "Finding Python 3.11 or newer"
  if [ -z "$python" ]; then
    for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
      if command -v "$candidate" >/dev/null 2>&1 && python_ok "$candidate"; then python="$candidate"; break; fi
    done
  fi
  if [ -n "$python" ] && "$python" -m venv "$venv" >/dev/null 2>&1; then
    ok "$("$venv/bin/python" -V) in $venv"
    return
  fi
  rm -rf "$venv"
  # No usable Python (or no venv module, e.g. Debian without python3-venv): uv
  # downloads a standalone Python into your home directory.
  local uv
  uv="$(command -v uv || true)"
  if [ -z "$uv" ] && [ -x "$HOME/.local/bin/uv" ]; then uv="$HOME/.local/bin/uv"; fi
  if [ -z "$uv" ]; then
    say "  No Python 3.11+ with venv found."
    ask_yes "Install uv (astral.sh) to get one? It goes in ~/.local/bin" \
      || fail "Install Python 3.11+ (with venv) and run this again, or pass --python /path/to/python3."
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
    uv="$HOME/.local/bin/uv"
    [ -x "$uv" ] || fail "uv didn't install. Install Python 3.11+ and run this again."
  fi
  "$uv" venv --quiet --seed --python 3.14 "$venv" || fail "couldn't create a Python environment in $venv"
  ok "$("$venv/bin/python" -V) in $venv (via uv)"
}

# ── package ────────────────────────────────────────────────────────────────
sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

install_package() {
  local venv="$1" tmp line want name got
  step "Downloading the Meridian gateway"
  tmp="$(mktemp -d)"
  MERIDIAN_TMP="$tmp"
  trap 'rm -rf "${MERIDIAN_TMP:-}"' EXIT
  line="$(curl -fsSL "$BASE/download/meridian.whl.sha256")" \
    || fail "couldn't reach $BASE/download. Is Meridian Cloud running?"
  want="${line%% *}"
  name="${line##* }"
  case "$name" in
    meridian-*-py3-none-any.whl) ;;
    *) fail "unexpected package name from the server: $name" ;;
  esac
  curl -fsSL -o "$tmp/$name" "$BASE/download/$name" || fail "download failed: $BASE/download/$name"
  got="$(sha256 "$tmp/$name")"
  [ "$got" = "$want" ] || fail "checksum mismatch for $name (expected $want, got $got). Nothing was installed."
  ok "$name (SHA-256 verified)"
  step "Installing into $venv"
  "$venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade "$tmp/$name" \
    || fail "pip couldn't install $name. Check your network access to PyPI."
  ok "meridian $("$venv/bin/python" -c 'import meridian; print(meridian.__version__)')"
}

# ── service ────────────────────────────────────────────────────────────────
restart_service() {
  if [ "$(uname -s)" = "Darwin" ]; then
    launchctl kickstart -k "gui/$(id -u)/dev.meridian.gateway" 2>/dev/null && ok "restarted the gateway" || true
  elif systemctl --user cat meridian-gateway >/dev/null 2>&1; then
    systemctl --user restart meridian-gateway && ok "restarted the gateway"
  elif systemctl cat meridian-gateway >/dev/null 2>&1; then
    sudo systemctl restart meridian-gateway && ok "restarted the gateway"
  else
    say "  Restart the gateway yourself to use the new version."
  fi
}

uninstall() {
  local dir="$1"
  say "This removes the Meridian gateway service and $dir (config, keys, logs)."
  ask_yes "Uninstall?" || fail "Cancelled."
  if [ "$(uname -s)" = "Darwin" ]; then
    local plist="$HOME/Library/LaunchAgents/dev.meridian.gateway.plist"
    launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
    rm -f "$plist"
  else
    systemctl --user disable --now meridian-gateway 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/meridian-gateway.service"
    if [ -f /etc/systemd/system/meridian-gateway.service ]; then
      sudo systemctl disable --now meridian-gateway || true
      sudo rm -f /etc/systemd/system/meridian-gateway.service
    fi
  fi
  rm -rf "$dir"
  ok "uninstalled"
}

# Everything above only defines functions: a download cut short runs nothing.
main "$@"
