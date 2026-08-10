#!/usr/bin/env bash
# sqreader agent installer.
#
#   curl -fsSL https://<central>/setup/install.sh | sudo bash -s -- <TOKEN>
#
# THIS FILE IS THE CANONICAL COPY. Central serves it verbatim from its own
# web/install.sh; keep the two byte-identical (`diff` them) so what an operator
# pipes into root's shell is what is reviewed here.
#
# Installs the compiled agent on a Squad game server, registers it against the
# central that issued the token, and starts it under systemd.
#
# Re-running is safe: recordings, stats and an existing enrolment are left
# alone, so this doubles as the manual upgrade path.
#
# SEVERAL SQUAD SERVERS ON ONE MACHINE: run this once per server, giving each
# its own instance name, HTTP port and game port:
#
#   SQREADER_INSTANCE=eu2 SQREADER_PORT=8082 SQREADER_SQUAD_PORT=7797 \
#     bash install.sh <TOKEN>
#
# Everything an instance owns is then named after it — install dir, systemd
# unit, retention timer — so a second install adds a server instead of
# replacing the first one.
set -euo pipefail

TOKEN="${1:-}"
CENTRAL="${SQREADER_CENTRAL:-https://squadreader.com}"
INSTANCE="${SQREADER_INSTANCE:-prod}"
UNIT="sqreader-$INSTANCE"
SQUAD_PORT="${SQREADER_SQUAD_PORT:-}"
SERVER_ID="${SQREADER_SERVER_ID:-}"

# The default instance keeps the paths and unit names it has always had, so an
# existing box upgrades in place instead of acquiring a second copy of itself.
if [ "$INSTANCE" = "prod" ]; then
    HOME_DIR="${SQREADER_HOME:-/opt/sqreader}"
    RETENTION="sqreader-retention"
    BIN_LINK="/usr/local/bin/sqreader"
    PORT="${SQREADER_PORT:-8081}"
else
    HOME_DIR="${SQREADER_HOME:-/opt/sqreader-$INSTANCE}"
    RETENTION="sqreader-$INSTANCE-retention"
    BIN_LINK="/usr/local/bin/sqreader-$INSTANCE"
    PORT="${SQREADER_PORT:-}"
fi

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m warn\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mfail\033[0m %s\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------- preconditions

[ "$(uname -s)" = "Linux" ] || die "this agent is Linux-only"
[ "$(id -u)" = "0" ] || die "run as root (needed to read game memory and write units)"
# A token is required to ENROL, not to upgrade: an already-enrolled box
# re-runs this script with no arguments to pick up a new release, and
# authenticates the download with its agent credentials instead (see dl()).
# Demanding a token here would have made upgrades impossible — the enrolment
# token is single-use and expires after a week.
if [ -z "$TOKEN" ] && [ ! -f "$HOME_DIR/.env.agent" ]; then
    die "usage: install.sh <ENROLMENT-TOKEN>
  (an already-enrolled box may re-run with no token to upgrade)"
fi
command -v systemctl >/dev/null || die "systemd is required"
command -v curl >/dev/null || die "curl is required"
command -v sha256sum >/dev/null || die "sha256sum is required"

case "$INSTANCE" in
    *[!A-Za-z0-9_-]*|"") die "SQREADER_INSTANCE must be letters, digits, - or _" ;;
esac

# Every running Squad server, one "<pid> <game-port>" per line.
#
# Identified by the exe link, not the process name: the kernel truncates a
# process name to 15 bytes, so LinuxGSM's `SquadGameServer.sh` launcher is
# indistinguishable from the binary by name alone and carries the same -Port=
# on its command line. The port is matched with its leading space so that a
# server's -QueryPort= cannot be read as another's game port.
squad_instances() {
    local d exe args port
    for d in /proc/[0-9]*; do
        exe="$(readlink "$d/exe" 2>/dev/null)" || continue
        [ "${exe##*/}" = "SquadGameServer" ] || continue
        args=" $(tr '\0' ' ' < "$d/cmdline" 2>/dev/null)"
        port="$(printf '%s' "$args" | grep -oE ' -Port=[0-9]+' | head -1 | cut -d= -f2)"
        printf '%s %s\n' "${d##*/}" "${port:-?}"
    done
}

# Guessing either port silently breaks something. Two agents on one HTTP port
# just fails to boot, which is at least loud — but an agent with no game port
# falls back to finding a server BY NAME, and on a box with several that
# answers arbitrarily and re-answers on every restart. Two agents can then read
# the same game and publish it twice under two names, while a third server
# nobody is reading looks simply idle. None of that announces itself, so the
# installer refuses to guess and shows what it found instead.
INSTANCES="$(squad_instances || true)"
INSTANCE_COUNT="$(printf '%s' "$INSTANCES" | grep -c . || true)"

suggest_instances() {
    printf '\n  Squad servers running on this machine:\n\n'
    printf '%s\n' "$INSTANCES" | while read -r p gp; do
        [ -n "$p" ] || continue
        printf '    pid %-8s -Port=%s\n' "$p" "$gp"
    done
    printf '\n  Install one agent per server, each with its own name, HTTP port\n'
    printf '  and game port — and its own enrolment token:\n\n'
    printf '%s\n' "$INSTANCES" | awk -v c="$CENTRAL" '
        NF { n++; printf "    SQREADER_INSTANCE=srv%d SQREADER_PORT=%d \\\n", n, 8080 + n
             printf "      SQREADER_SQUAD_PORT=%s bash install.sh <TOKEN-%d>\n\n", $2, n }'
}

if [ -z "$SQUAD_PORT" ] && [ "$INSTANCE_COUNT" -gt 1 ]; then
    die "$INSTANCE_COUNT Squad servers are running — SQREADER_SQUAD_PORT says which one
  this agent reads. Without it the server is picked by name, which answers
  arbitrarily and differently after every restart.
$(suggest_instances)"
fi

if [ "$INSTANCE" != "prod" ]; then
    [ -n "$PORT" ] || die "SQREADER_PORT is required for instance '$INSTANCE'
  (8081 belongs to the first install; give this one its own, e.g. 8082)"
    [ -n "$SQUAD_PORT" ] || die "SQREADER_SQUAD_PORT is required for instance '$INSTANCE'
  (the -Port= of the Squad server THIS agent should read, e.g. 7797 —
   without it both agents may read the same game)"
fi

if [ -n "$SQUAD_PORT" ] \
        && [ "$INSTANCE_COUNT" -gt 0 ] \
        && ! printf '%s\n' "$INSTANCES" | grep -qE " $SQUAD_PORT\$"; then
    warn "no Squad server is currently listening on -Port=$SQUAD_PORT."
    warn "Installing anyway — but if that is a typo the agent will never start."
    printf '%s' "$(suggest_instances)" >&2
fi

# Warn, don't abort: an operator may well install before the game server's
# first boot. The unit resolves the pid at start time anyway.
if [ -n "$SQUAD_PORT" ]; then
    say "this instance reads the Squad server on -Port=$SQUAD_PORT"
elif [ "$INSTANCE_COUNT" -gt 0 ]; then
    say "found a running Squad server (pid $(printf '%s' "$INSTANCES" | head -1 | cut -d' ' -f1))"
else
    warn "no running SquadGameServer found — installing anyway; the service"
    warn "resolves the pid when it starts, so start Squad first, then this."
fi

say "installing into $HOME_DIR (central: $CENTRAL)"
mkdir -p "$HOME_DIR"/{recordings,stats}

# --------------------------------------------------------------- fetch + verify

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Download with whatever auth this run can present. central's download gate
# accepts a non-consuming Bearer enrol token (fresh install) OR an
# agent-signed X-Sqr-Auth (upgrade re-run on an already-enrolled box). On an
# ungated central both are simply ignored. Header, never query string, so the
# credential is not written to access logs or cached URLs.
dl() {
    local file="$1" args=()
    if [ -n "$TOKEN" ]; then
        args=(-H "Authorization: Bearer $TOKEN")
        # An operator upgrading months later re-runs this with the token still
        # sitting in their Discord DM — by then it is expired, and without this
        # retry the agent credentials that WOULD have worked are never tried.
        if ! curl -fsSL ${args[@]+"${args[@]}"} \
                "$CENTRAL/agent/download?file=$file" \
                -o "$TMP/$file" 2>/dev/null; then
            if [ -x "$HOME_DIR/sqreader" ] && [ -f "$HOME_DIR/.env.agent" ]; then
                local hdr2
                if hdr2="$(cd "$HOME_DIR" && ./sqreader download-auth "$file" 2>/dev/null)" \
                        && [ -n "$hdr2" ]; then
                    curl -fsSL -H "X-Sqr-Auth: $hdr2" \
                         "$CENTRAL/agent/download?file=$file" -o "$TMP/$file"
                    return
                fi
            fi
            return 1
        fi
        return 0
    elif [ -x "$HOME_DIR/sqreader" ] && [ -f "$HOME_DIR/.env.agent" ]; then
        # Run it from $HOME_DIR: the agent resolves .env.agent relative to the
        # CURRENT DIRECTORY, and the operator invokes this script from wherever
        # they happen to be. Without the cd, download-auth reports "not
        # enrolled", the header is never sent, and every upgrade 404s.
        # Old binaries predate `download-auth`; tolerate its absence and fall
        # through to a bare GET (fine unless the gate is enforcing).
        local hdr
        if hdr="$(cd "$HOME_DIR" && ./sqreader download-auth "$file" 2>/dev/null)" \
                && [ -n "$hdr" ]; then
            args=(-H "X-Sqr-Auth: $hdr")
        fi
    fi
    # ${args[@]+...} guard: on bash < 4.4 an empty array expands to an unbound
    # variable under `set -u` and aborts the install. That is the no-credential
    # path, i.e. exactly the case we want to reach the server and fail cleanly.
    curl -fsSL ${args[@]+"${args[@]}"} "$CENTRAL/agent/download?file=$file" \
         -o "$TMP/$file"
}

say "fetching release manifest"
dl SHA256SUMS || die "cannot fetch the release from $CENTRAL.
  fresh install : pass your enrolment token   ->  install.sh <TOKEN>
  upgrade       : this box's binary may be too old to authenticate the
                  download; re-run with a fresh token from your central admin."

# SHA256SUMS names the artifacts *and* pins them, so it is the only thing the
# installer needs to know up front — no version baked into this script.
# The assets tarball is ALSO called sqreader-assets-*.tar.gz, so a bare
# ^sqreader- match picks whichever line SHA256SUMS happens to list first and
# can install a tarball as the agent binary. Exclude the archive suffix.
BIN_NAME="$(awk '$2 ~ /^sqreader-/ && $2 !~ /\.(tar\.gz|tgz|zip|asc|sig)$/ {print $2; exit}' "$TMP/SHA256SUMS")"
[ -n "$BIN_NAME" ] || die "no agent binary listed in SHA256SUMS"
ASSETS_NAME="$(awk '$2 ~ /^sqreader-assets.*\.tar\.gz$/ {print $2; exit}' "$TMP/SHA256SUMS" || true)"

say "downloading $BIN_NAME"
dl "$BIN_NAME" || die "download failed: $BIN_NAME"
[ -n "$ASSETS_NAME" ] && { say "downloading $ASSETS_NAME"; dl "$ASSETS_NAME" || warn "asset download failed — map textures and the viewer will be missing"; }

say "verifying checksums"
( cd "$TMP" && grep -E " ($BIN_NAME${ASSETS_NAME:+|$ASSETS_NAME})\$" SHA256SUMS | sha256sum -c --quiet - ) \
    || die "checksum mismatch — refusing to install"

# ------------------------------------------------------------------- install

install -m 0755 "$TMP/$BIN_NAME" "$HOME_DIR/sqreader"
# Put it on PATH. Every instruction we give an operator ("sqreader enroll
# --status", "sqreader doctor") is written as a bare command, and without this
# all of them fail with "command not found" on a box where the binary lives in
# /opt. Symlink so an upgrade that replaces the target needs no relinking.
ln -sfn "$HOME_DIR/sqreader" "$BIN_LINK" 2>/dev/null \
    || warn "could not link $BIN_LINK — use $HOME_DIR/sqreader"

# Prove the artifact actually runs HERE before touching units or spending the
# token: wrong glibc, a broken crypto extension or missing embedded data all
# surface now rather than midway through onboarding.
say "checking the binary runs on this box"
"$HOME_DIR/sqreader" version || die "the binary does not run on this system"
"$HOME_DIR/sqreader" selftest || die "selftest failed — not installing a broken agent"

if [ -n "$ASSETS_NAME" ] && [ -f "$TMP/$ASSETS_NAME" ]; then
    say "unpacking assets (viewer, icons, map textures)"
    tar xzf "$TMP/$ASSETS_NAME" -C "$HOME_DIR"
fi

# NOTE: kernel.yama.ptrace_scope is deliberately NOT touched. The service runs
# as root, which reads /proc/<pid>/mem regardless of the yama setting — this is
# verified on a production box running ptrace_scope=1. Lowering it globally
# would weaken every process on the operator's machine for no benefit.

# -------------------------------------------------------------------- config

# Only written when absent: on an upgrade the operator's own edits live here,
# and re-running the installer must not quietly revert them. The agent reads it
# from the working directory, which the unit below pins to $HOME_DIR.
CONFIG="$HOME_DIR/sqreader.config.json"
if [ -f "$CONFIG" ]; then
    if [ -n "$SQUAD_PORT" ] || [ -n "$SERVER_ID" ]; then
        say "keeping the existing $CONFIG (edit it by hand to change ports/ids)"
    fi
elif [ -n "$SQUAD_PORT" ] || [ -n "$SERVER_ID" ]; then
    say "writing $CONFIG"
    {
        printf '{\n'
        [ -n "$SQUAD_PORT" ] && printf '  "squad_port": %s,\n' "$SQUAD_PORT"
        printf '  "server_id": "%s"\n' "${SERVER_ID:-$INSTANCE}"
        printf '}\n'
    } > "$CONFIG"
fi

# ------------------------------------------------------------------- systemd

say "writing systemd units"
cat > "/etc/systemd/system/$UNIT.service" <<UNIT
[Unit]
Description=sqreader agent $INSTANCE (Squad live reader + match recorder)
After=network.target
# Never give up restarting: a fault storm must not leave the unit dead.
# Backoff is handled by RestartSteps below, not by surrendering.
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory=$HOME_DIR
# Single-tier recording on purpose. Two-tier (--record-hz) gives smoother
# replays but spawns a helper subprocess, and that path is only proven from a
# source checkout; on a compiled build a spawn failure is the difference
# between "records at 2 Hz" and "crash-loops on a customer's game server".
# Revisit once a compiled build has actually been observed running two-tier.
#
# --pid is deliberately omitted: the agent resolves the Squad server itself
# (pidof, then a pgrep that prefers the instance carrying Port= on
# multi-instance boxes, then a /proc scan). Passing \$(pidof ...) from the unit
# resolves to an EMPTY string when Squad is not running yet, which argparse
# then reports as "argument --pid: expected one argument" — a baffling message
# for what is really "start the game server first". Letting the agent resolve
# gives "no SquadGameServer process running" instead, and it re-resolves on
# every systemd restart either way.
# Installs a release the running agent staged and then exited for. It runs
# BETWEEN the two processes — the only moment the binary is not executing, so
# the swap cannot hit ETXTBSY. Leading "-" because a failed or absent update
# must never stop the agent from starting: no marker is a silent no-op.
ExecStartPre=-$HOME_DIR/sqreader apply-staged-update --state-dir $HOME_DIR/stats
ExecStart=/bin/bash -c 'exec $HOME_DIR/sqreader serve --push \\
    --host 127.0.0.1 --port $PORT --hz 2 \\
    --recordings-dir $HOME_DIR/recordings \\
    --stats-db $HOME_DIR/stats/player_stats.db \\
    --icons-dir $HOME_DIR/icons \\
    --sqmaps-dir $HOME_DIR/sqmaps \\
    --frontend-dir $HOME_DIR/frontend/dist'
Restart=always
# RestartSteps/RestartMaxDelaySec need systemd >= 254; Ubuntu 22.04 (249) and
# Debian 12 (252) ignore them silently, leaving only RestartSec. So RestartSec
# is set to a value that is tolerable as a FLAT delay on those releases — at 2 s
# a persistent failure is a fork/exec storm on a machine also running the game.
RestartSec=5
RestartSteps=5
RestartMaxDelaySec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

# The agent shares a box with the game server, and the game server always wins.
mkdir -p "/etc/systemd/system/$UNIT.service.d"
cat > "/etc/systemd/system/$UNIT.service.d/safety.conf" <<'DROPIN'
[Service]
CPUQuota=50%
Nice=19
IOSchedulingClass=idle
DROPIN

# Recordings grow by hundreds of MB a day and nothing else removes them. On a
# box that also runs Squad, a full disk takes the GAME down too — this timer is
# the safety valve, not an optional extra.
cat > "/etc/systemd/system/$RETENTION.service" <<UNIT
[Unit]
Description=Prune old sqreader match recordings ($INSTANCE)
After=local-fs.target

[Service]
Type=oneshot
ExecStart=$HOME_DIR/sqreader retention --recordings-dir $HOME_DIR/recordings \\
    --max-age-days 90 --max-total-gb 150 --min-free-gb 50
StandardOutput=journal
StandardError=journal
Nice=10
IOSchedulingClass=idle
UNIT

cat > "/etc/systemd/system/$RETENTION.timer" <<UNIT
[Unit]
Description=Daily sqreader recording retention ($INSTANCE)

[Timer]
OnCalendar=daily
Persistent=true
# Staggered per instance: several agents pruning tens of GB at the same minute
# would hit one disk together, on a machine whose day job is serving a game.
RandomizedDelaySec=30m

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload

# -------------------------------------------------------------------- enrol

if [ -f "$HOME_DIR/.env.agent" ]; then
    say "already enrolled — keeping existing credentials"
else
    say "enrolling against $CENTRAL"
    ( cd "$HOME_DIR" && ./sqreader enroll "$TOKEN" --central-url "$CENTRAL" ) \
        || die "enrolment failed (token already used, or expired?)"
    chmod 600 "$HOME_DIR/.env.agent" 2>/dev/null || true
fi

# --------------------------------------------------------------------- start

say "starting services"
systemctl enable --now "$RETENTION.timer" >/dev/null 2>&1 || \
    warn "could not enable the retention timer — check disk usage yourself"
systemctl enable "$UNIT" >/dev/null 2>&1 || true
# RESTART, not just `enable --now`. On an upgrade the unit is already active,
# `--now` is a no-op, and `install` replaced the binary at a NEW inode — so the
# old process keeps running off the unlinked one. The installer would then
# report success and print the new version while the box quietly kept running
# the old agent forever. Restart is also correct for a fresh install.
systemctl restart "$UNIT" >/dev/null 2>&1 || true
sleep 3

if systemctl is-active --quiet "$UNIT"; then
    say "sqreader is running"
else
    warn "service did not come up — inspect with: journalctl -u $UNIT -n 50"
fi

cat <<EOF

  sqreader installed in $HOME_DIR
  instance  : $INSTANCE
  version   : $("$HOME_DIR/sqreader" version | head -1)
  local API : http://127.0.0.1:$PORT
  logs      : journalctl -u $UNIT -f
  status    : $HOME_DIR/sqreader enroll --status

  Finished matches are published to $CENTRAL.
  Live match data never leaves this machine.

  Another Squad server on this machine? Install it alongside this one:
    SQREADER_INSTANCE=<name> SQREADER_PORT=<free port> \\
      SQREADER_SQUAD_PORT=<that server's -Port=> bash install.sh <TOKEN>

EOF
