#!/usr/bin/env python3
"""herd-watchd: poll a herd board, wake @mentioned agents through herdr, alert the coordinator.

It only reads the board, sends one-line prompts and writes its own log and
cursor. It never decides that work is finished.

Environment:
  HERD_BOARD_HOST   ssh host that stores the board; empty or "local" = this machine
  HERD_ROOT         where bare project names live on the board host (default ~/herd)
  HERD_REMOTE_BIN   directory holding herd-read on the board host
                    (default: this script's directory when local, $HERD_ROOT/bin when remote)
  HERD_INTERVAL     seconds between polls (default 20)
  HERD_DENY_PANES   comma-separated pane ids / agent names that must never be prompted
  HERD_QUOTA_RE     regex that marks "quota exhausted" in a member's pane
  HERD_LANG         language of the one-line prompts: en (default) or zh
  HERD_HERDR_BIN    herdr binary (default: $HERDR_BIN_PATH, else "herdr")
"""
import argparse, datetime, json, os, re, shlex, subprocess, sys, time

HOST = os.environ.get('HERD_BOARD_HOST', '').strip()
LOCAL = HOST in ('', 'local')
ROOT = os.environ.get('HERD_ROOT', '~/herd')
INTERVAL = int(os.environ.get('HERD_INTERVAL', '20'))
DENY = set(filter(None, (s.strip() for s in os.environ.get('HERD_DENY_PANES', '').split(','))))
# Exact phrases only. A bare "429" also shows up in ordinary data, and
# "503 no capacity" is not a quota problem.
QUOTA_RE = re.compile(os.environ.get('HERD_QUOTA_RE', r'(quota reached|RESOURCE_EXHAUSTED|exhausted your quota)'), re.I)
HERDR = os.environ.get('HERD_HERDR_BIN') or os.environ.get('HERDR_BIN_PATH') or 'herdr'
LANG = os.environ.get('HERD_LANG', 'en').lower()[:2]
MSG = {
    'en': {
        'wake': "[board] #{seq} from {agent}: {text}. Read the board with herd-read.",
        'deadline': "[herd-watch] Deadline {dl} reached. Accept what exists now (herd-read shows the board).",
        'stall': "[herd-watch] No new post for {sm} minutes; still without [done]: {names}. Please check.",
        'quota': "[herd-watch] Member {name}'s pane shows a quota message: {line}. Switch to the fallback model.",
    },
    'zh': {
        'wake': "[board] #{seq} 来自 {agent}：{text}。请用 herd-read 读公告板",
        'deadline': "[herd-watch] 已到截止时间 {dl}。请按现有产物验收收卷（herd-read 看公告板）。",
        'stall': "[herd-watch] 公告板已 {sm} 分钟无新帖，未交 [done]：{names}。请检查。",
        'quota': "[herd-watch] 成员 {name} 窗格出现额度耗尽字样：{line}。请换备用模型。",
    },
}.get(LANG) or {}
DRY = False


def board_path(p):
    """Resolve a project argument to a path usable on the board host.

    Local: an absolute path. Remote: absolute, or relative to the remote home
    (ssh commands start there; a quoted ~ would not expand)."""
    if not (p.startswith('/') or p.startswith('~') or p.startswith('./') or p.startswith('../')):
        p = ROOT.rstrip('/') + '/' + p
    if LOCAL:
        return os.path.abspath(os.path.expanduser(p))
    if p == '~':
        return '.'
    return './' + p[2:] if p.startswith('~/') else p


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def run(argv, inp=None, timeout=60, cwd=None):
    try:
        r = subprocess.run(argv, input=inp, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, '', 'timeout'
    except FileNotFoundError as e:
        return 127, '', str(e)


def board(cmd, inp=None, timeout=60):
    """Run a shell command on the board host."""
    if LOCAL:
        return run(['bash', '-c', cmd], inp, timeout, cwd=os.path.expanduser('~'))
    return run(['ssh', '-o', 'BatchMode=yes', HOST, cmd], inp, timeout)


def log(msg):
    line = f"{now():%Y-%m-%dT%H:%M:%SZ} {msg}\n"
    board(f"cat >> {P}/watch.log", inp=line)
    if DRY:
        sys.stdout.write(line)


def herdr(machine, *args):
    pre = [HERDR] + ([] if machine in (None, '', 'local') else ['--machine', machine])
    return run(pre + list(args), timeout=90)


def agent_status(machine, target):
    rc, out, _ = herdr(machine, 'agent', 'get', target)
    if rc != 0:
        return 'missing'
    try:
        j = json.loads(out)['result']
        return j.get('agent', j).get('agent_status', 'unknown')
    except Exception:
        return 'unknown'


def prompt(machine, target, text):
    if not target:
        return 'missing'
    if target in DENY:
        log(f"DENY refuse prompt to {target}")
        return False
    if DRY:
        log(f"DRY would prompt {machine or 'local'}:{target}: {text}")
        return True
    st = agent_status(machine, target)
    if st in ('blocked', 'missing'):
        return st
    rc, out, err = herdr(machine, 'agent', 'prompt', target, text)
    return True if rc == 0 else f"err:{(err or out)[:150]}"


def load_json(name, default):
    rc, out, _ = board(f"cat {P}/{name} 2>/dev/null")
    try:
        return json.loads(out) if rc == 0 and out.strip() else default
    except Exception:
        return default


def save_state(st):
    board(f"cat > {P}/watch.state.json.tmp && mv {P}/watch.state.json.tmp {P}/watch.state.json",
          inp=json.dumps(st, ensure_ascii=False))


def mentions(text, names):
    """@name at a word start, case-insensitive. @all = everyone; @fix also wakes fix-1, fix-2, ..."""
    found = set()
    for m in re.finditer(r'(?<![A-Za-z0-9_-])@([A-Za-z0-9_-]+)', text):
        n = m.group(1).lower()
        if n == 'all':
            found |= set(names)
            continue
        for k in names:
            if k.lower() == n or re.fullmatch(re.escape(n) + r'-\d+', k.lower()):
                found.add(k)
    return found


def parse_time(s):
    return datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))


def tick(state):
    roster = load_json('roster.json', None)
    if not roster:
        log('WARN roster.json missing/invalid')
        return
    coord = roster.get('coordinator', {})
    members = dict(roster.get('members', {}))
    if coord.get('pane'):
        members.setdefault('coordinator', {'machine': coord.get('machine'), 'agent': coord['pane'], 'wake_only': True})
    rc, out, err = board(f"{READ} {P} --json")
    if rc != 0:
        log(f"WARN herd-read failed rc={rc} {err[:100]}")
        return
    posts = [json.loads(l) for l in out.splitlines() if l.strip()]
    notified = state.setdefault('notified', {})            # "seq:name" -> 1
    blocked_logged = state.setdefault('blocked_logged', {})
    for p in posts:
        if p['seq'] <= state.get('cursor', 0):
            continue
        for name in sorted(mentions(p['text'], members)):
            if name == p['agent']:
                continue
            key = f"{p['seq']}:{name}"
            if key in notified:
                continue
            m = members[name]
            msg = MSG['wake'].format(seq=p['seq'], agent=p['agent'], text=p['text'][:80])
            res = prompt(m.get('machine'), m.get('agent', name), msg)
            if res is True:
                notified[key] = 1
                log(f"WAKE {name} for #{p['seq']}")
            elif res == 'blocked':
                if key not in blocked_logged:
                    blocked_logged[key] = 1
                    log(f"SKIP {name} blocked, #{p['seq']} pending")
            else:
                log(f"FAIL wake {name} #{p['seq']}: {res}")
    # The cursor stops just before the oldest post whose mention is still waiting on a blocked agent.
    pending = [int(k.split(':')[0]) for k in blocked_logged if k not in notified]
    top = posts[-1]['seq'] if posts else 0
    state['cursor'] = (min(pending) - 1) if pending else top
    if posts and posts[-1]['seq'] != state.get('last_seq'):
        state['last_seq'] = posts[-1]['seq']
        state['last_post_time'] = posts[-1]['time']
    alerts = state.setdefault('alerts', {})

    def alert(key, text):
        if key in alerts:
            return
        res = prompt(coord.get('machine'), coord.get('pane'), text)
        if res is True:
            alerts[key] = 1
            log(f"ALERT coordinator {key}")
        elif res == 'blocked':
            if f'b:{key}' not in alerts:
                log(f"ALERT {key} deferred: coordinator blocked")
            alerts[f'b:{key}'] = 1
        else:
            log(f"ALERT {key} fail: {res}")

    # Deadline: one alert once the absolute time has passed.
    dl = roster.get('deadline_utc')
    if dl:
        try:
            if now() >= parse_time(dl):
                alert('deadline', MSG['deadline'].format(dl=dl))
        except Exception as e:
            log(f"WARN bad deadline {dl}: {e}")
    # Stall: nobody posted for stall_minutes while some member has no [done] post.
    sm = roster.get('stall_minutes')
    done = {p['agent'] for p in posts if '[done]' in p['text'].lower()}
    notdone = [n for n, m in members.items() if n not in done and not m.get('wake_only')]
    if sm and notdone:
        ref = state.get('last_post_time') or state.setdefault('started', f"{now():%Y-%m-%dT%H:%M:%SZ}")
        if (now() - parse_time(ref)).total_seconds() > sm * 60:
            alert(f"stall:{state.get('last_seq', 0)}", MSG['stall'].format(sm=sm, names=', '.join(notdone)))
    # Quota: look for an exhausted-quota phrase in each member's pane.
    if DRY:
        return
    for name, m in members.items():
        if m.get('wake_only') or f"quota:{name}" in alerts:
            continue
        target = m.get('agent', name)
        rc, out, _ = herdr(m.get('machine'), 'agent', 'read', target, '--source', 'recent-unwrapped', '--lines', '60')
        if rc != 0:  # a working agent refuses the long read; fall back to the visible screen
            rc, out, _ = herdr(m.get('machine'), 'agent', 'read', target, '--source', 'visible')
        if rc == 0:
            hit = [l for l in out.splitlines() if QUOTA_RE.search(l) and '[board]' not in l and '[herd-watch]' not in l]
            if hit:
                alert(f"quota:{name}", MSG['quota'].format(name=name, line=hit[-1].strip()[:100]))


def main():
    global P, READ, DRY, MSG
    ap = argparse.ArgumentParser(
        prog='herd-watchd.py', formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__.split('\n\n')[0], epilog=__doc__.split('\n\n', 2)[2])
    ap.add_argument('project', help='project directory on the board host, or a bare name under $HERD_ROOT')
    ap.add_argument('--once', action='store_true', help='run a single poll and exit')
    ap.add_argument('--dry-run', action='store_true', help='log the prompts instead of sending them; never calls herdr')
    ap.add_argument('--print-path', action='store_true', help='print the resolved project path on the board host and exit')
    a = ap.parse_args()
    if not MSG:
        ap.error(f"HERD_LANG={LANG!r} is not supported (use en or zh)")
    path = board_path(a.project)
    if a.print_path:
        print(path)
        return
    P = shlex.quote(path)
    rbin = os.environ.get('HERD_REMOTE_BIN') or (os.path.dirname(os.path.abspath(__file__)) if LOCAL else ROOT.rstrip('/') + '/bin')
    if LOCAL:
        rbin = os.path.abspath(os.path.expanduser(rbin))
    elif rbin.startswith('~/'):
        rbin = './' + rbin[2:]
    READ = shlex.quote(rbin.rstrip('/') + '/herd-read')
    DRY = a.dry_run
    log(f"START pid={os.getpid()} interval={INTERVAL}s board={'local' if LOCAL else HOST} deny={sorted(DENY)}"
        + (' dry-run' if DRY else '') + (' once' if a.once else ''))
    state = load_json('watch.state.json', {})
    while True:
        try:
            tick(state)
            save_state(state)
        except Exception as e:
            log(f"ERROR {type(e).__name__}: {e}")
        if a.once:
            break
        time.sleep(INTERVAL)


if __name__ == '__main__':
    main()
