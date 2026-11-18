#!/bin/sh
# worktree-chat.sh — pre_start for the chat-worktree pack's chat agent.
#
# Usage: worktree-chat.sh <rig-root> <target-dir> <name>
#
# Ensures <target-dir> is a git worktree of <rig-root> on branch
# gc-chat-<name>, so every chat session works in its own checkout:
#
#   - An existing worktree of this rig is reused as is.
#   - A leftover gc-chat-<name> branch is resumed as is.
#   - A new branch is cut from origin/HEAD, else origin/main, else the rig's
#     local HEAD, after a fetch bounded by CHAT_WORKTREE_FETCH_TIMEOUT
#     seconds (default 5, clamped to 1..8).
#   - A non-empty directory that is not a worktree of this rig is refused.
#   - /worktrees/ is kept in the rig's local info/exclude, so the rig
#     checkout's status stays clean.
#
# Deliberately absent, compared with gastown's worktree-setup.sh:
#   - no --sync: an existing worktree or branch is never pulled, rebased,
#     checked out or reset, so a session never loses work on restart;
#   - no .beads/redirect;
#   - no stage-and-merge of stray files into a foreign directory.
# The script never deletes a worktree or a branch; see the pack README.
#
# Every failure prints one stderr line prefixed "worktree-chat:" and exits 1.

set -eu

die() {
    printf 'worktree-chat: %s\n' "$*" >&2
    exit 1
}

if [ "$#" -ne 3 ] || [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ]; then
    die "usage: worktree-chat.sh <rig-root> <target-dir> <name>"
fi

RIG_ROOT=$1
WT=$2
NAME=$3
BRANCH="gc-chat-$NAME"

# Never let a credential prompt block session start.
GIT_TERMINAL_PROMPT=0
export GIT_TERMINAL_PROMPT

if ! git check-ref-format --branch "$BRANCH" >/dev/null 2>&1; then
    die "invalid name $NAME for $WT: $BRANCH is not a valid branch name"
fi

if [ "$(git -C "$RIG_ROOT" rev-parse --is-inside-work-tree 2>/dev/null)" != "true" ]; then
    die "$RIG_ROOT is not a git work tree"
fi

# The work_dir and the name are rendered separately by gc. If they ever
# disagree, every session could derive the same branch, so fail loudly.
if [ "$(basename "$WT")" != "chat-$NAME" ]; then
    die "$WT does not match name $NAME"
fi

FETCH_TIMEOUT=${CHAT_WORKTREE_FETCH_TIMEOUT:-5}
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
if ! grep -qxF '/worktrees/' "$EXCLUDE" 2>/dev/null; then
    {
        mkdir -p "$RIG_COMMON/info" &&
            if [ -s "$EXCLUDE" ] && [ -n "$(tail -c 1 "$EXCLUDE")" ]; then
                printf '\n' >>"$EXCLUDE"
            fi &&
            printf '/worktrees/\n' >>"$EXCLUDE"
    } 2>/dev/null || die "cannot update $EXCLUDE"
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
        die "$WT is a partially created worktree; remove it by hand (see the chat-worktree README, Cleanup)"
    fi
    exit 0
fi

if [ -e "$WT" ] && [ ! -d "$WT" ]; then
    die "refusing $WT: exists and is not a directory"
fi
if [ -d "$WT" ]; then
    if [ -n "$(find "$WT" -mindepth 1 -maxdepth 1 | head -n 1)" ]; then
        die "refusing $WT: exists, is not empty, and is not a git worktree"
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
