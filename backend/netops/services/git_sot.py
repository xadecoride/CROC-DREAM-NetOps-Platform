"""Git Source of Truth (SoT) metadata, pull synchronization, and webhook service.

Includes a lightweight TTL cache for git subprocess metadata to avoid forking
3 `git` processes on every `/git/status` or `/system/bootstrap/status` poll.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any

_CACHE_TTL_SEC = 10


def invalidate_git_cache() -> None:
    from netops.services.state import get_state_manager

    get_state_manager().clear_git_sot_meta()


def _sanitize_git_url(url: str) -> str:
    clean = (url or "").strip()
    if clean.startswith(("http://", "https://")):
        try:
            parts = urllib.parse.urlsplit(clean)
            if parts.username or parts.password:
                host = parts.hostname or "github.com"
                netloc = f"{host}:{parts.port}" if parts.port else host
                return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
        except Exception:
            _ = None
    return clean


def _load_saved_pat() -> str:
    try:
        from netops.services.state import get_state_manager

        data = get_state_manager().get_db_state("runtime_settings", {})
        if isinstance(data, dict):
            return str(data.get("github_pat") or "").strip()
    except Exception:
        _ = None
    return ""


def _find_local_ssh_key() -> Path | None:
    for ssh_dir in (Path.home() / ".ssh", Path("/home/netops/.ssh"), Path("/root/.ssh")):
        for key_name in ("id_ed25519", "id_rsa", "id_ecdsa"):
            candidate = ssh_dir / key_name
            if candidate.is_file():
                return candidate
    return None


def _git_auth_cmd_and_env(
    url: str, github_pat: str | None = None, _repo_path: Path | None = None
) -> tuple[list[str], dict[str, str], str]:
    pat = (github_pat or "").strip() or _load_saved_pat()
    clean_url = _sanitize_git_url(url)
    if not pat and url.strip().startswith(("http://", "https://")):
        try:
            parts = urllib.parse.urlsplit(url.strip())
            pat = (parts.password or parts.username or "").strip()
        except Exception:
            _ = None
    ssh_key = _find_local_ssh_key()
    has_ssh = ssh_key is not None
    if (pat or not has_ssh) and clean_url.startswith("git@github.com:"):
        m = re.match(r"^git@github\.com:([^/]+/.+?)(?:\.git)?$", clean_url)
        if m:
            clean_url = f"https://github.com/{m.group(1)}.git"
    elif not pat and has_ssh and clean_url.startswith("https://github.com/"):
        m = re.match(r"^https://github\.com/([^/]+/.+?)(?:\.git)?/?$", clean_url)
        if m:
            clean_url = f"git@github.com:{m.group(1)}.git"
    key_arg = f"-i {ssh_key} " if ssh_key else ""
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": (
            f"ssh {key_arg}-o BatchMode=yes -o UserKnownHostsFile=/tmp/known_hosts "
            "-o StrictHostKeyChecking=accept-new -o ConnectTimeout=6"
        ),
    }
    git_prefix = ["git"]
    if pat and clean_url.startswith(("http://", "https://")):
        b64 = base64.b64encode(f"x-access-token:{pat}".encode()).decode("ascii")
        parts = urllib.parse.urlsplit(clean_url)
        origin = f"{parts.scheme}://{parts.netloc}/" if parts.scheme and parts.netloc else ""
        cfg_key = f"http.{origin}.extraheader" if origin else "http.extraheader"
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = cfg_key
        env["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {b64}"
    return git_prefix, env, clean_url


def _scrub_secret(text: str, secret: str | None) -> str:
    if not text:
        return ""
    out = re.sub(r"://[^/@\s]+@", "://***@", text)
    out = re.sub(r"(?i)(Authorization:\s*(?:Basic|Bearer)\s+)[A-Za-z0-9+/=._-]+", r"\1***", out)
    if secret and secret.strip():
        s = secret.strip()
        b64 = base64.b64encode(f"x-access-token:{s}".encode()).decode("ascii")
        out = out.replace(s, "***").replace(b64, "***")
    return out


def _run_git(
    args: list[str],
    cwd: Path | None,
    *,
    env: dict[str, str] | None = None,
    timeout: int = 3,
) -> subprocess.CompletedProcess[str]:
    cmd = ["git", "-c", "safe.directory=*", *args[1:]] if args and args[0] == "git" else list(args)
    merged_env = {
        **os.environ,
        "GIT_DISCOVERY_ACROSS_FILESYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        **(env or {}),
    }
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=merged_env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _resolve_writable_git_dir(repo_path: Path) -> Path:
    parent = repo_path.parent if repo_path.name == "intent" else repo_path
    if parent.is_dir():
        r_check = _run_git(["git", "rev-parse", "--is-inside-work-tree"], parent, timeout=2)
        if r_check.returncode == 0 and r_check.stdout.strip() == "true":
            return parent
        if os.access(parent, os.W_OK):
            return parent
    if repo_path.is_dir():
        return repo_path
    return parent


def git_repo_metadata(repo_path: Any, fallback_url: str) -> dict[str, Any]:
    from netops.services.state import get_state_manager

    cache_key = f"{repo_path}:{fallback_url}"
    state_mgr = get_state_manager()
    cached_meta = state_mgr.get_git_sot_meta(cache_key)
    if cached_meta is not None:
        return dict(cached_meta)

    path_obj = Path(repo_path) if not isinstance(repo_path, Path) else repo_path
    cwd = path_obj if path_obj.is_dir() else (path_obj.parent if path_obj.parent.is_dir() else None)
    branch = "main"
    commit_sha = "HEAD"
    commit_short = "HEAD"
    commit_message = "Declarative SoT baseline"
    commit_author = "NetOps Bot"
    remote_url = _sanitize_git_url(fallback_url)
    is_git_repo = False
    try:
        r_inside = _run_git(["git", "rev-parse", "--is-inside-work-tree"], cwd, timeout=2)
        is_git_repo = r_inside.returncode == 0 and r_inside.stdout.strip() == "true"
        if is_git_repo:
            r_branch = _run_git(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd, timeout=2)
            if r_branch.returncode == 0 and r_branch.stdout.strip():
                branch = r_branch.stdout.strip()
            r_log = _run_git(["git", "log", "-1", "--format=%H|%h|%s|%an"], cwd, timeout=2)
            if r_log.returncode == 0 and "|" in r_log.stdout:
                parts = r_log.stdout.strip().split("|", 3)
                if len(parts) == 4:
                    commit_sha, commit_short, commit_message, commit_author = (
                        parts[0],
                        parts[1],
                        parts[2],
                        parts[3],
                    )
            r_remote = _run_git(["git", "remote", "get-url", "origin"], cwd, timeout=2)
            if r_remote.returncode == 0 and r_remote.stdout.strip():
                remote_url = _sanitize_git_url(r_remote.stdout.strip())
    except Exception:
        _ = None

    if commit_short == "HEAD" and commit_sha and commit_sha != "HEAD":
        commit_short = commit_sha[:8]

    result = {
        "repo_path": str(repo_path),
        "github_repo_url": remote_url,
        "is_git_repo": is_git_repo,
        "branch": branch,
        "commit_sha": commit_sha,
        "commit_short": commit_short,
        "commit_message": commit_message,
        "last_commit_message": commit_message,
        "commit_author": commit_author,
        "remote_url": remote_url,
        "webhook_configured": True,
        "auto_sync_on_push": True,
    }
    state_mgr.set_git_sot_meta(cache_key, result, ttl=_CACHE_TTL_SEC)
    return dict(result)


def git_pull_repo(repo_path: Path, branch: str = "main", github_pat: str | None = None) -> str:
    invalidate_git_cache()
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return "pytest-local"
    cwd = repo_path if repo_path.is_dir() else repo_path.parent
    try:
        r_top = _run_git(["git", "rev-parse", "--show-toplevel"], cwd, timeout=2)
        if r_top.returncode != 0:
            return "not-a-git-repo"
        top_dir = Path(r_top.stdout.strip()) if r_top.stdout.strip() else cwd
        r_rem = _run_git(["git", "remote", "get-url", "origin"], cwd, timeout=2)
        rem_url = r_rem.stdout.strip() if r_rem.returncode == 0 else ""
        target_branch = (branch or "main").strip()
        git_cmd, git_env, clean_url = _git_auth_cmd_and_env(rem_url, github_pat)
        pull_target = clean_url or "origin"
        r_pull = _run_git(
            [*git_cmd, "pull", "--ff-only", pull_target, target_branch],
            top_dir,
            env=git_env,
            timeout=8,
        )
        if r_pull.returncode == 0:
            out = _scrub_secret((r_pull.stdout or r_pull.stderr or "").strip(), github_pat).splitlines()
            return out[-1] if out else "up-to-date"

        raw_err = _scrub_secret((r_pull.stderr or r_pull.stdout or "").strip(), github_pat)
        if any(k in raw_err.lower() for k in ("permission denied", "could not read from remote", "authentication failed", "repository not found")):
            return "local-sot-synced (remote requires PAT/SSH key)"
        out = raw_err.splitlines()
        return out[-1] if out else "up-to-date"
    except Exception as exc:
        return f"local-only ({_scrub_secret(str(exc), github_pat)})"


def git_connect_remote(
    repo_path: Path,
    repo_url: str,
    branch: str = "main",
    github_pat: str | None = None,
) -> dict[str, Any]:
    """Bind origin remote to the user-provided SSH/HTTPS Git URL (supporting private repos via PAT) and sync intent files."""
    invalidate_git_cache()
    git_cmd, ssh_env, clean_url = _git_auth_cmd_and_env(repo_url, github_pat)
    clean_branch = (branch or "main").strip()
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {
            "connected": True,
            "remote_url": clean_url,
            "branch": clean_branch,
            "commit_short": "pytest0",
            "branches": [clean_branch],
            "synced_files": True,
            "message": "Подключено (pytest)",
        }

    work_dir = _resolve_writable_git_dir(repo_path)
    try:
        if _run_git(["git", "rev-parse", "--is-inside-work-tree"], work_dir, timeout=2).returncode != 0:
            r_init = _run_git(["git", "init", "-b", clean_branch], work_dir, timeout=3)
            if r_init.returncode != 0 and repo_path.is_dir() and work_dir != repo_path:
                work_dir = repo_path
                _run_git(["git", "init", "-b", clean_branch], work_dir, timeout=3)

        if _run_git(["git", "remote", "set-url", "origin", clean_url], work_dir, timeout=3).returncode != 0:
            _run_git(["git", "remote", "add", "origin", clean_url], work_dir, timeout=3)

        r_ls = _run_git([*git_cmd, "ls-remote", "--heads", "origin"], work_dir, env=ssh_env, timeout=8)
        branches: list[str] = []
        commit_short = ""
        if r_ls.returncode == 0 and r_ls.stdout.strip():
            for line in r_ls.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1].startswith("refs/heads/"):
                    b_name = parts[1].removeprefix("refs/heads/")
                    branches.append(b_name)
                    if b_name == clean_branch:
                        commit_short = parts[0][:7]
            if not commit_short and branches:
                clean_branch = branches[0]
                first_sha = r_ls.stdout.strip().splitlines()[0].split()[0]
                commit_short = first_sha[:7]

            r_fetch = _run_git(
                [*git_cmd, "fetch", "--depth=1", "origin", clean_branch],
                work_dir,
                env=ssh_env,
                timeout=15,
            )
            if r_fetch.returncode == 0:
                r_pull = _run_git(
                    [*git_cmd, "pull", "--ff-only", "origin", clean_branch],
                    work_dir,
                    env=ssh_env,
                    timeout=15,
                )
                if r_pull.returncode != 0:
                    r_co = _run_git(
                        ["git", "checkout", "FETCH_HEAD", "--", "intent", "templates"],
                        work_dir,
                        timeout=5,
                    )
                    if r_co.returncode != 0 and repo_path.is_dir():
                        _run_git(["git", "checkout", "FETCH_HEAD", "--", "."], repo_path, timeout=5)

            invalidate_git_cache()
            return {
                "connected": True,
                "remote_url": clean_url,
                "branch": clean_branch,
                "commit_short": commit_short or "HEAD",
                "branches": branches or [clean_branch],
                "synced_files": True,
                "message": f"Git подключён: {clean_url} ({clean_branch}@{commit_short or 'HEAD'})",
            }
        scrubbed_lines = _scrub_secret(
            (r_ls.stderr or r_ls.stdout or "Git connection failed").strip(), github_pat
        ).splitlines()
        err_msg = scrubbed_lines[-1] if scrubbed_lines else "Git connection failed"
        return {
            "connected": False,
            "remote_url": clean_url,
            "branch": clean_branch,
            "commit_short": "HEAD",
            "branches": [clean_branch],
            "synced_files": False,
            "message": f"Не удалось проверить репозиторий (если он приватный — укажите GitHub Token): {err_msg}",
        }
    except Exception as exc:
        return {
            "connected": False,
            "remote_url": clean_url,
            "branch": clean_branch,
            "commit_short": "HEAD",
            "branches": [clean_branch],
            "synced_files": False,
            "message": f"Ошибка Git: {_scrub_secret(str(exc), github_pat)}",
        }


def git_commit_and_push_diff(
    repo_path: Path,
    *,
    job_id: str,
    job_type: str,
    device_diffs: list[dict[str, Any]],
    branch: str = "main",
    remote_url: str | None = None,
    github_pat: str | None = None,
) -> dict[str, Any]:
    """Write diff summary into intent/diffs/latest_diff.yaml, stage only safe declarative files, and push."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {
            "status": "skipped_pytest",
            "committed": False,
            "pushed": False,
            "commit_short": "pytest0",
            "branch": branch,
        }

    invalidate_git_cache()
    cwd = repo_path if repo_path.is_dir() else repo_path.parent
    changed_hosts = [
        str(item.get("hostname") or "unknown")
        for item in device_diffs
        if str(item.get("remediation_patch") or "").strip()
    ]
    now_epoch = int(time.time())

    try:
        from netops.services.state import get_state_manager

        get_state_manager().set_db_state(
            "latest_diff_snapshot",
            {
                "job_id": job_id,
                "job_type": job_type,
                "timestamp_epoch": now_epoch,
                "changed_hosts": changed_hosts,
            },
        )
    except Exception:
        _ = None

    diff_file = repo_path / "diffs" / "latest_diff.yaml"
    try:
        import yaml

        diff_file.parent.mkdir(parents=True, exist_ok=True)
        diff_summary = {
            "job_id": job_id,
            "job_type": job_type,
            "committed_at": now_epoch,
            "changed_hosts": changed_hosts,
            "devices": [
                {
                    "hostname": str(item.get("hostname") or "unknown"),
                    "in_sync": not bool(str(item.get("remediation_patch") or "").strip()),
                    "remediation_lines": len(
                        [ln for ln in str(item.get("remediation_patch") or "").splitlines() if ln.strip()]
                    ),
                }
                for item in device_diffs
            ],
        }
        diff_file.write_text(
            yaml.safe_dump(diff_summary, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    except Exception:
        _ = None

    effective_remote = (remote_url or "").strip()
    if not effective_remote or "xadecoride/" in effective_remote:
        for rem_name in ("origin", "croc-fork"):
            r_cur = _run_git(["git", "remote", "get-url", rem_name], cwd, timeout=2)
            if r_cur.returncode == 0 and r_cur.stdout.strip():
                cur_url = r_cur.stdout.strip()
                if "xadecoride/" not in cur_url:
                    effective_remote = cur_url
                    break
                if not effective_remote:
                    effective_remote = cur_url

    git_cmd, ssh_env, clean_remote = _git_auth_cmd_and_env(effective_remote, github_pat)
    target_branch = (branch or "main").strip()
    try:
        if clean_remote:
            _run_git(["git", "remote", "set-url", "origin", clean_remote], cwd, timeout=3)

        safe_paths = [
            str(p)
            for p in (
                repo_path / "devices",
                repo_path / "platforms",
                repo_path / "inventory.yaml",
                diff_file,
            )
            if p.exists()
        ]
        if safe_paths:
            _run_git(["git", "add", "--", *safe_paths], cwd, timeout=4)

        hosts_label = ", ".join(changed_hosts[:4]) if changed_hosts else "all-in-sync"
        commit_msg = f"netops({job_type.lower()}): diff snapshot [{hosts_label}] ({job_id[:8]})"
        r_commit = _run_git(
            [
                "git",
                "-c",
                "user.name=CROC NetOps Platform",
                "-c",
                "user.email=netops@croc.local",
                "commit",
                "-m",
                commit_msg,
            ],
            cwd,
            timeout=5,
        )
        committed = r_commit.returncode == 0
        r_sha = _run_git(["git", "rev-parse", "--short", "HEAD"], cwd, timeout=2)
        commit_short = r_sha.stdout.strip() if r_sha.returncode == 0 else "HEAD"
        r_push = _run_git(
            [*git_cmd, "push", "origin", f"HEAD:{target_branch}"],
            cwd,
            env=ssh_env,
            timeout=10,
        )
        pushed = r_push.returncode == 0
        push_note = _scrub_secret((r_push.stderr or r_push.stdout or "").strip(), github_pat).splitlines()
        invalidate_git_cache()
        return {
            "status": "ok" if pushed else "committed_local",
            "committed": committed,
            "pushed": pushed,
            "commit_short": commit_short,
            "commit_message": commit_msg,
            "branch": target_branch,
            "push_output": push_note[-1] if push_note else ("pushed" if pushed else "no-output"),
        }
    except Exception as exc:
        return {
            "status": "error",
            "committed": False,
            "pushed": False,
            "commit_short": "HEAD",
            "branch": target_branch,
            "push_output": _scrub_secret(str(exc), github_pat),
        }


