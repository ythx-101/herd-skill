#!/usr/bin/env bash
# Popup pane for the herd board: shows the latest posts and refreshes every 5 seconds.
# Settings come from $HERDR_PLUGIN_CONFIG_DIR/config (KEY=VALUE lines):
#   HERD_PROJECT=<project path or bare name>      required
#   HERD_BOARD_HOST=<ssh host>                    optional, empty = this machine
#   HERD_REMOTE_BIN=<dir with herd-read there>    optional, default ~/herd/bin
# Keys: q quits, any other key refreshes.
root=$(cd "$(dirname "$0")/.." && pwd)
cfg="${HERDR_PLUGIN_CONFIG_DIR:-$HOME/.config/herd-board}/config"
if [ -f "$cfg" ]; then
  while IFS='=' read -r k v; do
    case $k in HERD_PROJECT|HERD_BOARD_HOST|HERD_REMOTE_BIN|HERD_ROOT) export "$k=$v" ;; esac
  done <"$cfg"
fi
if [ -z "${HERD_PROJECT:-}" ]; then
  echo "herd board: set HERD_PROJECT=<project> in $cfg"; read -rsn1 _; exit 1
fi
host=${HERD_BOARD_HOST:-}; [ "$host" = local ] && host=
show() {
  if [ -z "$host" ]; then "$root/skills/herd/bin/herd-read" "$HERD_PROJECT"
  else ssh -o BatchMode=yes "$host" "${HERD_REMOTE_BIN:-herd/bin}/herd-read $(printf %q "$HERD_PROJECT")"; fi
}
while :; do
  out=$(show 2>&1 | tail -n $(( $(tput lines 2>/dev/null || echo 24) - 2 )))
  clear; printf 'herd board  %s%s   (q quits)\n\n%s\n' "${host:+$host:}" "$HERD_PROJECT" "$out"
  read -rsn1 -t 5 key && [ "$key" = q ] && exit 0
done
