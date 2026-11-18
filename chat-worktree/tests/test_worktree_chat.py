"""Behaviour tests for worktree-chat.sh (REQ-003 to REQ-008, REQ-012).

Every case runs against throwaway repositories under tmp_path: a bare origin
seeded with commit "one" and a rig clone of it. Nothing touches the network.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest


PACK = Path(__file__).resolve().parents[1]
SCRIPT = PACK / "assets" / "scripts" / "worktree-chat.sh"

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
        GIT_AUTHOR_NAME="Chat Worktree Test",
        GIT_AUTHOR_EMAIL="chat-worktree@example.invalid",
        GIT_COMMITTER_NAME="Chat Worktree Test",
        GIT_COMMITTER_EMAIL="chat-worktree@example.invalid",
    )
    env.pop("CHAT_WORKTREE_FETCH_TIMEOUT", None)
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

    def target(self, name: str) -> Path:
        return self.rig / "worktrees" / f"chat-{name}"

    def run(
        self,
        name: str,
        target: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [str(SCRIPT), str(self.rig), str(target or self.target(name)), name],
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


def assert_created(repos: Repos, name: str) -> Path:
    wt = repos.target(name)
    assert repos.git("-C", wt, "branch", "--show-current") == f"gc-chat-{name}"
    assert repos.git("-C", wt, "status", "--porcelain") == ""
    return wt


def assert_refused(result: subprocess.CompletedProcess[str], path: Path) -> None:
    assert result.returncode != 0
    lines = result.stderr.splitlines()
    assert len(lines) == 1, result.stderr
    assert lines[0].startswith("worktree-chat: ")
    assert str(path) in lines[0]


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


def test_creates_from_advanced_remote_tip(repos: Repos):
    repos.advance_origin("two")

    result = repos.run("alder")

    assert result.returncode == 0, result.stderr
    wt = assert_created(repos, "alder")
    assert repos.log(wt) == ["two", "one"]


def test_new_branch_does_not_track_the_default_branch(repos: Repos):
    assert repos.run("alder").returncode == 0
    upstream = subprocess.run(
        ["git", "-C", str(repos.target("alder")), "rev-parse", "--abbrev-ref", "@{upstream}"],
        env=repos.env,
        capture_output=True,
    )
    assert upstream.returncode != 0


def test_falls_back_to_origin_main_without_origin_head(repos: Repos):
    repos.git("-C", repos.rig, "remote", "set-head", "origin", "-d")
    repos.advance_origin("two")

    result = repos.run("alder")

    assert result.returncode == 0, result.stderr
    wt = assert_created(repos, "alder")
    assert repos.log(wt) == ["two", "one"]


def test_no_origin_cuts_from_local_head(repos: Repos):
    repos.git("-C", repos.rig, "remote", "remove", "origin")
    repos.commit(repos.rig, "local")

    result = repos.run("alder")

    assert result.returncode == 0, result.stderr
    wt = assert_created(repos, "alder")
    assert repos.log(wt) == ["local", "one"]


@pytest.mark.parametrize("narrow_path", [False, True], ids=["timeout", "watchdog"])
def test_unreachable_fetch_is_bounded(repos: Repos, narrow_path: bool):
    repos.advance_origin("two")
    make_hanging_origin(repos)
    extra = {"CHAT_WORKTREE_FETCH_TIMEOUT": "1"}
    if narrow_path:
        extra["PATH"] = bin_without_timeout(repos)

    started = time.monotonic()
    result = repos.run("alder", extra_env=extra)
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert elapsed < 10, elapsed
    wt = assert_created(repos, "alder")
    assert repos.log(wt) == ["one"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "5"), ("1", "1"), ("8", "8"), ("99", "8"), ("0", "1"), ("07", "7"), ("soon", "5")],
)
def test_default_fetch_bound_is_within_budget(repos: Repos, value: str | None, expected: str):
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
        extra["CHAT_WORKTREE_FETCH_TIMEOUT"] = value

    result = repos.run("alder", extra_env=extra)

    assert result.returncode == 0, result.stderr
    args = record.read_text(encoding="utf-8").splitlines()
    assert args[:4] == ["-k", "1", expected, "git"]
    assert 1 <= int(args[2]) <= 8
    assert_created(repos, "alder")


def test_rejects_workdir_name_mismatch(repos: Repos):
    target = repos.target("alder")

    result = repos.run("birch", target=target)

    assert_refused(result, target)
    assert not target.exists()
    assert not repos.branch_exists("gc-chat-alder")
    assert not repos.branch_exists("gc-chat-birch")


@pytest.mark.parametrize("name", ["chat-adhoc-0a1b2c3d4e", "pack.chat-adhoc-0a1b2c3d4e"])
def test_gc_shaped_names(repos: Repos, name: str):
    result = repos.run(name)

    assert result.returncode == 0, result.stderr
    assert repos.target(name).name == f"chat-{name}"
    assert_created(repos, name)


def test_refuses_partially_created_worktree(repos: Repos):
    assert repos.run("alder").returncode == 0
    wt = repos.target("alder")
    admin = Path(repos.git("-C", wt, "rev-parse", "--absolute-git-dir"))
    (admin / "locked").write_text("initializing\n", encoding="utf-8")
    before = snapshot(wt)
    head = repos.git("-C", wt, "rev-parse", "HEAD")

    result = repos.run("alder")

    assert_refused(result, wt)
    assert snapshot(wt) == before
    assert repos.git("-C", wt, "rev-parse", "HEAD") == head


def test_rerun_is_idempotent_and_keeps_work(repos: Repos):
    assert repos.run("alder").returncode == 0
    wt = repos.target("alder")
    (wt / "scratch.txt").write_text("keep me\n", encoding="utf-8")
    repos.advance_origin("three")
    cached = repos.git("-C", repos.rig, "rev-parse", "refs/remotes/origin/main")

    result = repos.run("alder")

    assert result.returncode == 0, result.stderr
    assert (wt / "scratch.txt").read_text(encoding="utf-8") == "keep me\n"
    assert "three" not in repos.log(wt)
    # No fetch happened on the fast path.
    assert repos.git("-C", repos.rig, "rev-parse", "refs/remotes/origin/main") == cached


def test_resumes_existing_branch(repos: Repos):
    assert repos.run("alder").returncode == 0
    wt = repos.target("alder")
    repos.commit(wt, "mine")
    repos.git("-C", repos.rig, "worktree", "remove", "--force", wt)
    repos.advance_origin("three")

    result = repos.run("alder")

    assert result.returncode == 0, result.stderr
    assert_created(repos, "alder")
    assert repos.log(wt) == ["mine", "one"]


def test_resumes_after_directory_deleted(repos: Repos):
    assert repos.run("alder").returncode == 0
    wt = repos.target("alder")
    repos.commit(wt, "mine")
    shutil.rmtree(wt)

    result = repos.run("alder")

    assert result.returncode == 0, result.stderr
    assert_created(repos, "alder")
    assert repos.log(wt) == ["mine", "one"]


def test_two_names_two_worktrees(repos: Repos):
    assert repos.run("alder").returncode == 0
    assert repos.run("birch").returncode == 0

    alder = assert_created(repos, "alder")
    birch = assert_created(repos, "birch")
    assert alder != birch
    listing = repos.git("-C", repos.rig, "worktree", "list", "--porcelain")
    assert f"worktree {alder}" in listing
    assert f"worktree {birch}" in listing
    assert "branch refs/heads/gc-chat-alder" in listing
    assert "branch refs/heads/gc-chat-birch" in listing


def test_refuses_nonempty_plain_dir(repos: Repos):
    wt = repos.target("alder")
    wt.mkdir(parents=True)
    (wt / "foreign.txt").write_text("not ours\n", encoding="utf-8")

    result = repos.run("alder")

    assert_refused(result, wt)
    assert snapshot(wt) == {"foreign.txt": b"not ours\n"}
    assert not repos.branch_exists("gc-chat-alder")


def test_refuses_other_repo_checkout(repos: Repos):
    wt = repos.target("alder")
    wt.mkdir(parents=True)
    repos.git("init", "-q", "-b", "main", wt)
    repos.commit(wt, "other")
    before = snapshot(wt)

    result = repos.run("alder")

    assert_refused(result, wt)
    assert snapshot(wt) == before
    assert repos.log(wt) == ["other"]
    assert not repos.branch_exists("gc-chat-alder")


def test_refuses_other_clone_of_same_origin(repos: Repos):
    wt = repos.target("alder")
    repos.git("clone", "-q", repos.origin, wt)
    before = snapshot(wt)

    result = repos.run("alder")

    assert_refused(result, wt)
    assert snapshot(wt) == before
    assert repos.git("-C", wt, "branch", "--show-current") == "main"


def test_empty_existing_dir_is_used(repos: Repos):
    wt = repos.target("alder")
    wt.mkdir(parents=True)

    result = repos.run("alder")

    assert result.returncode == 0, result.stderr
    assert (assert_created(repos, "alder") / "one.txt").is_file()


def test_rig_status_clean_and_exclude_once(repos: Repos):
    common = Path(repos.git("-C", repos.rig, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    exclude = common / "info" / "exclude"
    exclude.write_text("*.swp", encoding="utf-8")

    for name in ("alder", "birch", "alder"):
        result = repos.run(name)
        assert result.returncode == 0, result.stderr

    assert repos.git("-C", repos.rig, "status", "--porcelain") == ""
    lines = exclude.read_text(encoding="utf-8").splitlines()
    assert lines == ["*.swp", "/worktrees/"]
    assert not (repos.rig / ".gitignore").exists()
    subprocess.run(["git", "-C", str(repos.rig), "diff", "--exit-code"], env=repos.env, check=True)


@pytest.mark.parametrize("name", ["a..b", "a b", "x~1"])
def test_rejects_unsafe_name(repos: Repos, name: str):
    target = repos.target(name)

    result = repos.run(name)

    assert_refused(result, target)
    assert not target.exists()
    assert repos.git("-C", repos.rig, "branch", "--list", "gc-chat-*") == ""


def test_usage_error_is_one_prefixed_line(repos: Repos):
    result = subprocess.run(
        [str(SCRIPT), str(repos.rig)],
        env=repos.env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 1
    assert result.stderr.startswith("worktree-chat: usage: ")
    assert len(result.stderr.splitlines()) == 1
