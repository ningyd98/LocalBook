"""Safe, isolated Git-backed version storage for a Vault."""

from __future__ import annotations

import json

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None
import os
import selectors
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from server.vault.errors import InvalidOperation, VaultUnavailable
from server.vault.path_safety import is_reserved_derived_path, validate_relative_path
from server.vault.service import VaultService

_MAX_OUTPUT = 2 * 1024 * 1024
_MAX_CAPTURE = 3 * 1024 * 1024
_MAX_FILE_BYTES = 32 * 1024 * 1024
_MAX_TREE_BYTES = 256 * 1024 * 1024


class VersionStore:
    """Explicitly enabled per-Vault history, without touching user files."""

    def __init__(self, vault: VaultService, *, timeout: float = 30.0) -> None:
        self.vault = vault
        self.timeout = timeout
        self.max_output = _MAX_OUTPUT
        self.root = vault.root / ".localnote" / "versions"
        self.repo = self.root / "repo"
        self.config = self.root / "config.json"
        self.pending = self.root / "pending.json"
        self._pending_temp = self.pending.with_suffix(".tmp")
        self.lock_path = self.root / "writer.lock"
        self._process_lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        return self.repo.is_dir() and self.config.is_file() and (self.repo / "HEAD").is_file()

    def _run(self, args: list[str], *, cwd: Path | None = None, check: bool = True) -> str | None:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
        }
        proc = None
        stdout = bytearray()
        stderr = bytearray()
        try:
            proc = subprocess.Popen(
                ["git", *args],
                cwd=cwd or self.vault.root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            selector = selectors.DefaultSelector()
            selector.register(proc.stdout, selectors.EVENT_READ, stdout)
            selector.register(proc.stderr, selectors.EVENT_READ, stderr)
            deadline = time.monotonic() + self.timeout
            while selector.get_map() or proc.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise VaultUnavailable("Version operation timed out")
                for key, _ in selector.select(min(remaining, 0.05)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    key.data.extend(chunk)
                    if len(stdout) + len(stderr) > self.max_output:
                        raise VaultUnavailable("Version command output exceeded limit")
            returncode = proc.wait()
        except Exception as exc:
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait()
            if isinstance(exc, (VaultUnavailable, InvalidOperation)):
                raise
            if isinstance(exc, OSError):
                raise VaultUnavailable("Version backend unavailable") from exc
            raise
        if returncode and check:
            raise VaultUnavailable("Version operation failed")
        if returncode:
            return "__NONZERO__"
        return stdout.decode("utf-8", errors="replace")

    def _path(self, value: str) -> str:
        relative = validate_relative_path(value)
        if is_reserved_derived_path(relative) or relative.split("/", 1)[0] == ".git":
            raise InvalidOperation("Version path is reserved")
        target = self.vault.root / relative
        current = self.vault.root
        for component in relative.split("/"):
            current /= component
            if current.is_symlink():
                raise InvalidOperation("Symbolic links are not versioned")
        if target.exists() and not target.is_file():
            raise InvalidOperation("Only regular files can be versioned")
        return relative

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "backend": "git" if self.enabled else None}

    def _writer_lock(self):
        """Cross-process exclusive lock for refs/config mutations."""
        import contextlib

        @contextlib.contextmanager
        def held():
            with self._process_lock:
                self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
                descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    if fcntl is None:
                        raise VaultUnavailable("Version locking unsupported on this platform")
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                    os.close(descriptor)

        return held()

    def enable(self) -> dict[str, Any]:
        with self.vault._mutation_lock:  # noqa: SLF001
            with self._writer_lock():
                self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
                if not self.repo.exists():
                    self._run(["init", "--bare", str(self.repo)])
                elif not (self.repo / "HEAD").is_file():
                    raise VaultUnavailable("Version repository is corrupt")
                if not self.config.exists():
                    temporary = self.config.with_suffix(".tmp")
                    temporary.write_text(
                        json.dumps({"schema": 1, "backend": "git"}) + "\n", encoding="utf-8"
                    )
                    os.replace(temporary, self.config)
        return self.status()

    def _atomic_config(self, values: dict[str, Any]) -> None:
        temp = self.config.with_suffix(".tmp")
        try:
            with temp.open("w", encoding="utf-8") as stream:
                json.dump(values, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.config)
        finally:
            temp.unlink(missing_ok=True)

    def _write_pending(self, values: dict[str, Any]) -> None:
        journal = self.pending.with_suffix(".tmp")
        try:
            with journal.open("w", encoding="utf-8") as stream:
                json.dump(values, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(journal, self.pending)
        finally:
            journal.unlink(missing_ok=True)

    def _recover_pending(self) -> None:
        if not self.pending.exists():
            return
        try:
            state = json.loads(self.pending.read_text(encoding="utf-8"))
            commit = state.get("commit")
            if state.get("state") == "prepared":
                commit = (
                    self._run(["--git-dir", str(self.repo), "rev-parse", "HEAD"]) or ""
                ).strip()
            if not isinstance(commit, str) or len(commit) not in (40, 64):
                raise ValueError
            self._atomic_config({"schema": 1, "backend": "git", "last_snapshot": commit})
            self.pending.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise VaultUnavailable("Version recovery journal is corrupt") from exc

    def _stage_locked(self) -> Path:
        staging = Path(tempfile.mkdtemp(prefix="snapshot-", dir=self.root))
        total = 0
        try:
            for source in self.vault.root.rglob("*"):
                relative = source.relative_to(self.vault.root)
                if relative.parts and relative.parts[0] == ".localnote":
                    continue
                if source.is_symlink():
                    continue
                if not source.is_file():
                    continue
                descriptor = -1
                try:
                    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                    info = os.fstat(descriptor)
                    if not stat.S_ISREG(info.st_mode):
                        raise InvalidOperation("Only regular files can be versioned")
                    size = info.st_size
                    if size > _MAX_FILE_BYTES or total + size > _MAX_TREE_BYTES:
                        raise InvalidOperation("Version snapshot size limit exceeded")
                    total += size
                    target = staging / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with (
                        os.fdopen(descriptor, "rb") as source_file,
                        target.open("wb") as target_file,
                    ):
                        shutil.copyfileobj(source_file, target_file)
                    descriptor = -1
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
            return staging
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def snapshot(self, *, reason: str = "manual") -> dict[str, Any] | None:
        """Commit a consistent staging tree while holding the Vault mutation lock."""
        with self.vault._mutation_lock:  # noqa: SLF001
            self.enable()
            with self._writer_lock():
                self._recover_pending()
                return self._snapshot_locked(reason)

    def _snapshot_locked(self, reason: str) -> dict[str, Any] | None:
        staging = self._stage_locked()
        git = ["--git-dir", str(self.repo), "--work-tree", str(staging)]
        try:
            old = self._run(
                ["--git-dir", str(self.repo), "rev-parse", "--verify", "HEAD"], check=False
            )
            if old and old != "__NONZERO__":
                self._run(["--git-dir", str(self.repo), "read-tree", "HEAD"])
            self._run([*git, "add", "-A"])
            changed = self._run([*git, "diff", "--cached", "--quiet"], check=False)
            if changed == "":
                return None
            if changed != "__NONZERO__":
                raise VaultUnavailable("Version operation failed")
            self._write_pending({"state": "prepared", "reason": reason})
            self._run(
                [
                    *git,
                    "-c",
                    "user.name=LocalBook",
                    "-c",
                    "user.email=localbook@localhost",
                    "commit",
                    "-m",
                    reason,
                ]
            )
            commit = (self._run(["--git-dir", str(self.repo), "rev-parse", "HEAD"]) or "").strip()
            self._write_pending({"state": "committed", "commit": commit, "reason": reason})
            self._atomic_config(
                {"schema": 1, "backend": "git", "last_snapshot": commit, "reason": reason}
            )
            self.pending.unlink(missing_ok=True)
            return {"id": commit, "commit": commit, "reason": reason}
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def history(
        self, *, limit: int = 20, offset: int = 0, path: str | None = None
    ) -> dict[str, Any]:
        if not self.enabled:
            return {
                "items": [],
                "total": 0,
                "limit": max(1, min(limit, 100)),
                "offset": max(0, offset),
            }
        args = ["--git-dir", str(self.repo), "log", "--format=%H%x09%cI%x09%s"]
        if path:
            args += ["--", self._path(path)]
        lines = (self._run(args) or "").splitlines()
        safe_limit = max(1, min(int(limit), 100))
        safe_offset = max(0, int(offset))
        items = [
            dict(zip(("id", "created_at", "reason"), line.split("\t", 2), strict=True))
            for line in lines[safe_offset : safe_offset + safe_limit]
        ]
        return {"items": items, "total": len(lines), "limit": safe_limit, "offset": safe_offset}

    def content(self, commit: str, path: str) -> bytes:
        relative = self._path(path)
        if (
            not commit
            or len(commit) not in (40, 64)
            or any(c not in "0123456789abcdef" for c in commit.lower())
        ):
            raise InvalidOperation("Invalid version identifier")
        output = self._run(["--git-dir", str(self.repo), "show", f"{commit}:{relative}"])
        return (output or "").encode()

    def diff(
        self, base: str, target: str, path: str | None = None, *, max_bytes: int = _MAX_OUTPUT
    ) -> str:
        if max_bytes < 0 or max_bytes > _MAX_OUTPUT:
            raise InvalidOperation("Diff limit is invalid")
        args = ["--git-dir", str(self.repo), "diff", "--no-ext-diff", base, target]
        if path:
            args += ["--", self._path(path)]
        return (self._run(args) or "")[:max_bytes]


__all__ = ["VersionStore"]
