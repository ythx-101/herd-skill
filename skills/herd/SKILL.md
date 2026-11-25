---
name: herd
description: "Coordinate worker agents across one or more machines with herdr: preflight model quota, write a BRIEF and roster, start panes, pass messages on a JSONL board, let herd-watch wake @mentioned members and alert on deadline / stall / quota, and accept work only on a [done] post plus machine checks. Use when the user asks to coordinate, dispatch or herd several agents, or run a multi-agent arena (统筹、派活、多 agent 擂台). Requires herdr."
---

# herd: coordinate agents across machines

You are the coordinator. You plan, dispatch and accept work; the workers do it.

Paths below are relative to this skill's directory (`<skill>`). Check `herdr --help` and `herdr --skill` for the exact herdr syntax of your installed version.

## Pieces

| Piece | What it does |
|---|---|
| `bin/herd-post <project> <author> <text...\|->` | Append a post to `<project>/board.jsonl` (flock, prints the new seq). `-` reads the text from stdin. |
| `bin/herd-read <project> [--since N] [--json]` | Print posts with seq > N. |
| `bin/herd-preflight <machine> <model...>` | Ping each model: `ok`, `quota`, `no-capacity`, `timeout` or `error`. |
| `bin/herd-watch start\|stop\|status <project>` | Background watcher on the coordinator's machine. |
| `templates/BRIEF.md`, `templates/roster.json` | Copy and fill for each project. |

The board lives on one machine, the board host. Every member reaches it either directly or with `ssh <board-host> '<bin>/herd-read <project>'`.

Settings (environment variables on the coordinator's machine):

| Variable | Meaning | Default |
|---|---|---|
| `HERD_BOARD_HOST` | ssh host that stores the board; empty or `local` means this machine | local |
| `HERD_ROOT` | where bare project names live on the board host | `~/herd` |
| `HERD_REMOTE_BIN` | directory with `herd-post` / `herd-read` on the board host | this `bin/` when local, `$HERD_ROOT/bin` when remote |
| `HERD_INTERVAL` | watcher poll seconds | 20 |
| `HERD_DENY_PANES` | panes or agent names the watcher must never prompt | empty |
| `HERD_LANG` | language of the watcher's one-line prompts, `en` or `zh` | en |

A `<project>` argument is a path, or a bare name that resolves to `$HERD_ROOT/<name>`. With a remote board host, write `HERD_ROOT` as the remote machine sees it (`'~/herd'` in quotes, or an absolute remote path).

## Flow

### 1. Preflight (under 2 minutes)

```bash
<skill>/bin/herd-preflight box-a model-a model-b      # an ssh host, or "local"
```

Give work only to models that answer `ok`, and pick one fallback model per seat. `no-capacity` (503) is not quota: retry later. `quota` means the allowance is used up: switch model. The default ping uses the Antigravity CLI (`agy -p`); set `HERD_PING_CMD` for another CLI, for example `HERD_PING_CMD="claude -p 'Reply with exactly: pong' --model {model}"`.

### 2. Write the BRIEF and the roster (in the project directory on the board host)

- Copy `templates/BRIEF.md`. Write the deadline as an absolute time with its time zone and UTC. List the hard rules, the deliverables with one check command each, the fallback model and how to use the board. One per member or one shared, 60 lines at most.
- Copy `templates/roster.json`. `coordinator.pane` is your own pane id (`echo $HERDR_PANE_ID`). Set `deadline_utc` and `stall_minutes`. In `members`, each key is a member name mapped to `{machine, agent}`: `machine` is `local` or a saved herdr machine label (`herdr machine list`), `agent` is the herdr agent name. Member name = board author name = @ name; keep it equal to the herdr agent name.

### 3. Start the panes

- Local seat: `herdr pane split --current --direction down --cwd "$PWD" --no-focus`, read `.result.pane.pane_id`, then `herdr agent start <name> --kind <kind> --pane <id> -- <agent args>`.
- Seat on another machine: put `herdr --machine <label>` in front of every command and look the ids up on that machine; local ids do not apply there. If `pane split --cwd` does not land in the directory you asked for, run `herdr --machine <label> pane run <id> 'cd <dir>'` first and start the agent after it.
- An agent may open on a trust prompt or a start banner. Read the pane (`agent read`) and answer with `agent send-keys <name> enter` or `esc` before the first prompt. Do not trust the home directory itself.
- First prompt, nothing more: "You are `<name>`. Read `<project>/BRIEF.md` on the board host and follow it."
- Then post the assignment: `herd-post <project> coordinator '@<name> start, see BRIEF section N'`.
- Keep small machines small: two or three agents on a 2-core box, and send heavy work to a bigger machine.

### 4. Watch

```bash
<skill>/bin/herd-watch start <project>      # status / stop take the same argument
```

Every 20 seconds it reads new posts. A word-initial `@name` in a post (`@all` for everyone, `@coordinator` for you, `@fix` for `fix-1`, `fix-2`, ...) makes it send that member one line through `herdr agent prompt`: `[board] #seq from X: <first 80 characters>. Read the board with herd-read.` A blocked member is not prompted; the watcher logs it and delivers after the block clears. Each post reaches each member once (the cursor is `watch.state.json` in the project directory, so a restart continues where it stopped).

It also alerts the coordinator once for each of: the deadline passed; no new post for `stall_minutes` while a member has no `[done]` post; a member's pane shows `quota reached` or `RESOURCE_EXHAUSTED`.

The log is `<project>/watch.log`. `herd-watchd.py --once --dry-run <project>` shows what would be sent without calling herdr. The watcher reads the board and sends reminders. It does not judge whether work is finished.

### 5. Accept

- Work is finished only when the board has an `@coordinator [done]` post from that member and every check command in the BRIEF passes. herdr's `idle` or `done` state is not acceptance.
- Run the checks yourself in the project directory. If one fails, post `@<name> failed: <reason>`.
- At the deadline, accept what exists. A member who has not delivered is out. Do not extend.

### 6. Blind review (optional)

- Start a new member that has not read the board, on a different model when you can. Give it only the artifact paths and the scoring file: no author names, no discussion posts.
- Rename several artifacts to A / B / C before review. The reviewer writes its result to a file and posts `@coordinator [done] <path>`.

### 7. Wrap up

- Post one `[done] <summary>` without any @, then `herd-watch stop <project>`.
- Close only the panes you opened (`herdr [--machine <label>] pane close <id>`). Never touch a pane that belongs to someone else.
- Write a short summary in the project directory: result, time spent, pitfalls.

## Pitfalls

- `herdr pane wait-output --match X` also matches the command line echoed in the pane. Keep the marker word out of the command you send.
- The quota pattern matches exact phrases only. A bare `429` also appears in ordinary data, and `503 No capacity` is not quota.
- If herdr reports `server_unavailable` and a wait returns early, the worker may still be alive. Run `agent get` before deciding.
- Do not edit a script while a worker is running it. `pkill -f <pattern>` can kill the shell it runs in.
- When posting over ssh, send the text on stdin so the local shell does not expand `~`, `$` or quotes: `printf '%s' "$TEXT" | ssh <board-host> '<bin>/herd-post <project> <name> -'`.
- The watcher runs several ssh / herdr calls per member on every poll. With many members one round takes longer than 20 seconds.
- A mention of an agent that herdr cannot find is logged as `FAIL` and not retried.
