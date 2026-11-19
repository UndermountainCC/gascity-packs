"""Behaviour tests for agent-worktree.sh (REQ-004 to REQ-007, REQ-011).

Every case runs against throwaway repositories under tmp_path: a bare origin
seeded with commit "one" and a rig clone of it. Nothing touches the network.
The safety cases run once per prefix mode, chat and agent.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest


PACK = Path(__file__).resolve().parents[1]
SCRIPT = PACK / "assets" / "scripts" / "agent-worktree.sh"
MODES = ("chat", "agent")

# Tools the script and git's local transport need when PATH is narrowed to a
# directory of symlinks that leaves out timeout(1).
FALLBACK_TOOLS = (
    "sh",
    "git",
    "sleep",
    "find",
    "head",
    "tail",
    "grep",
    "sed",
    "mkdir",
    "rmdir",
    "mv",
    "rm",
    "mktemp",
    "dirname",
    "basename",
    "cat",
    "env",
)


@pytest.fixture
def git_env(tmp_path: Path) -> dict[str, str]:
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
        # No detached auto-maintenance: it can write .git/objects/maintenance.lock
        # after a commit returns and break the before/after snapshots.
        GIT_CONFIG_COUNT="2",
        GIT_CONFIG_KEY_0="maintenance.auto",
        GIT_CONFIG_VALUE_0="false",
        GIT_CONFIG_KEY_1="gc.auto",
        GIT_CONFIG_VALUE_1="0",
    )
    env.pop("AGENT_WORKTREE_FETCH_TIMEOUT", None)
    return env


class Repos:
    def __init__(self, tmp_path: Path, env: dict[str, str]) -> None:
        self.tmp = tmp_path
        self.env = env
        self.origin = tmp_path / "origin.git"
        self.seed = tmp_path / "seed"
        self.rig = tmp_path / "rig"

    def git(self, *args: str | Path, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", *map(str, args)],
            cwd=cwd,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def commit(self, repo: Path, message: str) -> None:
        (repo / f"{message}.txt").write_text(message + "\n", encoding="utf-8")
        self.git("-C", repo, "add", f"{message}.txt")
        self.git("-C", repo, "commit", "-q", "-m", message)

    def advance_origin(self, message: str) -> None:
        self.commit(self.seed, message)
        self.git("-C", self.seed, "push", "-q", "origin", "main")

    def target(self, name: str, prefix: str) -> Path:
        return self.rig / "worktrees" / f"{prefix}-{name}"

    def run(
        self,
        name: str,
        prefix: str,
        target: Path | None = None,
        extra_env: dict[str, str] | None = None,
        rig: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [str(SCRIPT), prefix, str(rig or self.rig), str(target or self.target(name, prefix)), name],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def branch_exists(self, branch: str) -> bool:
        result = subprocess.run(
            ["git", "-C", str(self.rig), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            env=self.env,
        )
        return result.returncode == 0

    def log(self, repo: Path) -> list[str]:
        return self.git("-C", repo, "log", "--format=%s").splitlines()

    def branches(self, pattern: str) -> str:
        return self.git("-C", self.rig, "branch", "--list", pattern)

    def exclude_file(self) -> Path:
        common = self.git("-C", self.rig, "rev-parse", "--path-format=absolute", "--git-common-dir")
        return Path(common) / "info" / "exclude"


@pytest.fixture(params=MODES)
def mode(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def repos(tmp_path: Path, git_env: dict[str, str]) -> Repos:
    r = Repos(tmp_path, git_env)
    r.git("init", "-q", "--bare", "-b", "main", r.origin)
    r.git("clone", "-q", r.origin, r.seed)
    r.git("-C", r.seed, "symbolic-ref", "HEAD", "refs/heads/main")
    r.commit(r.seed, "one")
    r.git("-C", r.seed, "push", "-q", "origin", "main")
    r.git("clone", "-q", r.origin, r.rig)
    return r


def assert_created(repos: Repos, name: str, prefix: str) -> Path:
    wt = repos.target(name, prefix)
    assert repos.git("-C", wt, "branch", "--show-current") == f"gc-{prefix}-{name}"
    assert repos.git("-C", wt, "status", "--porcelain") == ""
    return wt


def assert_one_error_line(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode != 0
    lines = result.stderr.splitlines()
    assert len(lines) == 1, result.stderr
    assert lines[0].startswith("agent-worktree: ")
    return lines[0]


def assert_refused(result: subprocess.CompletedProcess[str], path: Path) -> None:
    assert str(path) in assert_one_error_line(result)


def snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def make_hanging_origin(repos: Repos) -> None:
    wrapper = repos.tmp / "slow-upload-pack"
    wrapper.write_text('#!/bin/sh\nsleep 30\nexec git-upload-pack "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    repos.git("-C", repos.rig, "config", "remote.origin.uploadpack", wrapper)


def bin_without_timeout(repos: Repos) -> str:
    bin_dir = repos.tmp / "bin-no-timeout"
    bin_dir.mkdir()
    for tool in FALLBACK_TOOLS:
        found = shutil.which(tool)
        assert found, tool
        (bin_dir / tool).symlink_to(found)
    assert shutil.which("timeout", path=str(bin_dir)) is None
    return str(bin_dir)


def test_creates_from_advanced_remote_tip(repos: Repos, mode: str):
    repos.advance_origin("two")

    result = repos.run("alder", mode)

    assert result.returncode == 0, result.stderr
    wt = assert_created(repos, "alder", mode)
    assert repos.log(wt) == ["two", "one"]


def test_new_branch_does_not_track_the_default_branch(repos: Repos, mode: str):
    assert repos.run("alder", mode).returncode == 0
    upstream = subprocess.run(
        ["git", "-C", str(repos.target("alder", mode)), "rev-parse", "--abbrev-ref", "@{upstream}"],
        env=repos.env,
        capture_output=True,
    )
    assert upstream.returncode != 0


def test_falls_back_to_origin_main_without_origin_head(repos: Repos, mode: str):
    repos.git("-C", repos.rig, "remote", "set-head", "origin", "-d")
    repos.advance_origin("two")

    result = repos.run("alder", mode)

    assert result.returncode == 0, result.stderr
    wt = assert_created(repos, "alder", mode)
    assert repos.log(wt) == ["two", "one"]


def test_no_origin_cuts_from_local_head(repos: Repos, mode: str):
    repos.git("-C", repos.rig, "remote", "remove", "origin")
    repos.commit(repos.rig, "local")

    result = repos.run("alder", mode)

    assert result.returncode == 0, result.stderr
    wt = assert_created(repos, "alder", mode)
    assert repos.log(wt) == ["local", "one"]


@pytest.mark.parametrize("narrow_path", [False, True], ids=["timeout", "watchdog"])
def test_unreachable_fetch_is_bounded(repos: Repos, mode: str, narrow_path: bool):
    repos.advance_origin("two")
    make_hanging_origin(repos)
    extra = {"AGENT_WORKTREE_FETCH_TIMEOUT": "1"}
    if narrow_path:
        extra["PATH"] = bin_without_timeout(repos)

    started = time.monotonic()
    result = repos.run("alder", mode, extra_env=extra)
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert elapsed < 10, elapsed
    wt = assert_created(repos, "alder", mode)
    assert repos.log(wt) == ["one"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "5"), ("1", "1"), ("8", "8"), ("99", "8"), ("0", "1"), ("07", "7"), ("soon", "5")],
)
def test_default_fetch_bound_is_within_budget(repos: Repos, mode: str, value: str | None, expected: str):
    # A fake timeout(1) records its arguments and reports a timeout, so the
    # script must fall back to the cached origin ref.
    bin_dir = repos.tmp / "fake-bin"
    bin_dir.mkdir()
    record = repos.tmp / "timeout-args"
    fake = bin_dir / "timeout"
    fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{record}"\nexit 124\n', encoding="utf-8")
    fake.chmod(0o755)
    extra = {"PATH": f"{bin_dir}{os.pathsep}{repos.env['PATH']}"}
    if value is not None:
        extra["AGENT_WORKTREE_FETCH_TIMEOUT"] = value

    result = repos.run("alder", mode, extra_env=extra)

    assert result.returncode == 0, result.stderr
    args = record.read_text(encoding="utf-8").splitlines()
    assert args[:4] == ["-k", "1", expected, "git"]
    assert 1 <= int(args[2]) <= 8
    assert_created(repos, "alder", mode)


def test_rejects_workdir_name_mismatch(repos: Repos, mode: str):
    target = repos.target("alder", mode)

    result = repos.run("birch", mode, target=target)

    assert_refused(result, target)
    assert not target.exists()
    assert not repos.branch_exists(f"gc-{mode}-alder")
    assert not repos.branch_exists(f"gc-{mode}-birch")


@pytest.mark.parametrize("name", ["chat-adhoc-0a1b2c3d4e", "pack.chat-adhoc-0a1b2c3d4e"])
def test_gc_shaped_names(repos: Repos, mode: str, name: str):
    result = repos.run(name, mode)

    assert result.returncode == 0, result.stderr
    assert repos.target(name, mode).name == f"{mode}-{name}"
    assert_created(repos, name, mode)


def test_refuses_partially_created_worktree(repos: Repos, mode: str):
    assert repos.run("alder", mode).returncode == 0
    wt = repos.target("alder", mode)
    admin = Path(repos.git("-C", wt, "rev-parse", "--absolute-git-dir"))
    (admin / "locked").write_text("initializing\n", encoding="utf-8")
    before = snapshot(wt)
    head = repos.git("-C", wt, "rev-parse", "HEAD")

    result = repos.run("alder", mode)

    assert_refused(result, wt)
    assert snapshot(wt) == before
    assert repos.git("-C", wt, "rev-parse", "HEAD") == head


def test_rerun_is_idempotent_and_keeps_work(repos: Repos, mode: str):
    assert repos.run("alder", mode).returncode == 0
    wt = repos.target("alder", mode)
    (wt / "scratch.txt").write_text("keep me\n", encoding="utf-8")
    repos.advance_origin("three")
    cached = repos.git("-C", repos.rig, "rev-parse", "refs/remotes/origin/main")

    result = repos.run("alder", mode)

    assert result.returncode == 0, result.stderr
    assert (wt / "scratch.txt").read_text(encoding="utf-8") == "keep me\n"
    assert "three" not in repos.log(wt)
    # No fetch happened on the fast path.
    assert repos.git("-C", repos.rig, "rev-parse", "refs/remotes/origin/main") == cached


def test_resumes_existing_branch(repos: Repos, mode: str):
    assert repos.run("alder", mode).returncode == 0
    wt = repos.target("alder", mode)
    repos.commit(wt, "mine")
    repos.git("-C", repos.rig, "worktree", "remove", "--force", wt)
    repos.advance_origin("three")

    result = repos.run("alder", mode)

    assert result.returncode == 0, result.stderr
    assert_created(repos, "alder", mode)
    assert repos.log(wt) == ["mine", "one"]


def test_resumes_after_directory_deleted(repos: Repos, mode: str):
    assert repos.run("alder", mode).returncode == 0
    wt = repos.target("alder", mode)
    repos.commit(wt, "mine")
    shutil.rmtree(wt)

    result = repos.run("alder", mode)

    assert result.returncode == 0, result.stderr
    assert_created(repos, "alder", mode)
    assert repos.log(wt) == ["mine", "one"]


def test_two_names_two_worktrees(repos: Repos, mode: str):
    assert repos.run("alder", mode).returncode == 0
    assert repos.run("birch", mode).returncode == 0

    alder = assert_created(repos, "alder", mode)
    birch = assert_created(repos, "birch", mode)
    assert alder != birch
    listing = repos.git("-C", repos.rig, "worktree", "list", "--porcelain")
    assert f"worktree {alder}" in listing
    assert f"worktree {birch}" in listing
    assert f"branch refs/heads/gc-{mode}-alder" in listing
    assert f"branch refs/heads/gc-{mode}-birch" in listing


def test_refuses_nonempty_plain_dir(repos: Repos, mode: str):
    wt = repos.target("alder", mode)
    wt.mkdir(parents=True)
    (wt / "foreign.txt").write_text("not ours\n", encoding="utf-8")

    result = repos.run("alder", mode)

    assert_refused(result, wt)
    assert snapshot(wt) == {"foreign.txt": b"not ours\n"}
    assert not repos.branch_exists(f"gc-{mode}-alder")


def test_refuses_other_repo_checkout(repos: Repos, mode: str):
    wt = repos.target("alder", mode)
    wt.mkdir(parents=True)
    repos.git("init", "-q", "-b", "main", wt)
    repos.commit(wt, "other")
    before = snapshot(wt)

    result = repos.run("alder", mode)

    assert_refused(result, wt)
    assert snapshot(wt) == before
    assert repos.log(wt) == ["other"]
    assert not repos.branch_exists(f"gc-{mode}-alder")


def test_refuses_other_clone_of_same_origin(repos: Repos, mode: str):
    wt = repos.target("alder", mode)
    repos.git("clone", "-q", repos.origin, wt)
    before = snapshot(wt)

    result = repos.run("alder", mode)

    assert_refused(result, wt)
    assert snapshot(wt) == before
    assert repos.git("-C", wt, "branch", "--show-current") == "main"


def test_empty_existing_dir_is_used(repos: Repos, mode: str):
    wt = repos.target("alder", mode)
    wt.mkdir(parents=True)

    result = repos.run("alder", mode)

    assert result.returncode == 0, result.stderr
    assert (assert_created(repos, "alder", mode) / "one.txt").is_file()


def test_rig_status_clean_and_exclude_once(repos: Repos, mode: str):
    common = Path(repos.git("-C", repos.rig, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    exclude = common / "info" / "exclude"
    exclude.write_text("*.swp", encoding="utf-8")

    for name in ("alder", "birch", "alder"):
        result = repos.run(name, mode)
        assert result.returncode == 0, result.stderr

    assert repos.git("-C", repos.rig, "status", "--porcelain") == ""
    lines = exclude.read_text(encoding="utf-8").splitlines()
    assert lines == ["*.swp", "/worktrees/", "/.gc/"]
    assert not (repos.rig / ".gitignore").exists()
    subprocess.run(["git", "-C", str(repos.rig), "diff", "--exit-code"], env=repos.env, check=True)


# gc 1.5.1 writes its managed settings, and each provider's hook and overlay
# files, into the work_dir before pre_start runs, so a new work_dir is
# never empty in a real city.
GC_SETTINGS = {".gc/settings.json": b'{"hooks": {}}\n'}


def write_files(directory: Path, files: dict[str, bytes]) -> None:
    for relative, content in files.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def stage_leftovers(repos: Repos) -> list[Path]:
    return sorted((repos.rig / "worktrees").glob(".agent-worktree-stage.*"))


def test_accepts_gc_settings_written_before_pre_start(repos: Repos, mode: str):
    wt = repos.target("alder", mode)
    write_files(wt, GC_SETTINGS)

    result = repos.run("alder", mode)

    assert result.returncode == 0, result.stderr
    assert_created(repos, "alder", mode)
    assert (wt / "one.txt").is_file()
    assert snapshot(wt / ".gc") == {"settings.json": GC_SETTINGS[".gc/settings.json"]}
    assert stage_leftovers(repos) == []


def test_accepts_provider_overlay_files(repos: Repos, mode: str):
    wt = repos.target("alder", mode)
    files = {
        **GC_SETTINGS,
        ".codex/hooks.json": b"{}\n",
        ".claude/skills/demo/SKILL.md": b"demo\n",
        "AGENTS.md": b"kiro\n",
    }
    write_files(wt, files)

    result = repos.run("alder", mode)

    assert result.returncode == 0, result.stderr
    assert repos.git("-C", wt, "branch", "--show-current") == f"gc-{mode}-alder"
    after = snapshot(wt)
    for relative, content in files.items():
        assert after[relative] == content
    assert stage_leftovers(repos) == []


def test_refuses_gc_files_mixed_with_foreign_content(repos: Repos, mode: str):
    wt = repos.target("alder", mode)
    write_files(wt, {**GC_SETTINGS, "foreign.txt": b"not ours\n"})
    before = snapshot(wt)

    result = repos.run("alder", mode)

    assert_refused(result, wt)
    assert snapshot(wt) == before
    assert not repos.branch_exists(f"gc-{mode}-alder")
    assert stage_leftovers(repos) == []


def test_checkout_files_win_over_gc_copies(repos: Repos, mode: str):
    write_files(repos.seed, {".claude/settings.json": b"tracked\n"})
    repos.git("-C", repos.seed, "add", ".claude/settings.json")
    repos.git("-C", repos.seed, "commit", "-q", "-m", "claude settings")
    repos.git("-C", repos.seed, "push", "-q", "origin", "main")
    wt = repos.target("alder", mode)
    write_files(wt, {".claude/settings.json": b"gc copy\n", ".claude/extra.json": b"extra\n"})

    result = repos.run("alder", mode)

    assert result.returncode == 0, result.stderr
    assert (wt / ".claude" / "settings.json").read_bytes() == b"tracked\n"
    assert (wt / ".claude" / "extra.json").read_bytes() == b"extra\n"
    assert repos.git("-C", wt, "status", "--porcelain") == "?? .claude/extra.json"
    assert stage_leftovers(repos) == []


def test_gc_files_are_restored_when_the_worktree_cannot_be_added(repos: Repos, mode: str):
    # The branch is already checked out elsewhere, so worktree add fails.
    repos.git("-C", repos.rig, "worktree", "add", "-q", "-b", f"gc-{mode}-alder", repos.tmp / "elsewhere")
    wt = repos.target("alder", mode)
    write_files(wt, GC_SETTINGS)
    before = snapshot(wt)

    result = repos.run("alder", mode)

    assert_refused(result, wt)
    assert snapshot(wt) == before
    assert stage_leftovers(repos) == []


def test_parallel_runs_write_each_exclude_line_once(repos: Repos, mode: str):
    # gc starts sessions in parallel; their exclude updates must not race
    # into duplicate lines.
    names = [f"chat-adhoc-{index:010x}" for index in range(8)]
    runs = [
        subprocess.Popen(
            [str(SCRIPT), mode, str(repos.rig), str(repos.target(name, mode)), name],
            env=repos.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        for name in names
    ]
    for run in runs:
        _, stderr = run.communicate(timeout=60)
        assert run.returncode == 0, stderr

    common = Path(repos.git("-C", repos.rig, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    lines = (common / "info" / "exclude").read_text(encoding="utf-8").splitlines()
    assert lines.count("/worktrees/") == 1
    assert lines.count("/.gc/") == 1
    assert not list((common / "info").glob("exclude.agent-worktree.*"))


@pytest.mark.parametrize("name", ["a..b", "a b", "x~1"])
def test_rejects_unsafe_name(repos: Repos, mode: str, name: str):
    target = repos.target(name, mode)

    result = repos.run(name, mode)

    assert_refused(result, target)
    assert not target.exists()
    assert repos.git("-C", repos.rig, "branch", "--list", f"gc-{mode}-*") == ""


def test_usage_error_is_one_prefixed_line(repos: Repos, mode: str):
    result = subprocess.run(
        [str(SCRIPT), mode, str(repos.rig)],
        env=repos.env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 1
    assert result.stderr.startswith("agent-worktree: usage: ")
    assert len(result.stderr.splitlines()) == 1


def test_named_agent_mode_create_reuse_resume(repos: Repos):
    name = "oversight-rig.project-lead"
    repos.advance_origin("two")

    result = repos.run(name, "agent")

    assert result.returncode == 0, result.stderr
    wt = assert_created(repos, name, "agent")
    assert wt == repos.rig / "worktrees" / "agent-oversight-rig.project-lead"
    assert repos.log(wt) == ["two", "one"]

    # A restart reuses the stable worktree untouched.
    (wt / "notes.txt").write_text("keep me\n", encoding="utf-8")
    repos.commit(wt, "mine")
    repos.advance_origin("three")

    result = repos.run(name, "agent")

    assert result.returncode == 0, result.stderr
    assert (wt / "notes.txt").read_text(encoding="utf-8") == "keep me\n"
    assert "three" not in repos.log(wt)

    # With the worktree gone, the branch is resumed where it was.
    repos.git("-C", repos.rig, "worktree", "remove", "--force", wt)

    result = repos.run(name, "agent")

    assert result.returncode == 0, result.stderr
    assert repos.git("-C", wt, "branch", "--show-current") == "gc-agent-oversight-rig.project-lead"
    assert repos.log(wt) == ["mine", "two", "one"]


@pytest.mark.parametrize("name", ["oversight-rig.project-lead", "pack.sub.worker-1"])
def test_dotted_binding_names_are_accepted(repos: Repos, mode: str, name: str):
    result = repos.run(name, mode)

    assert result.returncode == 0, result.stderr
    assert_created(repos, name, mode)


@pytest.mark.parametrize(
    ("prefix", "dirname", "name"),
    [("agent", "agent-foo", "bar"), ("agent", "chat-x", "x"), ("chat", "agent-x", "x")],
    ids=["other-name", "chat-dir-in-agent-mode", "agent-dir-in-chat-mode"],
)
def test_rejects_workdir_name_mismatch_per_mode(repos: Repos, prefix: str, dirname: str, name: str):
    target = repos.rig / "worktrees" / dirname

    result = repos.run(name, prefix, target=target)

    assert_refused(result, target)
    assert not target.exists()
    assert repos.branches("gc-*") == ""


@pytest.mark.parametrize("prefix", ["", "Chat", "a-b", "../x", "1chat"])
def test_rejects_invalid_prefix(repos: Repos, prefix: str):
    target = repos.rig / "worktrees" / "chat-alder"
    exclude = repos.exclude_file()
    before = exclude.read_bytes()

    result = repos.run("alder", prefix, target=target)

    assert_one_error_line(result)
    assert not (repos.rig / "worktrees").exists()
    assert repos.branches("gc-*") == ""
    assert exclude.read_bytes() == before


def test_refuses_linked_worktree_rig_root(repos: Repos, mode: str):
    linked = repos.rig / "worktrees" / "gcp-abc"
    repos.git("-C", repos.rig, "worktree", "add", "-q", "--detach", linked)
    exclude = repos.exclude_file()
    before_exclude = exclude.read_bytes()
    before_list = repos.git("-C", repos.rig, "worktree", "list", "--porcelain")
    target = linked / "worktrees" / f"{mode}-alder"

    result = repos.run("alder", mode, target=target, rig=linked)

    assert "nest" in assert_one_error_line(result)
    assert not target.exists()
    assert repos.git("-C", repos.rig, "worktree", "list", "--porcelain") == before_list
    assert repos.branches(f"gc-{mode}-*") == ""
    assert exclude.read_bytes() == before_exclude
