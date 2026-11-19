# agent-worktree

Opt any agent of a rig into its own stable git worktree, by config alone.
The pack also ships a rig-scoped `chat` agent that gives every chat session
its own worktree.

Formerly `chat-worktree`.

## Purpose

A rig's agents normally all run in the rig's shared checkout. When several
long-lived named agents, chat sessions and the rig's workers edit that one
working tree, they clobber each other's edits, leave surprise dirty state,
and switch branches underneath each other.

This pack gives an agent its own git worktree of the rig, on its own branch,
so it never shares the rig checkout:

- **Named agents** (for example `oversight-rig.project-lead`) are opted in
  per rig with a config patch. The pack that defines the agent is not
  touched. Each opted-in agent keeps one stable worktree across restarts.
- **Chat sessions** of the pack's own `chat` agent each get a worktree of
  their own. This is a ready-made example of the same mechanism.

The model is gastown's polecat, without `--sync` and without
`.beads/redirect`. No gc change is needed.

## How it works

The agent's `work_dir` points at a directory under `<rig>/worktrees/`, and a
host `pre_start` entry makes sure that directory is a git worktree of the rig
before the session starts. The entry runs
`assets/scripts/agent-worktree.sh <prefix> <rig-root> <target-dir> <name>`:

- for an opted-in named agent, through the pack command
  `gc <binding> ensure`, which always uses the prefix `agent`;
- for the pack's `chat` agent, directly through `{{.ConfigDir}}`, with the
  prefix `chat`.

The script:

1. Checks its arguments. The prefix must be one lowercase word, the branch
   name must pass `git check-ref-format --branch`, and the target directory
   must be named `<prefix>-<name>`. A mismatch fails loudly instead of
   letting two sessions derive the same branch.
2. Refuses a rig root that is itself a linked worktree (see
   [Refusals and the nesting guard](#refusals-and-the-nesting-guard)).
3. Keeps `/worktrees/` and `/.gc/` in the rig's local exclude file,
   `$(git rev-parse --git-common-dir)/info/exclude`. The rig checkout's
   `git status` then does not list the worktrees, and the settings gc writes
   into each worktree never show up as untracked. The file is local and
   untracked; each line is added once, also when sessions start in parallel.
   A tracked `.gitignore` is never touched.
4. Reuses an existing worktree of this rig as it is (see
   [Restart and reuse](#restart-and-reuse)).
5. Accepts gc's own files. gc writes its managed settings
   (`.gc/settings.json`) and each provider's hook or overlay files
   (`.claude/`, `.codex/`, `.gemini/`, `.github/`, `.cursor/`, `.opencode/`,
   `.pi/`, `.kiro/` with `AGENTS.md`, and the like) into the work_dir before
   pre_start runs. A directory holding only those is accepted: they are set
   aside, the worktree is added, and they are put back wherever the checkout
   has no file of its own. If the worktree cannot be added, they are put back
   as they were.
6. Checks out a leftover branch `gc-<prefix>-<name>` again exactly where it
   was, without a reset.
7. Otherwise cuts a new branch, with `--no-track`, from the first of:
   1. `refs/remotes/origin/HEAD`, if set;
   2. `refs/remotes/origin/main`, if present;
   3. the rig's current `HEAD` (a repo with no `origin`).

   Before cutting, it fetches that remote branch. The fetch is bounded by
   `AGENT_WORKTREE_FETCH_TIMEOUT` seconds: default 5, clamped to 1..8, which
   leaves room inside gc's 10 s setup timeout. On a timeout or a failure it
   cuts from the last fetched ref. Only a new branch fetches.

The script sets `GIT_LFS_SKIP_SMUDGE=1` and `GIT_TERMINAL_PROMPT=0`, so it
never blocks on LFS downloads or a credential prompt. It never pulls,
rebases, resets or syncs, and it writes no `.beads/redirect`.

Because the session's work_dir is then inside a linked git worktree, herdr
opens it as a worktree workspace under the rig's workspace. That is the gc
change [gascity#1](https://github.com/UndermountainCC/gascity/pull/1)
(`gcs-mfi`), "open linked-worktree sessions as herdr worktree workspaces".
Without it, herdr shows the session as a tab (see
[Known limits](#known-limits)).

## Install

The pack has two uses, through two independent imports. Use either one or
both.

### City import: opt-ins

Opting in a named agent needs the pack command `gc <binding> ensure`. Only a
city import provides it: rig imports expose no pack commands. Import the pack
in the city's `pack.toml`:

```toml
[imports.agent-worktree]
source = "https://github.com/gastownhall/gascity-packs.git//agent-worktree"
agents_exclude = ["chat"]
```

A local checkout works the same way:
`source = "/path/to/gascity-packs/agent-worktree"`.

- The binding, here `agent-worktree`, is the command's name:
  `gc agent-worktree ensure`. The snippets below assume it.
- A city import stamps the pack's rig-scoped agents into every rig.
  `agents_exclude = ["chat"]` keeps `chat` out of every rig while the
  command stays available. Drop it only if you want `chat` in every rig.

### Rig import: chat

The `chat` agent comes from a rig import in `city.toml`:

```toml
[[rigs]]
name = "your-project"

[rigs.imports.chat]
source = "https://github.com/gastownhall/gascity-packs.git//agent-worktree"
```

The binding is the qualified-name prefix: with the binding `chat` the agent
is `<rig>/chat.chat`. The pack pins no `provider`, so the agent inherits the
city's or rig's default. A rig import provides no `ensure` command.

### Both together

The two imports can coexist: the city import for opt-ins and a rig import
for `chat` in the rigs that want it. A city import stamps its agents into
every rig except the rigs that declare a rig import with the same binding;
such a rig gets its own copy, not the city's.

## Opt in a named agent

Worked example: opt `oversight-rig.project-lead` in for rig `feedlot-packs`.
`oversight-rig` defines the agent with `work_dir = "{{.RigRoot}}"` and no
`pre_start`. This needs the [city import](#city-import-opt-ins).

Add one patch to the city's `city.toml`:

```toml
[[patches.agent]]
dir = "feedlot-packs"
name = "oversight-rig.project-lead"
work_dir = "{{.RigRoot}}/worktrees/agent-{{.AgentBase}}"
pre_start_append = ["gc agent-worktree ensure {{.RigRoot}} {{.WorkDir}} {{.AgentBase}}"]
```

- `dir` is the rig. `name` is the agent's name with its import binding,
  `<binding>.<agent>`, here `oversight-rig.project-lead`. The same agent in
  other rigs, and every other agent of this rig, are unaffected.
- `gc agent-worktree ensure` runs the pack's script with the prefix `agent`,
  wherever gc installed the pack. `agent-worktree` is the city import's
  binding; use yours if it differs.
- Use `{{.AgentBase}}`, not `{{.Agent}}`. `{{.Agent}}` is the full qualified
  identity, `feedlot-packs/oversight-rig.project-lead`, which contains a `/`
  and so is not a single path component. `{{.AgentBase}}` is the part after
  the last `/`, `oversight-rig.project-lead`. It keeps the binding, so it
  stays unique when two bindings in one rig define the same local name, and
  it renders the same on every restart.
- Do not use `{{.ConfigDir}}` in a patch: there it resolves to the target
  agent's pack (`oversight-rig`), not to this one.
- Put no gc flags before the binding. `gc --city X agent-worktree ensure …`
  passes `--city X` to the command, which then refuses its arguments. The
  command finds the city through `GC_CITY`, which every session's pre_start
  has.
- `gc` must be on the supervisor's `PATH`, because pre_start runs
  `sh -c` with the controller's environment.

If the city already has a `[[patches.agent]]` block for this agent and rig,
add `work_dir` and `pre_start_append` to that block, or add exactly one new
block. For example, a city that keeps per-rig blocks such as:

```toml
[[patches.agent]]
dir = "feedlot-packs"
name = "oversight-rig.project-lead"
suspended = true
```

can extend that block with the two keys. gc applies every matching block in
order and does not reject duplicates. Two blocks that both carry
`pre_start_append` run the ensure command twice. That is harmless, because the
script is idempotent, but it is noise.

**Secondary form, for agents the rig imports itself.** An agent from one of
the rig's own imports can also be patched in its `[[rigs]]` entry, by its bare
local name:

```toml
[[rigs]]
name = "your-project"

[[rigs.patches]]
agent = "project-lead"
work_dir = "{{.RigRoot}}/worktrees/agent-{{.AgentBase}}"
pre_start_append = ["gc agent-worktree ensure {{.RigRoot}} {{.WorkDir}} {{.AgentBase}}"]
```

`[[rigs.patches]]` matches only agents of the rig's own packs and imports,
and only by the bare local name. A qualified key such as
`oversight-rig.project-lead` errors with "not found in pack", and an agent
that comes from a city import is not reached at all: the patch is silently
not applied. Use `[[patches.agent]]` for city-imported agents. Either form
still needs the city import for the `ensure` command.

**Verify** the result with `gc config show` (or `gc config show --provenance`):

- the patched agent shows
  `work_dir = "{{.RigRoot}}/worktrees/agent-{{.AgentBase}}"` and the ensure
  command in its `pre_start`, after its own entries;
- the same agent in other rigs, and the rig's other agents, are unchanged.

**Adopting an existing named session.**

gc records a session's work_dir on its session bead when the bead is
created, and every later start of that bead reuses it. A named session that
already existed before the patch therefore keeps starting in the rig root.
The ensure command still creates the worktree, but the session does not move
into it. In the scratch city, all of these kept the old dir:

- the config-drift restart that loading the patch triggers;
- `gc session suspend` followed by `gc session wake`;
- `gc session reset`;
- `gc session kill`.

To adopt the patch, close the named session once, after the patch is loaded:

```sh
gc session close <rig>/<binding>.<agent>    # for example feedlot-packs/oversight-rig.project-lead
```

> **Warning:** closing ends the session's conversation. The new session
> starts with a fresh provider conversation, not a resumed one.

The controller then creates the named session again on a new bead, which
records the worktree as its work_dir. Later restarts, including
`gc session kill`, keep it. Work assigned to the named identity stays
assigned and is served by the new session (`gc session close --help`). This
was verified with `mode = "always"`, where the controller recreates the
session at once (see [E2E evidence](#e2e-evidence)). Only an existing session
needs this step: a named session first created after the patch starts in
its worktree.

## pre_start ordering

The effective `pre_start` of an opted-in agent runs in this order:

1. the agent's own `pre_start` entries, from the pack that defines it;
2. the `pre_start_append` entries of the matching patches, in patch order,
   which includes the ensure command;
3. gc's own skill and MCP materialization entries, which gc appends after
   both.

So with `pre_start_append` the worktree exists before gc's entries, but after
the agent's own entries. `project-lead` has none, so the snippet above is
enough. The order observed in the scratch city is recorded under
[E2E evidence](#e2e-evidence).

If an agent's own `pre_start` writes into `{{.WorkDir}}`, those entries would
run before the worktree exists. For such an agent, replace the whole list
with `pre_start`, putting the ensure command first and copying the agent's
own entries after it:

```toml
[[patches.agent]]
dir = "your-project"
name = "<binding>.<agent>"
work_dir = "{{.RigRoot}}/worktrees/agent-{{.AgentBase}}"
pre_start = [
  "gc agent-worktree ensure {{.RigRoot}} {{.WorkDir}} {{.AgentBase}}",
  "<the agent's own pre_start entries, copied in order>",
]
```

`pre_start` replaces the list, so this copy must be maintained by hand: when
the target pack changes its `pre_start`, update the patch. Copied entries may
keep `{{.ConfigDir}}`, which in a patch resolves to the target agent's pack,
the right place for its own scripts.

## Naming

| Use | Worktree | Branch |
| --- | --- | --- |
| Opted-in named agent | `<rig>/worktrees/agent-<AgentBase>` | `gc-agent-<AgentBase>` |
| Chat session | `<rig>/worktrees/chat-<name>` | `gc-chat-<name>` |

For the worked example, `{{.AgentBase}}` is `oversight-rig.project-lead`, so
the agent gets `feedlot-packs/worktrees/agent-oversight-rig.project-lead` on
`gc-agent-oversight-rig.project-lead`. Dots are valid in branch names. The
value observed in the scratch city is recorded under
[E2E evidence](#e2e-evidence).

For chat, `<name>` is the session's `{{.AgentBase}}`; see
[Chat example](#chat-example).

## Restart and reuse

- **Existing worktree.** If the work_dir is already a worktree of this rig
  (same common git dir, and the work_dir is its top level), the script exits
  0 at once. It does no fetch, checkout or reset, so uncommitted and
  untracked work survives a restart or reattach. An opted-in agent's branch
  is never synced with origin on restart.
- **Removed directory.** If the worktree directory is gone but its branch
  exists, the script prunes the stale worktree entry and checks the branch
  out again exactly where it was.
- **Nothing is ever deleted.** The pack never removes a worktree or a branch
  (see [Cleanup](#cleanup)).
- **No migration.** Work left uncommitted in the rig checkout stays there. An
  agent opted in after it has run in the rig checkout starts on a new branch,
  cut from the remote default branch.

## Refusals and the nesting guard

The session does not start, and nothing is created, when the script refuses:

- **Bad arguments.** An invalid prefix, a name that is not a valid branch
  component, or a work_dir that is not named `<prefix>-<name>`.
- **Partially created worktree.** If a previous `git worktree add` was killed
  halfway, its lock still reads `initializing`. The script refuses it rather
  than repair it; remove it by hand (see [Cleanup](#cleanup)).
- **Foreign directory.** A non-empty work_dir that is not a worktree of this
  rig is refused, untouched, unless it holds only gc's own files. That
  includes a separate repository and a different clone of the same origin.
- **Nesting guard.** A rig root that is itself a linked worktree (its
  `git-dir` differs from its `git-common-dir`) is refused before anything is
  written, even the exclude file, because a worktree added under it would
  nest inside that worktree.

An empty work_dir is used as the creation target. Every refusal is one
`stderr` line starting with `agent-worktree:` and naming the path, and the
script exits 1. gc treats a failed pre_start as fatal for that start. The
`ensure` command also refuses, with a usage line, anything but exactly three
arguments.

## Do not opt in

Some agents create per-bead worktrees relative to the directory they run in.
Inside an opted-in worktree those would nest. Do not opt in:

- `gc.run-operator`;
- the `gc.implementation-worker` pool;
- any agent running a formula that creates `$(pwd)/worktrees/<id>`, such as
  the `implementation-base` `prepare-worktree` step and the
  `build-basic-review` setup step;
- any gascity roles-pack agent: they all assume `$(pwd)` is the rig root, so
  keep them roots-only.

The limit of the guard: it checks only the rig root it is given. It cannot
detect an opted-in agent that later creates `$(pwd)/worktrees/<id>` inside
its own worktree, so this list is the primary control.

## Chat example

The pack's `chat` agent is the mechanism applied per session.
`agents/chat/agent.toml` sets:

```toml
work_dir = "{{.RigRoot}}/worktrees/chat-{{.AgentBase}}"
pre_start = ["{{.ConfigDir}}/assets/scripts/agent-worktree.sh chat {{.RigRoot}} {{.WorkDir}} {{.AgentBase}}"]
```

`{{.ConfigDir}}` is right here, because this pack defines the agent. Import
the pack at rig scope (see [Rig import: chat](#rig-import-chat)).
`min_active_sessions = 0`, so nothing starts until someone opens a session.
At most 8 sessions run at once (`max_active_sessions`), and an idle session
stops after 4 hours (`idle_timeout`).

### Use

```sh
gc session new your-project/chat.chat               # open and attach
gc session new your-project/chat.chat --no-attach   # open in the background
gc session new your-project/chat.chat --alias auth-fix
```

How the session is named decides its worktree and branch:

- **Without `--alias`**, gc 1.5.1 names each session `chat-adhoc-<10 hex>`.
  The pack sees `{{.AgentBase}}` as `chat.chat-adhoc-<hex>` (the binding is
  prefixed), so the session gets:
  - worktree `<rig>/worktrees/chat-chat.chat-adhoc-<hex>`;
  - branch `gc-chat-chat.chat-adhoc-<hex>`.

  Every such session gets a fresh worktree and branch, and none is ever
  reused.
- **With `--alias <name>`**, the worktree and branch are named after the
  alias: `<rig>/worktrees/chat-chat.<name>` on `gc-chat-chat.<name>`. A later
  session with the same alias resumes them.

Use `--alias` for any conversation you expect to come back to.

`namepool.txt` names (`alder`, `birch`, …) are not used by `gc session new`.
They only name the reconciler's pool slots (shown by `gc status` as
`<rig>/chat.alder` and so on) and cap their number.

### Name uniqueness

Two sessions open at once always get two different worktrees under gc 1.5.1.
`gc session new` does not draw names from the namepool. It gives each session
without `--alias` a random `chat-adhoc-<10 hex>` name, and it refuses a
second session with an alias that an open session already holds. The script
also refuses a work_dir that does not end in `chat-<name>`, so the two values
cannot drift apart silently.

This was checked in a throwaway scratch city, never a live one, before the
pack was renamed to `agent-worktree`; the `chat` agent, its binding and the
script's chat-mode behaviour are unchanged since. The setup: gc 1.5.1, tmux
runtime, a shell provider (`sh -c 'exec sleep 86400'`) as the city default,
and the pack imported at rig scope with binding `chat` on a toy rig cloned
from a local bare origin. A temporary wrapper around the script logged the
arguments and `GC_*` environment each pre_start received.

```sh
gc config show                    # agent "chat", dir "demo", scope "rig", no provider,
                                  # 16 namepool names (the # comment line is ignored)
gc status                         # demo/chat.chat  scaled (min=0, max=8)
for round in 1 2 3 4; do
  gc session new demo/chat.chat --no-attach &
  gc session new demo/chat.chat --no-attach &
  wait
done
gc session list --template demo/chat.chat
git -C <rig> worktree list
```

Observed, 4 rounds of two parallel sessions:

| Round | Session | `{{.AgentBase}}` (name argument) | Branch |
| --- | --- | --- | --- |
| 1 | `cs-wisp-v1g` | `chat.chat-adhoc-9a91efc6c2` | `gc-chat-chat.chat-adhoc-9a91efc6c2` |
| 1 | `cs-wisp-4rw` | `chat.chat-adhoc-672a9e60d9` | `gc-chat-chat.chat-adhoc-672a9e60d9` |
| 2 | `cs-wisp-lle` | `chat.chat-adhoc-43f3fdee58` | `gc-chat-chat.chat-adhoc-43f3fdee58` |
| 2 | `cs-wisp-ano` | `chat.chat-adhoc-8af5f6f83c` | `gc-chat-chat.chat-adhoc-8af5f6f83c` |
| 3 | `cs-wisp-89a` | `chat.chat-adhoc-1e18388553` | `gc-chat-chat.chat-adhoc-1e18388553` |
| 3 | `cs-wisp-8fr` | `chat.chat-adhoc-30ac515c96` | `gc-chat-chat.chat-adhoc-30ac515c96` |
| 4 | `cs-wisp-g3b` | `chat.chat-adhoc-7ecf5829b4` | `gc-chat-chat.chat-adhoc-7ecf5829b4` |
| 4 | `cs-wisp-6vk` | `chat.chat-adhoc-8c3e4d1f6e` | `gc-chat-chat.chat-adhoc-8c3e4d1f6e` |

- Every name was distinct. Each pre_start received
  `<rig> <rig>/worktrees/chat-<AgentBase> <AgentBase>` and exited 0, and
  each tmux pane ran in its own worktree.
- `git worktree list` showed one `chat-*` worktree per session, each on its
  own `gc-chat-*` branch, each with a clean `git status`. The rig checkout's
  `git status` did not list `worktrees/`.
- pre_start received `GC_SESSION_ID` (`cs-wisp-…`),
  `GC_SESSION_NAME` (`chat-adhoc-<hex>`), `GC_AGENT` and `GC_ALIAS`
  (`demo/chat.chat-adhoc-<hex>`), `GC_TEMPLATE` (`demo/chat.chat`),
  `GC_SESSION_ORIGIN=manual`, `GC_RIG_ROOT`, and `GC_DIR` (the work_dir).
- `gc session new demo/chat.chat --alias auth-fix` gave `{{.AgentBase}}`
  `chat.auth-fix`, worktree `<rig>/worktrees/chat-chat.auth-fix` and branch
  `gc-chat-chat.auth-fix`. While it was open, a second
  `--alias auth-fix` was refused by gc:
  `session alias already exists: "demo/chat.auth-fix" already belongs to cs-wisp-nzub`.
  After closing it, a new `--alias auth-fix` session resumed the same
  worktree and branch, with an uncommitted file still in place.
- The reconciler left the manual sessions alone: over more than 60
  reconcile ticks (about 25 minutes), each session ran pre_start exactly
  once and none was stopped, restarted or drained.
- `gc session close` left every worktree and branch in place, including a
  commit and an uncommitted file made in one of them.

The first attempt also found that gc writes `.gc/settings.json` into the
work_dir before pre_start, and that parallel starts could write the exclude
lines twice. Both are handled by the script and covered by its tests.

## Cleanup

Cleanup is manual. The pack never deletes a worktree or a branch. When a
session ends (close, idle timeout or drain), or an agent is opted out, its
worktree and branch stay in place, with any uncommitted work. A later
session with the same name resumes them.

Chat sessions opened without `--alias` never reuse a name, so each one leaves
a worktree and a branch behind.

List them, from the rig checkout:

```sh
git worktree list | grep -E '/worktrees/(agent|chat)-'
git branch --list 'gc-agent-*' 'gc-chat-*'
```

Discard one (this deletes its uncommitted work and its branch):

```sh
git worktree remove worktrees/agent-<name>     # or worktrees/chat-<name>; add --force to drop uncommitted changes
git branch -D gc-agent-<name>                  # or gc-chat-<name>
git worktree prune
```

gc 1.5.1 also creates an empty `worktrees/chat-<binding>.chat` directory for
the chat agent template itself. It is harmless and can be removed with
`rmdir`.

## Known limits

- **Workspace-trust dialog (`gcp-10u`, city bead `pi-7vb`).** Under gc
  1.5.1's built-in herdr provider, a Claude Code session in a path it has
  never seen can hang on the workspace-trust dialog. Every new worktree is
  such a path. This needs a gc upgrade. The pack deliberately does not edit
  `~/.claude.json` or any other user config to work around it.
- **Herdr shows a tab without gascity#1.** On a gc build without
  [gascity#1](https://github.com/UndermountainCC/gascity/pull/1), herdr shows
  the session as a tab, not as a worktree workspace.
- **`{{.ConfigDir}}` in a patch** resolves to the target agent's pack, not to
  this one. That is why opt-ins go through `gc <binding> ensure`.
- **Prompts still say "rig checkout".** An opted-in agent's prompt, written
  by the pack that defines it, may still say that it works in the rig
  checkout. The agent's actual work_dir is its worktree.
- **Task work_dir overrides are not retargeted.** When gc applies a task
  work_dir override to a session, the ensure command still receives the
  template's `{{.WorkDir}}`. Named agents normally have no such override.
- **Host pre_start only.** The worktree is created by a host pre_start, so
  the pack works only on runtimes that run pre_start on the host: tmux, and
  the herdr registrations that share the host filesystem.
- **gc's files.** A work_dir is accepted when it holds only the top-level
  entries gc 1.5.1 is known to write (listed in the script). If a later gc
  or a pack overlay writes something else there first, the script refuses
  the directory, and the entry has to be added to the script's list.

## E2E evidence

This was checked in a throwaway scratch city, never a live one, on
2026-11-19. The setup:

- the scratch city's own gc build (`gc version`: `dev-3bb5a3e10`), its own
  supervisor and `GC_HOME`, and its own herdr session (`herdr --session ws-e2e`);
- this pack imported from a local checkout twice: at city scope as
  `agent-worktree` with `agents_exclude = ["chat"]`, and at rig scope as
  `chat` in rig `testrig`;
- a toy rig `testrig`, cloned from a local bare origin;
- a stand-in for `project-lead`: a scratch pack imported at city scope as
  `e2e`. Its rig-scoped `lead` agent uses a shell provider, has
  `work_dir = "{{.RigRoot}}"` and no `pre_start`, and has a
  `[[named_session]]` with `mode = "always"`;
- the opt-in patch from [Opt in a named agent](#opt-in-a-named-agent), with
  `dir = "testrig"` and `name = "e2e.lead"`.

Below, `<run2>` is the scratch directory, and `gc` is run as
`bin/run gc --city <run2>/city`. `bin/run` gives a clean environment with
the scratch `GC_HOME` and `PATH`. Output is trimmed.

**Config resolution.** The patched agent, the same agent in another rig,
and an unpatched agent:

```text
$ gc config show
[[agent]]
name = "lead"
dir = "testrig"
work_dir = "{{.RigRoot}}/worktrees/agent-{{.AgentBase}}"
pre_start = ["gc agent-worktree ensure {{.RigRoot}} {{.WorkDir}} {{.AgentBase}}"]

[[agent]]
name = "lead"
dir = "trustrig"                 # same agent, other rig: unchanged
work_dir = "{{.RigRoot}}"

[[agent]]
name = "chat"
dir = "testrig"                  # unpatched agent: unchanged
work_dir = "{{.RigRoot}}/worktrees/chat-{{.AgentBase}}"
pre_start = ["{{.ConfigDir}}/assets/scripts/agent-worktree.sh chat {{.RigRoot}} {{.WorkDir}} {{.AgentBase}}"]
```

No `chat` agent came from the city import; the two `chat` agents in the
output are the rigs' own imports.

**pre_start order.** A config-only copy of the city added an agent with two
`pre_start` entries of its own, and imported `oversight-rig` with the same
patch for `oversight-rig.project-lead`:

```text
$ gc --city <probe-city> config show --json     # Agents[].Dir, .Name, .PreStart
testrig   lead          ["echo own-1 {{.WorkDir}}", "echo own-2",
                         "gc agent-worktree ensure {{.RigRoot}} {{.WorkDir}} {{.AgentBase}}"]
trustrig  lead          ["echo own-1 {{.WorkDir}}", "echo own-2"]
testrig   project-lead  work_dir "{{.RigRoot}}/worktrees/agent-{{.AgentBase}}",
                        ["gc agent-worktree ensure {{.RigRoot}} {{.WorkDir}} {{.AgentBase}}"]
trustrig  project-lead  work_dir "{{.RigRoot}}", no pre_start
```

gc's own entries come last. The supervisor log shows the resolved list for
a Claude `chat` session, with gc's skill materialization after the pack's
entry:

```text
PreStart: [<pack>/assets/scripts/agent-worktree.sh chat <run2>/testrig <run2>/testrig/worktrees/chat-chat.chat-adhoc-24eff23299 chat.chat-adhoc-24eff23299
           "${GC_BIN:-gc}" internal materialize-skills --best-effort --agent testrig/chat.chat --workdir <run2>/testrig/worktrees/chat-chat.chat-adhoc-24eff23299]
```

The shell-provider `lead` got no gc entries; its resolved list was the
ensure command alone.

**Adoption pass.** The named session was started before the patch, then
restarted after it:

```text
# before the patch
$ gc session list                 # gcg-5975  testrig/e2e.lead  active  WORKDIR <run2>/testrig
# add the patch, then:
$ gc reload                       # Config reloaded: 15 agents, 2 rigs
                                  # log: config-drift testrig--e2e__lead: drifted fields: PreStart
$ gc session list                 # gcg-5975  active  WORKDIR <run2>/testrig
$ readlink /proc/<pid>/cwd        # <run2>/testrig
$ git -C <run2>/testrig worktree list | grep agent-
<run2>/testrig/worktrees/agent-e2e.lead  9708b81 [gc-agent-e2e.lead]
$ gc session suspend gcg-5975; gc session wake gcg-5975   # process cwd still <run2>/testrig
$ gc session reset gcg-5975                               # process cwd still <run2>/testrig;
                                                          # bead work_dir still <run2>/testrig
$ gc session close gcg-5975       # Session gcg-5975 closed.
$ gc session list                 # gcg-6018  testrig/e2e.lead  active
                                  #   WORKDIR <run2>/testrig/worktrees/agent-e2e.lead
$ readlink /proc/<pid>/cwd        # <run2>/testrig/worktrees/agent-e2e.lead
```

So the old dir wins until the session is closed once. Closing it by alias
(`gc session close testrig/e2e.lead`) works the same way: the controller
recreated it as `gcg-6060`, in the same worktree.

**Start 1.** For a clean first creation, the session was suspended, its
worktree and branch were removed, and `origin` was pointed at an
unreachable address (`https://192.0.2.1/unreachable.git`). Then:

```text
$ gc session wake gcg-6018
$ gc session list                 # gcg-6018  active  WORKDIR <run2>/testrig/worktrees/agent-e2e.lead
$ readlink /proc/<pid>/cwd        # <run2>/testrig/worktrees/agent-e2e.lead
$ git -C <run2>/testrig worktree list
<run2>/testrig                           98b7f67 [main]
<run2>/testrig/worktrees/agent-e2e.lead  9708b81 [gc-agent-e2e.lead]
<run2>/testrig/worktrees/chat-chat.chat-adhoc-…  (four chat sessions from earlier runs)
$ git -C <run2>/testrig/worktrees/agent-e2e.lead status --short     # (empty)
$ git -C <run2>/testrig status --short
 M .beads/config.yaml
 M .gitignore
?? .claude/
```

- The observed `{{.AgentBase}}` is `e2e.lead`: the binding plus the local
  name, without the rig. The worktree is `worktrees/agent-e2e.lead`, on
  `gc-agent-e2e.lead`, cut from `origin/main` (`9708b81`), the last fetched
  ref.
- The rig's `git status` is identical before and after the run. The three
  entries were left by gc's rig and beads setup before this run began. The
  pack added none: `worktrees/` and `.gc/` are in the local exclude file.
- The start succeeded with the fetch timing out, inside gc's 10 s
  `setup_timeout`. Timed directly, with the same unreachable origin, for a
  probe name:

  ```text
  $ cd <run2>/testrig
  $ /usr/bin/time -f 'wall=%es rc=%x' bin/run env GC_CITY=<run2>/city \
      gc agent-worktree ensure <run2>/testrig <run2>/testrig/worktrees/agent-e2e.timing e2e.timing
  wall=5.09s rc=0
  ```

  That is the 5 s fetch bound plus gc's start-up. The supervisor's
  `start_call` for this start was 21.5 s, against 16.3 s to 16.9 s for this
  session's other starts with a reachable origin, before and after the
  patch. `origin` was restored afterwards, and the probe worktree and branch
  were removed.

**Start 2.** A marker file, then a restart that keeps the bead:

```text
$ touch <run2>/testrig/worktrees/agent-e2e.lead/wi7-marker
$ gc session kill gcg-6018        # Session gcg-6018 killed.  (the controller restarts it)
$ gc session list                 # gcg-6018  active  WORKDIR <run2>/testrig/worktrees/agent-e2e.lead
$ readlink /proc/<new pid>/cwd    # <run2>/testrig/worktrees/agent-e2e.lead
$ ls <run2>/testrig/worktrees/agent-e2e.lead/wi7-marker           # present
$ git -C <run2>/testrig worktree list | grep -c /worktrees/agent-  # 1
$ git -C <run2>/testrig/worktrees/agent-e2e.lead status --short   # ?? wi7-marker
```

The restarted session's work_dir was checked from its process, not assumed.

**herdr.** Observed through the scratch city's own herdr session only:

```text
$ herdr --session ws-e2e workspace list
w1  "testrig"   is_linked_worktree=false  <run2>/testrig
w6  "trustrig"  is_linked_worktree=false  <trustrig>
wF  "lead"      is_linked_worktree=true   <run2>/testrig/worktrees/agent-e2e.lead
$ herdr --session ws-e2e worktree list --workspace w1
gc-agent-e2e.lead  linked  open_workspace_id=wF  <run2>/testrig/worktrees/agent-e2e.lead
$ herdr --session ws-e2e tab list
w6:t2  "lead"     # the unpatched trustrig/e2e.lead: a tab in its rig's workspace
```

The opted-in session opened as a worktree workspace under the rig, and the
unpatched one in the other rig stayed a tab.

No gc-managed file was refused. Besides the checkout, the new worktree held
only `.gc/`, which the script already accepts.

## Testing

```sh
python3 -m pytest agent-worktree/tests -q
gc lint agent-worktree
```

The tests use temporary git repositories and no network. They run every
safety case in both modes, `chat` and `agent`, and cover the start-point
order, the fetch bound (with and without `timeout(1)`), idempotency, branch
resume, refusals, the nesting guard, gc's pre-written files, parallel starts,
the rig's clean status, and the `ensure` command's delegation. CI also loads
the pack in a scratch city through `tests/test_maintained_packs_live_gc.py`.
