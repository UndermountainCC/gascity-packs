"""Tests for the `gc <binding> ensure` pack command (REQ-003, REQ-004).

commands/ensure/run.sh is run directly with GC_PACK_DIR set, as gc runs it,
against throwaway repositories under tmp_path. Nothing touches the network.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


PACK = Path(__file__).resolve().parents[1]
RUN = PACK / "commands" / "ensure" / "run.sh"
USAGE = "agent-worktree: usage: gc <binding> ensure <rig-root> <target-dir> <name> (no gc flags before the binding)"


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    home = tmp_path / "home"
    home.mkdir()
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="Agent Worktree Test",
        GIT_AUTHOR_EMAIL="agent-worktree@example.invalid",
        GIT_COMMITTER_NAME="Agent Worktree Test",
        GIT_COMMITTER_EMAIL="agent-worktree@example.invalid",
        GIT_CONFIG_COUNT="2",
        GIT_CONFIG_KEY_0="maintenance.auto",
        GIT_CONFIG_VALUE_0="false",
        GIT_CONFIG_KEY_1="gc.auto",
        GIT_CONFIG_VALUE_1="0",
        GC_PACK_DIR=str(PACK),
    )
    env.pop("AGENT_WORKTREE_FETCH_TIMEOUT", None)
    return env


def git(env: dict[str, str], *args: str | Path) -> str:
    result = subprocess.run(
        ["git", *map(str, args)], env=env, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def rig(tmp_path: Path, env: dict[str, str]) -> Path:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    rig = tmp_path / "rig"
    git(env, "init", "-q", "--bare", "-b", "main", origin)
    git(env, "clone", "-q", origin, seed)
    git(env, "-C", seed, "symbolic-ref", "HEAD", "refs/heads/main")
    (seed / "one.txt").write_text("one\n", encoding="utf-8")
    git(env, "-C", seed, "add", "one.txt")
    git(env, "-C", seed, "commit", "-q", "-m", "one")
    git(env, "-C", seed, "push", "-q", "origin", "main")
    git(env, "clone", "-q", origin, rig)
    return rig


def run(env: dict[str, str], *args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(RUN), *map(str, args)], env=env, capture_output=True, text=True, timeout=60
    )


def test_creates_the_agent_worktree_through_the_real_script(env: dict[str, str], rig: Path):
    name = "oversight-rig.project-lead"
    wt = rig / "worktrees" / f"agent-{name}"

    result = run(env, rig, wt, name)

    assert result.returncode == 0, result.stderr
    assert git(env, "-C", wt, "branch", "--show-current") == f"gc-agent-{name}"
    assert git(env, "-C", wt, "status", "--porcelain") == ""
    assert (wt / "one.txt").is_file()

    rerun = run(env, rig, wt, name)

    assert rerun.returncode == 0, rerun.stderr


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
def test_missing_pack_dir_is_one_line(env: dict[str, str], rig: Path, value: str | None):
    if value is None:
        env.pop("GC_PACK_DIR")
    else:
        env["GC_PACK_DIR"] = value
    wt = rig / "worktrees" / "agent-x"

    result = run(env, rig, wt, "x")

    assert result.returncode == 1
    assert result.stderr.splitlines() == [
        "agent-worktree: GC_PACK_DIR is not set; run as gc <binding> ensure"
    ]
    assert not (rig / "worktrees").exists()


@pytest.mark.parametrize(
    "args",
    [
        ("{rig}", "{wt}"),
        ("{rig}", "{wt}", "x", "extra"),
        ("--city", "/x", "{rig}", "{wt}", "x"),
        ("--city", "/x", "{rig}", "{wt}"),
    ],
    ids=["two", "four", "city-flag-five", "city-flag-four"],
)
def test_wrong_argument_count_prints_usage_and_creates_nothing(
    env: dict[str, str], rig: Path, args: tuple[str, ...]
):
    wt = rig / "worktrees" / "agent-x"
    argv = [arg.format(rig=rig, wt=wt) for arg in args]

    result = run(env, *argv)

    assert result.returncode == 1
    assert result.stderr.splitlines() == [USAGE]
    assert not (rig / "worktrees").exists()
    assert git(env, "-C", rig, "branch", "--list", "gc-*") == ""


def test_delegates_with_the_agent_prefix(env: dict[str, str], tmp_path: Path):
    stub_pack = tmp_path / "stub-pack"
    script = stub_pack / "assets" / "scripts" / "agent-worktree.sh"
    script.parent.mkdir(parents=True)
    # Mode 644 on purpose: run.sh must not rely on the exec bit.
    script.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n', encoding="utf-8")
    script.chmod(0o644)
    env["GC_PACK_DIR"] = str(stub_pack)

    result = run(env, "/r", "/r/worktrees/agent-x", "x")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["agent", "/r", "/r/worktrees/agent-x", "x"]
