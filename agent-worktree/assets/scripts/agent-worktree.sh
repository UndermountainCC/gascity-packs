#!/bin/sh
# agent-worktree.sh — pre_start that gives an agent its own git worktree.
#
# Usage: agent-worktree.sh <prefix> <rig-root> <target-dir> <name>
#
# <prefix> is one lowercase word (^[a-z][a-z0-9]*$). The pack uses two:
#
#   - chat: the pack's chat agent. Each session gets its own worktree,
#     <rig-root>/worktrees/chat-<name> on branch gc-chat-<name>.
#   - agent: a named agent opted in by a rig patch through
#     `gc <binding> ensure`. It keeps one stable worktree across restarts,
#     <rig-root>/worktrees/agent-<name> on branch gc-agent-<name>.
#
# Ensures <target-dir>, which must be named <prefix>-<name>, is a git
# worktree of <rig-root> on branch gc-<prefix>-<name>:
#
#   - An existing worktree of this rig is reused as is.
#   - A leftover gc-<prefix>-<name> branch is resumed as is.
#   - A new branch is cut from origin/HEAD, else origin/main, else the rig's
#     local HEAD, after a fetch bounded by AGENT_WORKTREE_FETCH_TIMEOUT
#     seconds (default 5, clamped to 1..8).
#   - gc writes provider hook and overlay files (such as .gc/settings.json)
#     into the work_dir before pre_start runs. A directory holding only
#     those is accepted: they are set aside while the worktree is added,
#     then put back wherever the checkout has no file of its own.
#   - Any other non-empty directory that is not a worktree of this rig is
#     refused.
#   - A rig root that is itself a linked worktree is refused before anything
#     is written: the new worktree would nest inside it.
#   - /worktrees/ and /.gc/ are kept in the rig's local info/exclude, so the
#     rig checkout's status and each agent worktree's status stay clean.
#
# Deliberately absent, compared with gastown's worktree-setup.sh:
#   - no --sync: an existing worktree or branch is never pulled, rebased,
#     checked out or reset, so a session never loses work on restart;
#   - no .beads/redirect;
#   - no stage-and-merge of arbitrary stray files: only gc's own files are
#     set aside, so a foreign directory is still refused untouched.
# The script never deletes a worktree or a branch; see the pack README.
#
# Every failure prints one stderr line prefixed "agent-worktree:" and exits 1.

set -eu

die() {
    printf 'agent-worktree: %s\n' "$*" >&2
    exit 1
}

if [ "$#" -ne 4 ] || [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ]; then
    die "usage: agent-worktree.sh <prefix> <rig-root> <target-dir> <name>"
fi

PREFIX=$1
RIG_ROOT=$2
WT=$3
NAME=$4

# One lowercase word, so <prefix>-<name> always splits unambiguously. The
# letters are spelled out because a range like [a-z] follows the locale.
case $PREFIX in
    [!abcdefghijklmnopqrstuvwxyz]* | *[!abcdefghijklmnopqrstuvwxyz0123456789]*)
        die "invalid prefix $PREFIX: it must match ^[a-z][a-z0-9]*\$"
        ;;
esac

BRANCH="gc-$PREFIX-$NAME"

# Never let a credential prompt block session start.
GIT_TERMINAL_PROMPT=0
export GIT_TERMINAL_PROMPT

if ! git check-ref-format --branch "$BRANCH" >/dev/null 2>&1; then
    die "invalid name $NAME for $WT: $BRANCH is not a valid branch name"
fi

if [ "$(git -C "$RIG_ROOT" rev-parse --is-inside-work-tree 2>/dev/null)" != "true" ]; then
    die "$RIG_ROOT is not a git work tree"
fi

# A rig root inside a linked worktree has a git dir apart from the common
# one. A worktree added under it would nest inside that worktree, so refuse
# before anything, even info/exclude, is written.
RIG_GIT_DIR=$(git -C "$RIG_ROOT" rev-parse --path-format=absolute --git-dir 2>/dev/null) &&
    RIG_GIT_DIR_REAL=$(CDPATH= cd -- "$RIG_GIT_DIR" 2>/dev/null && pwd -P) ||
    die "cannot resolve the git dir of $RIG_ROOT"
RIG_COMMON_DIR=$(git -C "$RIG_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) &&
    RIG_COMMON_REAL=$(CDPATH= cd -- "$RIG_COMMON_DIR" 2>/dev/null && pwd -P) ||
    die "cannot resolve the git common dir of $RIG_ROOT"
[ "$RIG_GIT_DIR_REAL" = "$RIG_COMMON_REAL" ] ||
    die "refusing $RIG_ROOT: it is a linked worktree, so $WT would nest inside it (see the agent-worktree README, Do not opt in)"

# The work_dir and the name are rendered separately by gc. If they ever
# disagree, every session could derive the same branch, so fail loudly.
[ "$(basename "$WT")" = "$PREFIX-$NAME" ] || die "$WT does not match name $NAME (prefix $PREFIX)"

FETCH_TIMEOUT=${AGENT_WORKTREE_FETCH_TIMEOUT:-5}
case $FETCH_TIMEOUT in
    '' | *[!0-9]*) FETCH_TIMEOUT=5 ;;
esac
FETCH_TIMEOUT=$(printf '%s' "$FETCH_TIMEOUT" | sed 's/^0*//')
case $FETCH_TIMEOUT in
    '') FETCH_TIMEOUT=1 ;;
    ???*) FETCH_TIMEOUT=8 ;;
esac
if [ "$FETCH_TIMEOUT" -lt 1 ]; then
    FETCH_TIMEOUT=1
elif [ "$FETCH_TIMEOUT" -gt 8 ]; then
    FETCH_TIMEOUT=8
fi

git_common_dir() {
    COMMON_REPO=$1
    COMMON_DIR=$(git -C "$COMMON_REPO" rev-parse \
        --path-format=absolute --git-common-dir 2>/dev/null) || return 1
    (CDPATH= cd -- "$COMMON_DIR" 2>/dev/null && pwd -P)
}

# Ported from gastown's worktree-setup.sh: the target must be the top level
# of a checkout that shares the rig's common git dir.
validate_existing_worktree() {
    WT_REAL=$(CDPATH= cd -- "$WT" 2>/dev/null && pwd -P) || return 1
    WT_TOP=$(git -C "$WT_REAL" rev-parse --show-toplevel 2>/dev/null) ||
        return 1
    WT_TOP=$(CDPATH= cd -- "$WT_TOP" 2>/dev/null && pwd -P) || return 1
    [ "$WT_TOP" = "$WT_REAL" ] || return 1
    WT_COMMON=$(git_common_dir "$WT_REAL") || return 1
    RIG_COMMON=$(git_common_dir "$RIG_ROOT") || return 1
    [ "$WT_COMMON" = "$RIG_COMMON" ]
}

# bounded <secs> <cmd...>: run cmd for at most secs seconds (plus one second
# of grace before SIGKILL). Without timeout(1), a sleep watchdog kills the
# direct child. That fallback cannot reach the child's descendants, so the
# caller must give the bounded command its own stdio; otherwise an orphaned
# transport process would keep the caller's stdout and stderr open.
bounded() {
    BOUND_SECS=$1
    shift
    if command -v timeout >/dev/null 2>&1; then
        timeout -k 1 "$BOUND_SECS" "$@"
        return
    fi
    "$@" &
    BOUND_PID=$!
    (
        trap 'kill "$WATCHDOG_SLEEP" 2>/dev/null; exit 0' TERM
        sleep "$BOUND_SECS" &
        WATCHDOG_SLEEP=$!
        wait "$WATCHDOG_SLEEP"
        kill -TERM "$BOUND_PID" 2>/dev/null || exit 0
        sleep 1 &
        WATCHDOG_SLEEP=$!
        wait "$WATCHDOG_SLEEP"
        kill -KILL "$BOUND_PID" 2>/dev/null || true
    ) </dev/null >/dev/null 2>&1 &
    WATCHDOG_PID=$!
    BOUND_STATUS=0
    wait "$BOUND_PID" || BOUND_STATUS=$?
    kill -TERM "$WATCHDOG_PID" 2>/dev/null || true
    wait "$WATCHDOG_PID" 2>/dev/null || true
    return "$BOUND_STATUS"
}

# Keep the rig checkout's status clean. info/exclude is local and untracked;
# a tracked .gitignore is never touched.
RIG_COMMON=$(git_common_dir "$RIG_ROOT") ||
    die "cannot resolve the git common dir of $RIG_ROOT"
EXCLUDE="$RIG_COMMON/info/exclude"
# /.gc/ is gc's runtime state, including the settings it writes into every
# agent worktree; excluding it keeps a session from committing it.
exclude_has_all() {
    grep -qxF /worktrees/ "$1" 2>/dev/null && grep -qxF /.gc/ "$1" 2>/dev/null
}
# Sessions start in parallel, so appending in place would race and duplicate
# lines. Instead, complete a private copy and rename it over the file: the
# copy is checked and extended from one snapshot, and every writer adds the
# same lines, so concurrent writers converge on one copy of each.
if ! exclude_has_all "$EXCLUDE"; then
    EXCLUDE_TMP="$EXCLUDE.agent-worktree.$$"
    {
        mkdir -p "$RIG_COMMON/info" &&
            if [ -s "$EXCLUDE" ]; then cat "$EXCLUDE" >"$EXCLUDE_TMP"; else : >"$EXCLUDE_TMP"; fi &&
            if [ -s "$EXCLUDE_TMP" ] && [ -n "$(tail -c 1 "$EXCLUDE_TMP")" ]; then
                printf '\n' >>"$EXCLUDE_TMP"
            fi &&
            for PATTERN in /worktrees/ /.gc/; do
                grep -qxF "$PATTERN" "$EXCLUDE_TMP" || printf '%s\n' "$PATTERN" >>"$EXCLUDE_TMP"
            done &&
            mv -f "$EXCLUDE_TMP" "$EXCLUDE"
    } 2>/dev/null || {
        rm -f "$EXCLUDE_TMP" 2>/dev/null
        die "cannot update $EXCLUDE"
    }
fi

# An existing worktree is reused untouched: no fetch, checkout or reset.
if [ -e "$WT/.git" ]; then
    validate_existing_worktree || die "refusing $WT: not a worktree of $RIG_ROOT"
    WT_GIT_DIR=$(git -C "$WT" rev-parse --absolute-git-dir 2>/dev/null) ||
        die "cannot resolve the git dir of $WT"
    # git worktree add holds this lock until checkout completes, so a lock
    # saying "initializing" means an earlier add was killed half way. Refuse
    # rather than repair it by checkout or reset.
    if [ -f "$WT_GIT_DIR/locked" ] && grep -q initializing "$WT_GIT_DIR/locked"; then
        die "$WT is a partially created worktree; remove it by hand (see the agent-worktree README, Cleanup)"
    fi
    exit 0
fi

if [ -e "$WT" ] && [ ! -d "$WT" ]; then
    die "refusing $WT: exists and is not a directory"
fi
# Top-level entries gc 1.5.1 may write into a work_dir before pre_start: its
# managed settings (.gc), and the hook and overlay files of each provider
# (.claude, core's per-provider overlays, and kiro's AGENTS.md).
is_gc_managed() {
    case $1 in
        .gc | .claude | .agents | .codex | .cursor | .gemini | .github | .kimi | \
            .kiro | .mimocode | .omp | .opencode | .pi | AGENTS.md)
            return 0
            ;;
    esac
    return 1
}

# merge_entry <src> <dst>: move src to dst, descending into directories, and
# never replacing anything already at dst. What cannot be placed stays in
# src, so the checkout's own files always win over gc's copies.
merge_entry() (
    if [ -d "$1" ] && [ ! -L "$1" ]; then
        mkdir -p "$2" 2>/dev/null || exit 0
        for MERGE_ENTRY in "$1"/.[!.]* "$1"/..?* "$1"/*; do
            [ -e "$MERGE_ENTRY" ] || [ -L "$MERGE_ENTRY" ] || continue
            merge_entry "$MERGE_ENTRY" "$2/$(basename "$MERGE_ENTRY")"
        done
        rmdir "$1" 2>/dev/null || true
        exit 0
    fi
    [ -e "$2" ] || [ -L "$2" ] || mv "$1" "$2" 2>/dev/null || true
)

# Put the set-aside files back into the work_dir. Runs on success, and from
# the traps when the worktree could not be added, so gc's files are never
# lost. Leftovers are only copies the checkout already has.
STAGE=""
restore_stage() {
    [ -n "$STAGE" ] || return 0
    mkdir -p "$WT" 2>/dev/null || true
    for STAGE_ENTRY in "$STAGE"/.[!.]* "$STAGE"/..?* "$STAGE"/*; do
        [ -e "$STAGE_ENTRY" ] || [ -L "$STAGE_ENTRY" ] || continue
        merge_entry "$STAGE_ENTRY" "$WT/$(basename "$STAGE_ENTRY")"
    done
    rm -rf "$STAGE" 2>/dev/null || true
    STAGE=""
}

if [ -d "$WT" ]; then
    for ENTRY in "$WT"/.[!.]* "$WT"/..?* "$WT"/*; do
        [ -e "$ENTRY" ] || [ -L "$ENTRY" ] || continue
        is_gc_managed "$(basename "$ENTRY")" ||
            die "refusing $WT: exists, is not empty, and is not a git worktree"
    done
    if [ -n "$(find "$WT" -mindepth 1 -maxdepth 1 | head -n 1)" ]; then
        STAGE=$(mktemp -d "$(dirname "$WT")/.agent-worktree-stage.XXXXXX" 2>/dev/null) ||
            die "cannot set aside gc's files in $WT"
        trap 'restore_stage' EXIT
        trap 'restore_stage; exit 1' HUP INT TERM
        for ENTRY in "$WT"/.[!.]* "$WT"/..?* "$WT"/*; do
            [ -e "$ENTRY" ] || [ -L "$ENTRY" ] || continue
            mv "$ENTRY" "$STAGE/" 2>/dev/null || die "cannot set aside gc's files in $WT"
        done
    fi
    rmdir "$WT" 2>/dev/null || die "cannot remove empty directory $WT"
fi

# Drop stale admin entries, e.g. for a worktree whose directory was deleted,
# so its branch can be checked out again.
git -C "$RIG_ROOT" worktree prune >/dev/null 2>&1 || true
mkdir -p "$(dirname "$WT")" 2>/dev/null ||
    die "cannot create the parent directory of $WT"

START=""
ORIGIN_HEAD=$(git -C "$RIG_ROOT" symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null || true)
if [ -n "$ORIGIN_HEAD" ] &&
    git -C "$RIG_ROOT" show-ref --verify --quiet "$ORIGIN_HEAD"; then
    START=$ORIGIN_HEAD
elif git -C "$RIG_ROOT" show-ref --verify --quiet refs/remotes/origin/main; then
    START=refs/remotes/origin/main
fi

GIT_LFS_SKIP_SMUDGE=1
export GIT_LFS_SKIP_SMUDGE

if git -C "$RIG_ROOT" show-ref --verify --quiet "refs/heads/$BRANCH"; then
    # Resume the leftover branch exactly where it was.
    ADD_STATUS=0
    ADD_ERR=$(git -C "$RIG_ROOT" worktree add "$WT" "$BRANCH" 2>&1 >/dev/null) ||
        ADD_STATUS=$?
else
    # Only a new branch fetches. A failed or timed-out fetch is tolerated:
    # the branch is cut from the cached remote ref instead.
    if [ -n "$START" ]; then
        bounded "$FETCH_TIMEOUT" git -C "$RIG_ROOT" fetch --quiet origin \
            "${START#refs/remotes/origin/}" </dev/null >/dev/null 2>&1 || true
    fi
    # --no-track: the branch never silently follows the default branch.
    ADD_STATUS=0
    ADD_ERR=$(git -C "$RIG_ROOT" worktree add --no-track -b "$BRANCH" "$WT" \
        ${START:+"$START"} 2>&1 >/dev/null) || ADD_STATUS=$?
fi

if [ "$ADD_STATUS" -ne 0 ]; then
    ADD_REASON=$(printf '%s\n' "$ADD_ERR" | sed '/^[[:space:]]*$/d' | tail -n 1)
    die "failed to create $WT from $RIG_ROOT (branch $BRANCH)${ADD_REASON:+: $ADD_REASON}"
fi

restore_stage
trap - EXIT HUP INT TERM
