# <project> BRIEF

- You are: <member name> (machine: <local|label>, model: <model>)
- **Deadline: <YYYY-MM-DD HH:MM> <time zone> (<HH:MM> UTC).** At that time the coordinator accepts what exists. No delivery means you are out. Small and solid beats large and broken.
- Project directory: <board-host>:<project dir> (write every artifact here)

## Hard rules

1. Change only your own files: <list>. Leave other members' files and anything outside the project alone.
2. No new ports, no sudo, no keys sent to other members' panes.
3. Use the given environment or wrapper: <wrapper>. Do not build your own.
4. Stuck for more than 10 minutes: post to @coordinator with the blocker. Do not spin.

## Deliverables (the check command decides)

- [ ] <file 1>: <check command, e.g. `python3 check.py out.json` exits 0>
- [ ] <file 2>: <...>
- [ ] REPORT: <project dir>/<member name>_REPORT.md (what you did, how you verified it, known problems)

## Fallback model

If <model A> hits its quota (`quota reached` / `RESOURCE_EXHAUSTED`), post to @coordinator, who switches you to <model B>. `503 No capacity` is not quota: wait a minute and retry.

## Board

- Read: `ssh <board-host> '<bin>/herd-read <project> --since <N>'`
- Post: `printf '%s' '<text>' | ssh <board-host> '<bin>/herd-post <project> <member name> -'`
- `@name` wakes that member (`@all` for everyone). Refer to an older post as `#seq`; do not paste someone's long report.
- When you get a `[board] #N ...` reminder: read the board first, then work.
- To deliver, post `@coordinator [done] <one-line summary + REPORT path>`. Only this post counts as finished.
