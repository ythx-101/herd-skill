# herd-skill

[English](README.md)

一个给协调者 agent 用的技能：用 [herdr](https://herdr.dev) 指挥多个编码 agent 一起干活，可以在一台机器上，也可以跨多台机器。协调者先测模型额度，写任务书，在 herdr 窗格里起 worker，然后通过一块 JSONL 公告板和它们传话。一个小守护进程负责唤醒被 @ 的成员，并在到截止时间、公告板长时间没动静、某个 worker 额度用完时提醒协调者。只有 worker 发了 `[done]` 帖，并且任务书里的检查命令全部通过，才算完成。

```
 协调者窗格 ── herd-post ──▶ board.jsonl（在公告板主机上）◀── herd-post ── worker 窗格
      ▲                            │                                  ▲
      │                      herd-watch 轮询                          │
      └──── 截止 / 停滞 / 额度提醒 ── herdr agent prompt ── 「@名字」唤醒
```

公告板、`@名字` 唤醒、`[done]` 约定都借鉴自 Google Antigravity CLI 的 `/swarm` 功能。这个技能把它们搬到了单个 agent 产品之外，所以 worker 可以是不同的 CLI、不同的模型、不同的机器。见[和 /swarm 的区别](#和-swarm-的区别)。

## 仓库里有什么

| 文件 | 用途 |
|---|---|
| `skills/herd/SKILL.md` | 协调者 agent 照着做的流程 |
| `skills/herd/bin/herd-post`、`herd-read` | 发帖和读帖（`board.jsonl`，flock 加锁，帖子有序号） |
| `skills/herd/bin/herd-watch`、`herd-watchd.py` | 后台守护：唤醒和提醒 |
| `skills/herd/bin/herd-preflight` | 派活前逐个模型试一下：`ok`、`quota`、`no-capacity`、`timeout` |
| `skills/herd/templates/` | `BRIEF.md`（英文）、`BRIEF.zh-CN.md`、`roster.json` |
| `herdr-plugin.toml`、`plugin/board.sh` | 可选的 herdr 插件：一个跟着公告板刷新的弹出窗格 |
| `tests/selftest.sh` | 自测，用假的 herdr 顶替，不需要 herdr 也不联网 |

## 依赖

- 协调者所在机器要装 [herdr](https://herdr.dev)，在 herdr 0.9.5 上测过。worker 在别的机器上时，那台机器也要有 herdr，并且在协调者这边存好机器配置（`herdr machine add`），因为守护进程用 `herdr --machine <标签>` 去够它们。
- `bash`、`python3`（3.9 以上，在 3.11 上测过）、`flock`（util-linux 自带）。
- 公告板在另一台机器上时，需要能免密 `ssh` 到那台机器。
- 至少一个 herdr 能启动的 agent CLI（Claude Code、Codex、Antigravity 等）。

## 安装

用 [skills](https://github.com/vercel-labs/skills) 命令行装成技能：

```bash
npx skills add ythx-101/herd-skill --skill herd -g
```

或者手动：

```bash
git clone https://github.com/ythx-101/herd-skill.git ~/herd-skill
mkdir -p ~/.claude/skills && ln -s ~/herd-skill/skills/herd ~/.claude/skills/herd     # Claude Code
mkdir -p ~/.agents/skills && ln -s ~/herd-skill/skills/herd ~/.agents/skills/herd     # Codex 等
```

公告板放在另一台机器上的话，那台机器只需要两个公告板脚本：

```bash
ssh hub 'mkdir -p ~/herd/bin'
scp skills/herd/bin/herd-post skills/herd/bin/herd-read hub:herd/bin/
```

可选的公告板弹窗：`herdr plugin install ythx-101/herd-skill`，在 `herdr plugin config-dir ythx-101.herd-board` 打印的目录里建一个 `config` 文件，写上 `HERD_PROJECT=<项目>`，再用 `herdr plugin pane open --plugin ythx-101.herd-board --entrypoint board` 打开。

检查装好没有：

```bash
bash tests/selftest.sh        # 最后一行是 SELFTEST_OK
```

## 最小示例（一台机器，一个 worker）

在 herdr 窗格里运行。`B` 是技能的 `bin` 目录。

```bash
B=~/.claude/skills/herd/bin
mkdir -p ~/herd/demo && cd ~/herd/demo

# 1. 花名册：有谁，什么时候截止（GNU date；macOS 用 date -u -v+30M +%FT%TZ）
cat > roster.json <<JSON
{"project": "demo",
 "coordinator": {"machine": "local", "pane": "$HERDR_PANE_ID"},
 "deadline_utc": "$(date -u -d '+30 minutes' +%FT%TZ)",
 "stall_minutes": 10,
 "members": {"builder": {"machine": "local", "agent": "builder"}}}
JSON

# 2. 任务书：抄模板再填
cp "$B/../templates/BRIEF.zh-CN.md" BRIEF.md

# 3. 在新窗格里起一个 worker（这里用 Claude Code）
pane=$(herdr pane split --current --direction down --cwd "$PWD" --no-focus | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["pane"]["pane_id"])')
herdr agent start builder --kind claude --pane "$pane"
herdr agent prompt builder "你是 builder。读 ~/herd/demo/BRIEF.md 并照做。"

# 4. 在公告板上派活，启动守护
"$B/herd-post" demo coordinator '@builder 开工，见 BRIEF'
HERD_LANG=zh "$B/herd-watch" start demo

# 5. 之后
"$B/herd-read" demo            # worker 的「@coordinator [done] ...」帖会出现在这里
"$B/herd-watch" stop demo
```

平时不用自己敲这些：让你的 agent「统筹」一个任务，它会照 `SKILL.md` 做。

## 多台机器

选一台机器放公告板（假设它的 ssh 名字是 `hub`），在协调者的机器上设置：

```bash
export HERD_BOARD_HOST=hub
export HERD_ROOT='~/herd'            # 按公告板主机的视角写，引号要留着
export HERD_REMOTE_BIN='~/herd/bin'  # 公告板主机上 herd-post / herd-read 的位置
```

`roster.json` 里，别的机器上的成员写 `"machine": "<herdr 机器标签>"`。worker 用 `ssh hub '~/herd/bin/herd-read demo'` 读，用 `printf '%s' "$TEXT" | ssh hub '~/herd/bin/herd-post demo <名字> -'` 发。

全部设置：

| 变量 | 含义 | 默认 |
|---|---|---|
| `HERD_BOARD_HOST` | 存公告板的 ssh 主机；空或 `local` 表示本机 | 本机 |
| `HERD_ROOT` | 公告板主机上，只写项目名时去哪个目录找 | `~/herd` |
| `HERD_REMOTE_BIN` | 公告板主机上 `herd-read` 所在目录 | 本机时是技能的 `bin`，远程时是 `$HERD_ROOT/bin` |
| `HERD_INTERVAL` | 守护轮询间隔（秒） | 20 |
| `HERD_DENY_PANES` | 守护绝不发消息的窗格或 agent 名，逗号分隔 | 空 |
| `HERD_QUOTA_RE` | 判断窗格里「额度耗尽」字样的正则 | `quota reached\|RESOURCE_EXHAUSTED\|exhausted your quota` |
| `HERD_LANG` | 守护发出的提醒用什么语言：`en` 或 `zh` | en |
| `HERD_HERDR_BIN` | herdr 可执行文件 | `$HERDR_BIN_PATH`，没有就用 `herdr` |
| `HERD_RUN_DIR` | `herd-watch` 的 pid 文件目录 | `~/.local/state/herd` |
| `HERD_PING_CMD` | preflight 的命令模板，含 `{model}` | Antigravity 的 `agy -p ...` |

## 已知坑

- **完成 = `[done]` 帖 + 检查通过。** herdr 的 `idle` / `done` 状态只说明 agent 停手了，不说明活干对了。
- **`herd-preflight` 只在 Antigravity CLI 上测过。** 别的 CLI 通过 `HERD_PING_CMD` 接入，对它们报错文字的归类只是尽力而为。默认的 ping 命令带 `--dangerously-skip-permissions`，为的是无人值守时不卡在权限确认上；不想要就改模板。
- **额度检测靠读窗格文字。** 只匹配精确短语，也会跳过守护自己发的行，但 worker 在正文里引用这些短语时仍可能误报一次。
- **`503 No capacity` 不是额度。** 稍后重试，不用换模型。
- **@ 到 herdr 找不到的 agent，只记一条 `FAIL`，不重试。** 对方处于 blocked 时会一直重试到解除。
- **经 ssh 发帖：** 正文走标准输入（`herd-post <项目> <名字> -`），否则本地 shell 会把 `~`、`$` 和引号展开。
- **成员多了守护会变慢。** 每一轮对每个成员都要跑几次 ssh / herdr。
- **`herdr pane wait-output --match X`** 也会匹配到窗格里回显的命令行，标记字不要放在发出去的命令里。
- **没有沙盒，也没有花费上限。** 每个 worker 花的是它自己那个 CLI 的额度。这个技能只提供开工前的试探和额度耗尽提醒。
- 守护处理 blocked 成员的那条路径只在假 herdr 的自测里跑过，还没有在真实 herdr 上验证。

## 和 /swarm 的区别

`/swarm` 是 Google Antigravity CLI 内置的多 agent 模式（[文档](https://antigravity.google/docs/swarm.md)）。这个技能是研究过它之后写的，沿用了它最好的几个想法。两者适合的场合不同。

| | Antigravity `/swarm` | herd 技能 |
|---|---|---|
| 在哪运行 | 一个 Antigravity 会话里，当前对话变成协调者 | 不依附某个产品，herdr 能启动的 CLI 都行 |
| worker | 协调者用内置工具创建，各自是全新对话 | 任意已保存机器上的 herdr 窗格 |
| 公告板 | 内置，支持 `@名字`、编号组、`@all`，有全屏看板和进度总览 | 一个 JSONL 文件加两个 shell 脚本，看板就是 herdr 窗格 |
| 唤醒 | 原生：等待中的成员不耗额度，被 @ 就醒 | 守护进程每 20 秒轮询，发一行提醒 |
| 模型 | worker 默认和协调者同模型；可原地换模型并保留上下文 | 每个席位用什么 CLI 和模型由你启动时决定；换模型要重起席位 |
| 工作区 | 可以给每个 worker 单独的工作副本 | 没有；靠任务书划分各自负责的文件 |
| 验收 | 协调者判断；建议配一个 verifier 成员 | `[done]` 帖加任务书里的检查命令；硬截止时间 |
| 额度 | 低额度预警，可实时查余量 | 开工前试探，窗格出现额度耗尽字样时提醒 |
| 上手 | 一条命令 | 要有 herdr、ssh 密钥、花名册和任务书 |

如果 worker 都在一台机器上、用同一家模型，`/swarm` 更省事，集成也更好。席位需要不同的 CLI、模型或机器，或者想把验收绑在命令和截止时间上时，用这个技能。

## 许可证

[MIT](LICENSE)
