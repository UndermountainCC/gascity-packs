"""Static layout checks for the chat-worktree pack (REQ-001 to REQ-003, REQ-009, REQ-010)."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tomllib


PACK = Path(__file__).resolve().parents[1]
AGENT_DIR = PACK / "agents" / "chat"
SCRIPT = PACK / "assets" / "scripts" / "worktree-chat.sh"

EXPECTED_AGENT = {
    "scope": "rig",
    "wake_mode": "fresh",
    "work_dir": "{{.RigRoot}}/worktrees/chat-{{.AgentBase}}",
    "pre_start": [
        "{{.ConfigDir}}/assets/scripts/worktree-chat.sh"
        " {{.RigRoot}} {{.WorkDir}} {{.AgentBase}}"
    ],
    "min_active_sessions": 0,
    "max_active_sessions": 8,
}

# Keys the agent may carry beyond EXPECTED_AGENT. `provider` is deliberately
# absent: the agent inherits the city or rig default (OQ-2).
OPTIONAL_AGENT_KEYS = {"description", "idle_timeout"}

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")

# Built by concatenation so this file does not match its own scan.
FORBIDDEN_NEEDLES = ("re" + "matu", "ses" + "ión", "char" + "la")

# Cleanup is manual and documented; no shipped file may run it (AC-09).
DESTRUCTIVE_COMMANDS = ("git worktree " + "remove", "git branch " + "-D")

# The script never syncs an existing worktree; these words may appear only in
# comments that explain their absence (AC-03).
SYNC_WORDS = ("pull", "rebase", "--sync", "redirect")


def load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_agent() -> dict:
    return load_toml(AGENT_DIR / "agent.toml")


def namepool_names() -> list[str]:
    lines = (AGENT_DIR / "namepool.txt").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def test_pack_manifest_is_standalone():
    manifest = load_toml(PACK / "pack.toml")
    assert manifest["pack"]["name"] == "chat-worktree"
    assert manifest["pack"]["schema"] == 2
    assert "imports" not in manifest
    assert "service" not in manifest
    assert "named_session" not in manifest


def test_agent_has_exactly_the_required_values():
    agent = load_agent()
    for key, value in EXPECTED_AGENT.items():
        assert agent.get(key) == value, key
    assert "provider" not in agent
    assert set(agent) - set(EXPECTED_AGENT) <= OPTIONAL_AGENT_KEYS


def test_namepool_names_are_unique_branch_safe_and_sufficient():
    names = namepool_names()
    assert len(names) == len(set(names))
    for name in names:
        assert NAME_PATTERN.match(name), name
    assert len(names) >= load_agent()["max_active_sessions"]


def test_namepool_comment_explains_pool_slot_scope():
    first = (AGENT_DIR / "namepool.txt").read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("#")
    assert "chat-adhoc-" in first
    assert "--alias" in first


def test_prompt_names_worktree_branch_and_prohibitions():
    prompt = (AGENT_DIR / "prompt.template.md").read_text(encoding="utf-8")
    assert "{{ .WorkDir }}" in prompt
    assert "gc-chat-" in prompt
    assert "{{ .RigRoot }}" in prompt
    lowered = prompt.lower()
    assert "never merge" in lowered
    assert "never push to the default branch" in lowered


def test_pack_has_no_project_specific_text():
    for path in sorted(PACK.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for needle in FORBIDDEN_NEEDLES:
            assert needle not in text, f"{path.relative_to(PACK)} contains {needle!r}"


def test_no_top_level_scripts_directory():
    assert not (PACK / "scripts").exists()


def shipped_files() -> list[Path]:
    return [
        path
        for path in sorted(PACK.rglob("*"))
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.relative_to(PACK).parts[0] != "tests"
        and path.name != "README.md"
    ]


def test_prestart_script_is_executable():
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK)
    tracked = subprocess.run(
        ["git", "ls-files", "-s", "--", SCRIPT.name],
        cwd=SCRIPT.parent,
        capture_output=True,
        text=True,
    )
    if tracked.returncode == 0 and tracked.stdout.strip():
        assert tracked.stdout.split()[0] == "100755"


def test_no_shipped_file_deletes_worktrees_or_branches():
    for path in shipped_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for command in DESTRUCTIVE_COMMANDS:
            assert command not in text, f"{path.relative_to(PACK)} contains {command!r}"


def test_prestart_script_mentions_sync_words_only_in_comments():
    for number, line in enumerate(SCRIPT.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        lowered = line.lower()
        for word in SYNC_WORDS:
            assert word not in lowered, f"line {number}: {line.strip()}"


def test_prestart_script_skips_lfs_smudge_before_worktree_add():
    text = SCRIPT.read_text(encoding="utf-8")
    exported = text.index("GIT_LFS_SKIP_SMUDGE=1\nexport GIT_LFS_SKIP_SMUDGE\n")
    assert exported < text.index('"$RIG_ROOT" worktree add')
