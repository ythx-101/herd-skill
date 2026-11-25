# herd-skill

[中文说明](README.zh-CN.md)

An agent skill for coordinating several coding agents with [herdr](https://herdr.dev), across one machine or many. One agent is the coordinator. It checks model quota, writes a brief, starts workers in herdr panes, and talks to them through a plain JSONL message board. A small watcher wakes whoever is @mentioned and tells the coordinator when the deadline passes, the board goes quiet, or a worker runs out of quota. Work counts as finished only when the worker posts `[done]` and the check commands in the brief pass.

```
 coordinator pane ── herd-post ──▶ board.jsonl (on the board host) ◀── herd-post ── worker panes
        ▲                               │                                   ▲
        │                         herd-watch polls                          │
        └──── deadline / stall / quota alerts ── herdr agent prompt ── "@name" wake-ups
```

The design borrows the board, `@name` wake-ups and `[done]` convention from the `/swarm` feature of Google's Antigravity CLI, and moves them outside any single agent product so that workers can be different CLIs, different models and different machines. See [How it differs from /swarm](#how-it-differs-from-swarm).

## What is in the box

| File | Purpose |
|---|---|
| `skills/herd/SKILL.md` | The instructions the coordinator agent follows |
| `skills/herd/bin/herd-post`, `herd-read` | Append to and read the board (`board.jsonl`, flock-protected, numbered posts) |
| `skills/herd/bin/herd-watch`, `herd-watchd.py` | Background watcher: wake-ups and alerts |
| `skills/herd/bin/herd-preflight` | Ping each model before dispatch: `ok`, `quota`, `no-capacity`, `timeout` |
| `skills/herd/templates/` | `BRIEF.md` (English), `BRIEF.zh-CN.md`, `roster.json` |
| `herdr-plugin.toml`, `plugin/board.sh` | Optional herdr plugin: a popup pane that follows the board |
| `tests/selftest.sh` | Self-test with a stub herdr; needs no herdr and no network |

## Requirements

- [herdr](https://herdr.dev) on the coordinator's machine. Tested with herdr 0.9.5. Workers on other machines need herdr there and a saved machine profile (`herdr machine add`), because the watcher reaches them with `herdr --machine <label>`.
- `bash`, `python3` (3.9 or newer; tested on 3.11), `flock` (util-linux).
- `ssh` with key login to the board host when the board is on another machine.
- At least one agent CLI that herdr can start (Claude Code, Codex, Antigravity and so on).

## Install

As a skill, with the [skills](https://github.com/vercel-labs/skills) CLI:

```bash
npx skills add ythx-101/herd-skill --skill herd -g
```

Or by hand:

```bash
git clone https://github.com/ythx-101/herd-skill.git ~/herd-skill
mkdir -p ~/.claude/skills && ln -s ~/herd-skill/skills/herd ~/.claude/skills/herd     # Claude Code
mkdir -p ~/.agents/skills && ln -s ~/herd-skill/skills/herd ~/.agents/skills/herd     # Codex and others
```

If the board lives on another machine, that machine needs only the two board scripts:

```bash
ssh hub 'mkdir -p ~/herd/bin'
scp skills/herd/bin/herd-post skills/herd/bin/herd-read hub:herd/bin/
```

Optional board popup for herdr: `herdr plugin install ythx-101/herd-skill`, then write `HERD_PROJECT=<project>` into the file `config` inside `herdr plugin config-dir ythx-101.herd-board`, and open it with `herdr plugin pane open --plugin ythx-101.herd-board --entrypoint board`.

Check the install:

```bash
bash tests/selftest.sh        # ends with SELFTEST_OK
```

## Smallest example (one machine, one worker)

Run this inside a herdr pane. `B` is the skill's `bin` directory.

```bash
B=~/.claude/skills/herd/bin
mkdir -p ~/herd/demo && cd ~/herd/demo

# 1. roster: who is in, when it ends (GNU date; on macOS use: date -u -v+30M +%FT%TZ)
cat > roster.json <<JSON
{"project": "demo",
 "coordinator": {"machine": "local", "pane": "$HERDR_PANE_ID"},
 "deadline_utc": "$(date -u -d '+30 minutes' +%FT%TZ)",
 "stall_minutes": 10,
 "members": {"builder": {"machine": "local", "agent": "builder"}}}
JSON

# 2. brief: copy the template and fill it in
cp "$B/../templates/BRIEF.md" BRIEF.md

# 3. start a worker in a new pane (here: Claude Code)
pane=$(herdr pane split --current --direction down --cwd "$PWD" --no-focus | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["pane"]["pane_id"])')
herdr agent start builder --kind claude --pane "$pane"
herdr agent prompt builder "You are builder. Read ~/herd/demo/BRIEF.md and follow it."

# 4. assign on the board and start the watcher
"$B/herd-post" demo coordinator '@builder start, see BRIEF'
"$B/herd-watch" start demo

# 5. later
"$B/herd-read" demo            # the worker's "@coordinator [done] ..." post shows up here
"$B/herd-watch" stop demo
```

Normally you do not type these yourself: ask your agent to "herd" a task and it follows `SKILL.md`.

## Several machines

Pick one machine as the board host (say its ssh name is `hub`) and set this on the coordinator's machine:

```bash
export HERD_BOARD_HOST=hub
export HERD_ROOT='~/herd'            # as the board host sees it; keep the quotes
export HERD_REMOTE_BIN='~/herd/bin'  # where herd-post / herd-read are on the board host
```

In `roster.json`, a member on another machine has `"machine": "<herdr machine label>"`. Workers read and post with `ssh hub '~/herd/bin/herd-read demo'` and `printf '%s' "$TEXT" | ssh hub '~/herd/bin/herd-post demo <name> -'`.

All settings:

| Variable | Meaning | Default |
|---|---|---|
| `HERD_BOARD_HOST` | ssh host that stores the board; empty or `local` = this machine | local |
| `HERD_ROOT` | where bare project names live on the board host | `~/herd` |
| `HERD_REMOTE_BIN` | directory with `herd-read` on the board host | the skill's `bin` (local), `$HERD_ROOT/bin` (remote) |
| `HERD_INTERVAL` | watcher poll interval, seconds | 20 |
| `HERD_DENY_PANES` | comma-separated panes / agent names the watcher must never prompt | empty |
| `HERD_QUOTA_RE` | regex for "quota exhausted" text in a pane | `quota reached\|RESOURCE_EXHAUSTED\|exhausted your quota` |
| `HERD_LANG` | language of the watcher's prompts: `en` or `zh` | en |
| `HERD_HERDR_BIN` | herdr binary | `$HERDR_BIN_PATH`, else `herdr` |
| `HERD_RUN_DIR` | pid files of `herd-watch` | `~/.local/state/herd` |
| `HERD_PING_CMD` | preflight command template with `{model}` | Antigravity `agy -p ...` |

## Known pitfalls

- **Finished means `[done]` plus checks.** herdr's `idle` / `done` state says the agent stopped typing, not that the work is right.
- **`herd-preflight` is only tested with the Antigravity CLI.** Other CLIs go through `HERD_PING_CMD`; the classification of their error text is a best guess. The default ping passes `--dangerously-skip-permissions` so that a headless run does not stop at a permission prompt; change the template if you do not want that.
- **Quota detection reads pane text.** It matches exact phrases and skips the watcher's own lines, but a worker that quotes such a phrase can still trigger one false alert.
- **`503 No capacity` is not quota.** Retry later instead of switching model.
- **A mention of an agent herdr cannot find is logged as `FAIL` and not retried.** A blocked agent is retried until it unblocks.
- **Posting over ssh:** send the text on stdin (`herd-post <project> <name> -`), or the local shell expands `~`, `$` and quotes.
- **Many members slow the watcher down.** Each poll runs a few ssh / herdr calls per member.
- **`herdr pane wait-output --match X`** also matches the echoed command line; keep the marker out of the command you send.
- **No sandbox, no cost cap.** Each worker spends its own CLI's quota. The skill gives you a preflight and a quota alert, nothing more.
- The watcher's blocked-agent path is covered by the stub self-test, not yet by a live herdr run.

## How it differs from /swarm

`/swarm` is the built-in multi-agent mode of Google's Antigravity CLI ([docs](https://antigravity.google/docs/swarm.md)). This skill was written after studying it and reuses its best ideas. The two are for different situations.

| | Antigravity `/swarm` | herd skill |
|---|---|---|
| Where it runs | Inside one Antigravity session; the conversation becomes the coordinator | Outside any one product; any CLI that herdr can start |
| Workers | Created by the coordinator with a built-in tool; fresh conversations | herdr panes on any saved machine |
| Board | Built in, with `@name`, groups, `@all`, a full-screen board and an overview | A JSONL file plus two shell scripts; herdr panes are the view |
| Wake-ups | Native: a waiting member costs nothing and wakes on a mention | A polling watcher (20 s) sends a one-line prompt |
| Models | Workers default to the coordinator's model; switching in place keeps context | Each seat is whatever CLI and model you start; switching means restarting the seat |
| Workspaces | Per-worker working copies available | None; the brief assigns file ownership |
| Acceptance | Coordinator's judgement; a verifier worker is recommended | `[done]` post plus the brief's check commands; hard deadline |
| Quota | Low-quota warnings, live quota query | Preflight ping, quota phrase alert |
| Setup | One command | herdr, ssh keys, a roster and a brief |

If your workers are all on one machine and one model family, `/swarm` is less work and better integrated. Use this skill when the seats need different CLIs, models or machines, or when you want acceptance tied to commands and a deadline.

## License

[MIT](LICENSE)
