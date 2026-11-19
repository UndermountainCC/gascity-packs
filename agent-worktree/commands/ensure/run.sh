#!/bin/sh
# gc <binding> ensure <rig-root> <target-dir> <name>
# pre_start entry point for agents opted in by a rig patch; see ../../README.md.
# The prefix is fixed to `agent`: worktrees/agent-<name> on gc-agent-<name>.
set -eu
[ -n "${GC_PACK_DIR:-}" ] || { echo "agent-worktree: GC_PACK_DIR is not set; run as gc <binding> ensure" >&2; exit 1; }
if [ "$#" -ne 3 ]; then
    echo "agent-worktree: usage: gc <binding> ensure <rig-root> <target-dir> <name> (no gc flags before the binding)" >&2
    exit 1
fi
# `exec sh`, not a direct exec: do not rely on the pack cache keeping the exec bit.
exec sh "$GC_PACK_DIR/assets/scripts/agent-worktree.sh" agent "$@"
