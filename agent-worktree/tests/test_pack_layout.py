"""Static layout checks for the agent-worktree pack (REQ-001 to REQ-004, REQ-009, REQ-010)."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tomllib

import pytest


PACK = Path(__file__).resolve().parents[1]
AGENT_DIR = PACK / "agents" / "chat"
SCRIPT = PACK / "assets" / "scripts" / "agent-worktree.sh"
ENSURE_DIR = PACK / "commands" / "ensure"
ENSURE = ENSURE_DIR / "run.sh"

EXPECTED_AGENT = {
    "scope": "rig",
    "wake_mode": "fresh",
    "work_dir": "{{.RigRoot}}/worktrees/chat-{{.AgentBase}}",
    "pre_start": [
        "{{.ConfigDir}}/assets/scripts/agent-worktree.sh chat"
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

# The scripts never sync an existing worktree; these words may appear only in
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
    assert manifest["pack"]["name"] == "agent-worktree"
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


@pytest.mark.parametrize("script", [SCRIPT, ENSURE], ids=lambda path: path.name)
def test_prestart_script_is_executable(script: Path):
    assert script.is_file()
    assert os.access(script, os.X_OK)
    tracked = subprocess.run(
        ["git", "ls-files", "-s", "--", script.name],
        cwd=script.parent,
        capture_output=True,
        text=True,
    )
    if tracked.returncode == 0 and tracked.stdout.strip():
        assert tracked.stdout.split()[0] == "100755"


def test_ensure_command_files_exist():
    for name in ("run.sh", "help.md", "command.toml"):
        assert (ENSURE_DIR / name).is_file(), name
    assert load_toml(ENSURE_DIR / "command.toml") == {
        "description": "Ensure an opted-in agent's git worktree (pre_start)"
    }


def test_no_shipped_file_deletes_worktrees_or_branches():
    assert {SCRIPT, ENSURE} <= set(shipped_files())
    for path in shipped_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for command in DESTRUCTIVE_COMMANDS:
            assert command not in text, f"{path.relative_to(PACK)} contains {command!r}"


@pytest.mark.parametrize("script", [SCRIPT, ENSURE], ids=lambda path: path.name)
def test_prestart_script_mentions_sync_words_only_in_comments(script: Path):
    for number, line in enumerate(script.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        lowered = line.lower()
        for word in SYNC_WORDS:
            assert word not in lowered, f"line {number}: {line.strip()}"


def test_prestart_script_skips_lfs_smudge_before_worktree_add():
    text = SCRIPT.read_text(encoding="utf-8")
    exported = text.index("GIT_LFS_SKIP_SMUDGE=1\nexport GIT_LFS_SKIP_SMUDGE\n")
    assert exported < text.index('"$RIG_ROOT" worktree add')


README = PACK / "README.md"

# README sections, in order (REQ-010, AC-10).
README_SECTIONS = (
    "Purpose",
    "How it works",
    "Install",
    "Opt in a named agent",
    "pre_start ordering",
    "Naming",
    "Restart and reuse",
    "Refusals and the nesting guard",
    "Do not opt in",
    "Chat example",
    "Cleanup",
    "Known limits",
    "E2E evidence",
)

# Sections whose snippets are the documented opt-in (AC-03).
OPT_IN_SECTIONS = ("Opt in a named agent", "pre_start ordering")

PRIMARY_SNIPPET = (
    'work_dir = "{{.RigRoot}}/worktrees/agent-{{.AgentBase}}"\n'
    'pre_start_append = ["gc agent-worktree ensure {{.RigRoot}} {{.WorkDir}} {{.AgentBase}}"]'
)

# A path token that starts at the root or the home directory. A path after a
# template var, such as {{.RigRoot}}/worktrees, is relative to it.
ABSOLUTE_PATH = re.compile(r"""(?:^|[\s"'=\[(,])(?:/|~/)[A-Za-z0-9_.-]""", re.MULTILINE)

FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)

# Built by concatenation so this file does not match its own scan (RC-2).
OLD_PACK_NAME = "chat" + "-worktree"
FORBIDDEN_OLD_NAMES = ("worktree" + "-chat", "CHAT" + "_WORKTREE")


def readme_sections() -> dict[str, str]:
    """Map each `## ` heading of the README to its body, in order."""
    text = README.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    for chunk in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        heading, _, body = chunk.partition("\n")
        sections[heading.strip()] = body
    return sections


def test_readme_has_required_sections_in_order():
    headings = list(readme_sections())
    positions = [headings.index(section) for section in README_SECTIONS]
    assert positions == sorted(positions), headings


def test_readme_opt_in_snippet_has_no_configdir_or_absolute_path():
    sections = readme_sections()
    snippets = [
        snippet
        for section in OPT_IN_SECTIONS
        for snippet in FENCE.findall(sections[section])
    ]
    assert any(PRIMARY_SNIPPET in snippet for snippet in snippets)
    for snippet in snippets:
        assert "{{.ConfigDir}}" not in snippet, snippet
        assert "{{.Agent}}" not in snippet, snippet
        assert not ABSOLUTE_PATH.search(snippet), snippet


def test_readme_do_not_opt_in_names_the_nesting_agents():
    section = readme_sections()["Do not opt in"]
    for name in ("gc.run-operator", "gc.implementation-worker", "$(pwd)/worktrees/<id>"):
        assert name in section, name
    assert "limit of the guard" in section


def test_readme_known_limits_name_trust_dialog_and_herdr_change():
    section = readme_sections()["Known limits"]
    for name in ("gcp-10u", "pi-7vb", "gascity#1"):
        assert name in section, name


def git_grep(needle: str) -> list[str]:
    repo = PACK.parent
    inside = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        pytest.skip("not a git checkout")
    found = subprocess.run(
        ["git", "grep", "-n", "-F", "-e", needle],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert found.returncode in (0, 1), found.stderr
    return found.stdout.splitlines()


def test_old_pack_name_appears_only_once_in_the_readme():
    matches = git_grep(OLD_PACK_NAME)
    assert len(matches) <= 1, matches
    for match in matches:
        assert match.startswith("agent-worktree/README.md:"), match


@pytest.mark.parametrize("needle", FORBIDDEN_OLD_NAMES)
def test_old_script_and_env_names_are_gone(needle: str):
    assert git_grep(needle) == []
