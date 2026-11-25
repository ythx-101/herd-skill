#!/usr/bin/env bash
# Self-test for the herd skill. Needs bash, python3, flock. No herdr, no network:
# a stub stands in for herdr and everything runs in a temporary directory.
set -uo pipefail
bin=$(cd "$(dirname "$0")/../skills/herd/bin" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
pass=0; fail=0
ok() { pass=$((pass+1)); echo "PASS $1"; }
no() { fail=$((fail+1)); echo "FAIL $1"; }
check() { local name=$1; shift; if "$@" >/dev/null 2>&1; then ok "$name"; else no "$name"; fi; }

# --- syntax
for f in herd-post herd-read herd-watch herd-preflight; do check "syntax $f" bash -n "$bin/$f"; done
check "syntax herd-watchd.py" python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$bin/herd-watchd.py"
check "herd-watchd.py --help" python3 "$bin/herd-watchd.py" --help

# --- board
export HERD_ROOT="$T/herd"; unset HERD_BOARD_HOST HERD_REMOTE_BIN HERD_LANG HERD_DENY_PANES
P="$T/herd/demo"
for i in 1 2 3 4 5; do "$bin/herd-post" "$P" "w$i" "post $i" >/dev/null & done; wait
seqs=$("$bin/herd-read" "$P" --json | python3 -c "import sys,json; print(sorted(json.loads(l)['seq'] for l in sys.stdin))")
[ "$seqs" = "[1, 2, 3, 4, 5]" ] && ok "5 concurrent posts get seq 1..5" || no "concurrent posts: $seqs"
n=$("$bin/herd-post" demo coordinator '@builder start, see BRIEF section 2')
[ "$n" = 6 ] && ok "bare project name resolves under HERD_ROOT (seq 6)" || no "bare name seq=$n"
[ "$("$bin/herd-read" demo --since 5 | wc -l)" = 1 ] && ok "herd-read --since" || no "herd-read --since"
printf 'quote " dollar $HOME\nsecond line' | "$bin/herd-post" demo builder - >/dev/null
got=$("$bin/herd-read" demo --since 6 --json | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['text'])")
[ "$got" = "$(printf 'quote " dollar $HOME\nsecond line')" ] && ok "stdin post keeps quotes, \$ and newlines" || no "stdin post: $got"
"$bin/herd-read" "$T/none" >/dev/null && ok "herd-read on a missing board exits 0" || no "herd-read missing board"

# --- watcher with a stub herdr
stub="$T/herdr"; calls="$T/calls.log"
cat >"$stub" <<STUB
#!/usr/bin/env bash
echo "\$*" >>"$calls"
[ "\$1" = --machine ] && shift 2
case "\$1 \$2" in
  "agent get") if [ -f "$T/blocked-\$3" ]; then st=blocked; else st=idle; fi
               [ "\$3" = ghost ] && exit 1
               echo "{\"result\":{\"agent\":{\"agent_status\":\"\$st\"}}}" ;;
  "agent prompt") echo '{"result":{}}' ;;
  "agent read") [ "\$3" = burner ] && echo "Error: RESOURCE_EXHAUSTED quota reached"; exit 0 ;;
esac
STUB
chmod +x "$stub"; export HERD_HERDR_BIN="$stub"
W="$T/herd/watch"; mkdir -p "$W"
past=$(date -u -d '-1 minute' +%FT%TZ 2>/dev/null || date -u -v-1M +%FT%TZ)
cat >"$W/roster.json" <<JSON
{"project":"watch","coordinator":{"machine":"local","pane":"w1:p1"},"deadline_utc":"$past","stall_minutes":15,
 "members":{"builder":{"machine":"local","agent":"builder"},"checker":{"machine":"box-a","agent":"checker"},
            "burner":{"machine":"local","agent":"burner"},"fix-1":{"machine":"local","agent":"fix-1"},"fix-2":{"machine":"local","agent":"fix-2"}}}
JSON
wd() { python3 "$bin/herd-watchd.py" --once "$@" watch; }
"$bin/herd-post" watch coordinator '@builder go' >/dev/null                 # 1
"$bin/herd-post" watch builder 'mail me at a@builder.example, @nobody' >/dev/null   # 2: no real mention
"$bin/herd-post" watch builder '@coordinator [done] report.md' >/dev/null    # 3
"$bin/herd-post" watch coordinator '@fix check both' >/dev/null              # 4: group mention
touch "$T/blocked-checker"
"$bin/herd-post" watch coordinator '@checker verify' >/dev/null              # 5: checker is blocked
wd
log="$W/watch.log"
grep -q 'WAKE builder for #1' "$log" && ok "@mention wakes the member" || no "wake builder"
grep -q 'agent prompt builder \[board\] #1 from coordinator: @builder go' "$calls" && ok "prompt text names seq and author" || no "prompt text"
! grep -q 'for #2' "$log" && ok "non-member and mid-word @ are ignored" || no "false mention"
grep -q 'WAKE coordinator for #3' "$log" && ok "@coordinator wakes the coordinator pane" || no "wake coordinator"
grep -q 'WAKE fix-1 for #4' "$log" && grep -q 'WAKE fix-2 for #4' "$log" && ok "@fix wakes fix-1 and fix-2" || no "group mention"
grep -q 'SKIP checker blocked, #5 pending' "$log" && ! grep -q 'agent prompt checker' "$calls" && ok "blocked member is not prompted" || no "blocked skip"
grep -q -- '--machine box-a agent get checker' "$calls" && ok "remote member goes through herdr --machine" || no "--machine"
grep -q 'ALERT coordinator deadline' "$log" && ok "deadline alert" || no "deadline alert"
grep -q 'ALERT coordinator quota:burner' "$log" && ok "quota phrase in a pane alerts the coordinator" || no "quota alert"
rm "$T/blocked-checker"; wd
grep -q 'WAKE checker for #5' "$log" && ok "blocked mention is delivered after unblock" || no "deliver after unblock"
[ "$(grep -c 'WAKE builder for #1' "$log")" = 1 ] && [ "$(grep -c 'ALERT coordinator deadline' "$log")" = 1 ] && ok "no duplicate wake or alert on the next poll" || no "duplicates"
# stall: pretend the last post is 20 minutes old
python3 - "$W/watch.state.json" <<'PY'
import json,sys,datetime
p=sys.argv[1]; s=json.load(open(p))
s['last_post_time']=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=20)).strftime('%Y-%m-%dT%H:%M:%SZ')
json.dump(s,open(p,'w'))
PY
wd
grep -q 'ALERT coordinator stall:5' "$log" && ok "stall alert names members without [done]" || no "stall alert"
grep -q 'still without \[done\]: checker, burner, fix-1, fix-2' "$calls" && ok "stall text lists exactly the unfinished members" || no "stall text"
# deny list + dry run + zh
D="$T/herd/dry"; mkdir -p "$D"; cp "$W/roster.json" "$D/"; : >"$calls"
"$bin/herd-post" dry coordinator '@builder @burner go' >/dev/null
out=$(HERD_DENY_PANES=burner HERD_LANG=zh python3 "$bin/herd-watchd.py" --once --dry-run dry)
grep -q 'DRY would prompt local:builder: \[board\] #1 来自 coordinator' <<<"$out" && ok "--dry-run logs the prompt (zh text)" || no "dry-run"
grep -q 'DENY refuse prompt to burner' <<<"$out" && ok "HERD_DENY_PANES refuses the pane" || no "deny"
[ ! -s "$calls" ] && ok "--dry-run never calls herdr" || no "dry-run called herdr"
# herd-watch start/status/stop
export HERD_RUN_DIR="$T/run" HERD_INTERVAL=1
"$bin/herd-watch" start watch | grep -q '^started pid' && ok "herd-watch start" || no "herd-watch start"
"$bin/herd-watch" start watch | grep -q '^already running' && ok "herd-watch start is idempotent" || no "start twice"
o=$("$bin/herd-watch" status watch); grep -q '^running pid' <<<"$o" && ok "herd-watch status" || no "herd-watch status"
"$bin/herd-watch" stop watch | grep -q '^stopped' && tail -1 "$log" | grep -q ' STOP$' && ok "herd-watch stop" || no "herd-watch stop"
o=$("$bin/herd-watch" start "$T/herd/none" 2>&1); grep -q 'no roster.json' <<<"$o" && ok "herd-watch refuses a project without roster.json" || no "missing roster"

# --- preflight with a fake model CLI
fake="$T/fakecli"
cat >"$fake" <<'F'
#!/usr/bin/env bash
case $1 in good) echo pong ;; spent) echo "RESOURCE_EXHAUSTED: quota reached"; exit 1 ;; busy) echo "503 No capacity"; exit 1 ;; slow) sleep 5 ;; *) echo "unknown model"; exit 3 ;; esac
F
chmod +x "$fake"
out=$(HERD_PING_CMD="$fake {model}" HERD_PING_TIMEOUT=1 "$bin/herd-preflight" local good spent busy slow other); rc=$?
want=$'good ok\nspent quota\nbusy no-capacity\nslow timeout'
[ "$(awk '{print $1, $2}' <<<"$out" | head -4)" = "$want" ] && grep -q '^other .*error(rc=3)' <<<"$out" && [ $rc = 1 ] \
  && ok "herd-preflight: ok / quota / no-capacity / timeout / error" || no "preflight: $out"
HERD_PING_CMD="$fake {model}" "$bin/herd-preflight" local good >/dev/null && ok "herd-preflight exits 0 when all ok" || no "preflight exit"

echo; echo "passed=$pass failed=$fail"
[ $fail -eq 0 ] && echo SELFTEST_OK
exit $((fail>0))
