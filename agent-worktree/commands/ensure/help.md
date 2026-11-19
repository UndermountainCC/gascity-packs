Ensure an opted-in agent's git worktree. Meant for `pre_start`.

Usage:
  gc <binding> ensure <rig-root> <target-dir> <name>

Creates or reuses `<rig-root>/worktrees/agent-<name>` on branch
`gc-agent-<name>`. `<target-dir>` must be that path. Pass exactly three
arguments, with no gc flags before the binding.

See ../../README.md for how to opt an agent in.
