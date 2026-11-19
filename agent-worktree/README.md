# chat-worktree

Gives every chat session in a rig its own git worktree and branch.

## What it does

`gc session new` normally runs a rig agent in the rig's shared checkout. When
several conversations and the rig's own agents all edit that one working
tree, they clobber each other's edits and leave surprise dirty state, and a
branch can switch underneath a live session.

This pack adds one rig-scoped agent, `chat`. Each session of it starts in its
own worktree, `<rig>/worktrees/chat-<name>`, on its own branch,
`gc-chat-<name>`, cut from the remote default branch. Sessions cannot collide
with each other or with the rig checkout. The model is gastown's polecat,
without `--sync` and without `.beads/redirect`. No gc change is needed.

## Install

Import the pack at rig scope in `city.toml`:

```toml
[[rigs]]
name = "your-project"

[rigs.imports.chat]
source = "https://github.com/gastownhall/gascity-packs.git//chat-worktree"
```

A local checkout works the same way: `source = "/path/to/gascity-packs/chat-worktree"`.

The agent's qualified name is `<rig>/<binding>.chat`. With the binding `chat`
shown above it is `<rig>/chat.chat`, which is the form verified below. The
pack pins no `provider`: the agent inherits the city's or rig's default.

`min_active_sessions = 0`, so nothing starts until someone opens a session.
At most 8 sessions run at once (`max_active_sessions`), and an idle session
stops after 4 hours (`idle_timeout`).

## Use

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

## How it works

`agents/chat/agent.toml` sets:

```toml
work_dir = "{{.RigRoot}}/worktrees/chat-{{.AgentBase}}"
pre_start = ["{{.ConfigDir}}/assets/scripts/worktree-chat.sh {{.RigRoot}} {{.WorkDir}} {{.AgentBase}}"]
```

Before the session starts, gc runs `assets/scripts/worktree-chat.sh` on the
host. It makes sure the work_dir is a worktree of the rig on branch
`gc-chat-<name>`:

1. It checks its arguments. The branch name must pass
   `git check-ref-format --branch`, and the work_dir must end in
   `chat-<name>`. A mismatch fails loudly instead of letting two sessions
   derive the same branch.
2. It keeps `/worktrees/` and `/.gc/` in the rig's local exclude (see
   [Local exclude](#local-exclude)).
3. An existing worktree of this rig is reused as it is (see
   [Idempotency and refusals](#idempotency-and-refusals)).
4. gc writes its own files into the work_dir before pre_start runs: its
   managed settings in `.gc/settings.json`, and each provider's hook or
   overlay files (`.claude/`, `.codex/`, `.gemini/`, `.github/`, `.cursor/`,
   `.opencode/`, `.pi/`, `.kiro/` with `AGENTS.md`, and the like). A
   directory holding only those is accepted. The script sets them aside, adds
   the worktree, and puts them back wherever the checkout has no file of its
   own. If the worktree cannot be added, they are put back as they were.
5. A leftover branch `gc-chat-<name>` is checked out again exactly where it
   was, without a reset.
6. Otherwise a new branch is cut, with `--no-track`, from the first of:
   1. `refs/remotes/origin/HEAD`, if set;
   2. `refs/remotes/origin/main`, if present;
   3. the rig's current `HEAD` (a repo with no `origin`).

   Before cutting, it fetches that remote branch. The fetch is bounded by
   `CHAT_WORKTREE_FETCH_TIMEOUT` seconds: default 5, clamped to 1..8, which
   leaves room inside gc's 10 s setup timeout. On a timeout or a failure it
   cuts from the last fetched ref. Only a new branch fetches.

The script sets `GIT_LFS_SKIP_SMUDGE=1` and `GIT_TERMINAL_PROMPT=0`, so it
never blocks on LFS downloads or a credential prompt. It never pulls,
rebases, resets or syncs, and it writes no `.beads/redirect`.

## Idempotency and refusals

- **Existing worktree.** If the work_dir is already a worktree of this rig
  (same common git dir, and the work_dir is its top level), the script exits
  0 at once. It does no fetch, checkout or reset, so uncommitted and
  untracked work survives a restart or reattach.
- **Partially created worktree.** If a previous `git worktree add` was
  killed halfway, its lock still reads `initializing`. The script refuses it
  rather than repair it; remove it by hand (see [Cleanup](#cleanup)).
- **Foreign directory.** A non-empty work_dir that is not a worktree of this
  rig is refused, untouched, unless it holds only gc's own files (step 4
  above). That includes a separate repository and a different clone of the
  same origin.
- **Empty directory.** An empty work_dir is used as the creation target.

Every refusal is one `stderr` line starting with `worktree-chat:` and naming
the path, and the session does not start. gc 1.5.1 then drops the failed
session; open a new one after fixing the cause.

## Local exclude

The script adds two lines to the repository's local exclude file,
`$(git rev-parse --git-common-dir)/info/exclude`:

- `/worktrees/`, so `git status` in the rig checkout does not list the chat
  worktrees;
- `/.gc/`, so the settings gc writes into each worktree never show up as
  untracked there and are not committed by accident.

The file is local and untracked, shared by the rig checkout and all its
worktrees. Each line is added once, also when sessions start in parallel. A
tracked `.gitignore` is never touched. Provider directories such as
`.claude/` are not excluded, because a repository may track them; if gc
writes one into a worktree, it shows as untracked there.

## Cleanup

The pack never deletes a worktree or a branch. When a session ends (close,
idle timeout or drain), `worktrees/chat-<name>` and `gc-chat-<name>` stay in
place, with any uncommitted work. A later session with the same name resumes
them: the worktree as it is, or the branch if only the worktree was removed.

Sessions opened without `--alias` never reuse a name, so each one leaves a
worktree and a branch behind. Clean them up by hand.

List them, from the rig checkout:

```sh
git worktree list | grep '/worktrees/chat-'
git branch --list 'gc-chat-*'
```

Discard one (this deletes its uncommitted work and its branch):

```sh
git worktree remove worktrees/chat-<name>     # add --force to drop uncommitted changes
git branch -D gc-chat-<name>
git worktree prune
```

gc 1.5.1 also creates an empty `worktrees/chat-<binding>.chat` directory for
the agent template itself. It is harmless and can be removed with `rmdir`.

## Name uniqueness

Two sessions open at once always get two different worktrees under gc 1.5.1.
`gc session new` does not draw names from the namepool. It gives each session
without `--alias` a random `chat-adhoc-<10 hex>` name, and it refuses a
second session with an alias that an open session already holds. The script
also refuses a work_dir that does not end in `chat-<name>`, so the two values
cannot drift apart silently.

This was checked in a throwaway scratch city, never a live one: gc 1.5.1,
tmux runtime, a shell provider (`sh -c 'exec sleep 86400'`) as the city
default, and the pack imported at rig scope with binding `chat` on a toy rig
cloned from a local bare origin. A temporary wrapper around the script
logged the arguments and `GC_*` environment each pre_start received.

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

| Round | Session | `{{.AgentBase}}` (argv 3) | Branch |
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

## Known limits

- **Workspace-trust dialog (`gcp-10u`, city bead `pi-7vb`).** Under gc
  1.5.1's built-in herdr provider, a Claude Code session in a path it has
  never seen can hang on the workspace-trust dialog. Every new chat worktree
  is such a path. This needs a gc upgrade. The pack deliberately does not
  edit `~/.claude.json` or any other user config to work around it.
- **Herdr shows a tab.** In herdr the session appears as a tab, not as a
  worktree workspace, until the paired gc change `gcs-mfi` lands.
- **Host pre_start only.** The worktree is created by a host pre_start, so
  the pack works only on runtimes that run pre_start on the host: tmux, and
  the herdr registrations that share the host filesystem.
- **No migration.** Sessions already running in the rig checkout stay there.
  Only sessions opened after the import get a worktree.
- **gc's files.** A work_dir is accepted when it holds only the top-level
  entries gc 1.5.1 is known to write (listed in the script). If a later gc
  or a pack overlay writes something else there first, the script refuses
  the directory, and the entry has to be added to the script's list.

## Testing

```sh
python3 -m pytest chat-worktree/tests -q
gc lint chat-worktree
```

The tests use temporary git repositories and no network. They cover the
start-point order, the fetch bound (with and without `timeout(1)`),
idempotency, branch resume, refusals, gc's pre-written files, parallel
starts, and the rig's clean status. CI also loads the pack in a scratch city
through `tests/test_maintained_packs_live_gc.py`.
