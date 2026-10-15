#!/usr/bin/env python3
"""Install OIO issue-log intake into one explicitly selected Git repository."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import stat
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

try:
    from jsonschema import Draft202012Validator
except ImportError as exc:  # pragma: no cover - exercised by CLI error path
    raise SystemExit("Install OIO tooling dependencies with: python -m pip install -r requirements.txt") from exc


SOURCE_ROOT = Path(__file__).resolve().parents[2]
SOURCE_IS_ADOPTER = not (SOURCE_ROOT / "ontology/default.json").is_file() and (SOURCE_ROOT / ".oio/ontology/default.json").is_file()
DATA_ROOT = SOURCE_ROOT / ".oio" if SOURCE_IS_ADOPTER else SOURCE_ROOT
try:
    OIO_VERSION = json.loads((SOURCE_ROOT / "stack-manifest.json").read_text(encoding="utf-8"))["pins"]["observational-issue-ops"]["version"]
except (OSError, KeyError, json.JSONDecodeError):
    try:
        OIO_VERSION = json.loads((SOURCE_ROOT / ".oio/install-manifest.json").read_text(encoding="utf-8"))["oio_version"]
    except (OSError, KeyError, json.JSONDecodeError):
        OIO_VERSION = "0.1.0"
MANIFEST_REL = ".oio/install-manifest.json"
JOURNAL_REL = ".oio/.installer-transaction.json"
AGENTS_START = "<!-- oio:issue-log-guidance:start -->"
AGENTS_END = "<!-- oio:issue-log-guidance:end -->"

PACKAGE_FILES = {
    ".oio/ontology/default.json": "ontology/default.json",
    ".oio/ontology/project-template.json": "ontology/project-template.json",
    ".oio/ontology/ISSUE_LOG_ONTOLOGY.md": "ontology/ISSUE_LOG_ONTOLOGY.md",
    ".oio/ontology/AGENT_GUIDE.md": "ontology/AGENT_GUIDE.md",
    ".oio/requirements.txt": "requirements.txt",
    ".oio/schemas/v1/default-ontology.schema.json": "schemas/v1/default-ontology.schema.json",
    ".oio/schemas/v1/project-ontology.schema.json": "schemas/v1/project-ontology.schema.json",
    ".oio/schemas/v1/issue-log-record.schema.json": "schemas/v1/issue-log-record.schema.json",
    ".github/ISSUE_TEMPLATE/observational-issue.yml": ".github/ISSUE_TEMPLATE/observational-issue.yml",
    ".github/workflows/issue-triage.yml": ".github/workflows/issue-triage.yml",
    ".github/scripts/oio_installer.py": ".github/scripts/oio_installer.py",
    ".github/scripts/oio_triage.py": ".github/scripts/oio_triage.py",
}
RECOVERY_ALLOWED = set(PACKAGE_FILES) | {"AGENTS.md", ".oio/ontology/project.json", MANIFEST_REL}


class InstallError(Exception):
    """A safe, actionable install refusal."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lf_bytes(data: bytes) -> bytes:
    """Return ``data`` with CRLF and lone CR normalized to LF.

    OIO's managed files are text, so their integrity contract is about content,
    not the line-ending style of the checkout that ran the installer. Installing
    the LF form makes a Windows (CRLF) and a Linux (LF) install byte-identical,
    so a correct install passes its own ``--check`` gate on either platform.
    """
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _matches_recorded(data: bytes, recorded_sha256: str) -> bool:
    """Whether ``data`` matches a recorded digest, ignoring line-ending style.

    The recorded digest was taken over the raw bytes of whatever checkout ran
    the older installer (LF on Linux, CRLF on Windows), and the current file may
    be in either style. Comparing every canonical form accepts a manifest written
    by that older installer so an existing adopter migrates through a normal
    re-install instead of deleting the install. A real content edit changes all
    of these digests and is still refused.
    """
    normalized = _lf_bytes(data)
    candidates = {data, normalized, normalized.replace(b"\n", b"\r\n")}
    return any(_sha256(candidate) == recorded_sha256 for candidate in candidates)


def _source_file(relative: str) -> Path:
    if relative.startswith(".github/") or relative == "AGENTS.md":
        return SOURCE_ROOT / relative
    return DATA_ROOT / relative


def _after_parent_open(target: Path, relative: str) -> None:
    """Test hook for exercising path swaps after secure directory traversal."""


def _is_reparse(info: os.stat_result) -> bool:
    """Whether a stat result is a symlink or a Windows reparse point (junction)."""
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _is_link_or_reparse(path: Path) -> bool:
    """``Path.is_symlink`` misses Windows junctions; this also catches those."""
    if path.is_symlink():
        return True
    try:
        return _is_reparse(os.lstat(path))
    except OSError:
        return False


class _TargetFS:
    """Managed-path access, dispatched per platform.

    POSIX holds descriptor-relative no-follow directory handles; Windows refuses
    reparse points and re-verifies parent identity, since it has no ``dir_fd``.
    """

    def __new__(cls, root: Path):
        if cls is _TargetFS:
            return super().__new__(_WindowsTargetFS if sys.platform == "win32" else _PosixTargetFS)
        return super().__new__(cls)


class _PosixTargetFS(_TargetFS):
    """Descriptor-relative access that refuses symlinks at managed path components."""

    def __init__(self, root: Path):
        if _posix_precondition():
            raise InstallError("this platform lacks descriptor-relative no-follow filesystem operations")
        self.root = root
        self.root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    def close(self) -> None:
        if self.root_fd is not None:
            os.close(self.root_fd)
            self.root_fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def _parent(self, relative: str, *, create: bool = False) -> tuple[int, str]:
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise InstallError(f"unsafe package path: {relative!r}")
        current_fd = os.dup(self.root_fd)
        try:
            for part in pure.parts[:-1]:
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                try:
                    next_fd = os.open(part, flags, dir_fd=current_fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(part, mode=0o755, dir_fd=current_fd)
                    except FileExistsError:
                        pass
                    next_fd = os.open(part, flags, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            return current_fd, pure.parts[-1]
        except Exception:
            os.close(current_fd)
            raise

    def read_bytes(self, relative: str) -> bytes | None:
        try:
            parent_fd, leaf = self._parent(relative)
        except FileNotFoundError:
            return None
        try:
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        except FileNotFoundError:
            os.close(parent_fd)
            return None
        except OSError:
            os.close(parent_fd)
            raise
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise InstallError(f"managed target path is not a regular file: {relative}")
            with os.fdopen(fd, "rb") as handle:
                fd = -1
                return handle.read()
        finally:
            if fd >= 0:
                os.close(fd)
            os.close(parent_fd)

    def _verify_parent(self, relative: str, opened_fd: int) -> None:
        current_fd, _ = self._parent(relative)
        try:
            opened = os.fstat(opened_fd)
            current = os.fstat(current_fd)
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise InstallError(f"managed target path changed during installation: {relative}")
        finally:
            os.close(current_fd)

    def atomic_write(self, relative: str, data: bytes) -> None:
        parent_fd, leaf = self._parent(relative, create=True)
        temp_name = f".oio-tmp-{os.getpid()}-{os.urandom(8).hex()}"
        temp_fd = -1
        try:
            _after_parent_open(self.root, relative)
            self._verify_parent(relative, parent_fd)
            temp_fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
            with os.fdopen(temp_fd, "wb") as handle:
                temp_fd = -1
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                info = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    raise InstallError(f"refusing symlink in managed target path: {relative}")
                if not stat.S_ISREG(info.st_mode):
                    raise InstallError(f"managed target path is not a regular file: {relative}")
            except FileNotFoundError:
                pass
            self._verify_parent(relative, parent_fd)
            os.rename(temp_name, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            if temp_fd >= 0:
                os.close(temp_fd)
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            os.close(parent_fd)

    def unlink(self, relative: str) -> None:
        parent_fd, leaf = self._parent(relative)
        try:
            self._verify_parent(relative, parent_fd)
            os.unlink(leaf, dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)


class _WindowsTargetFS(_TargetFS):
    """Refuse reparse points and re-verify parent identity around each write.

    Windows exposes no ``dir_fd`` operations, so this cannot hold a directory
    open the way the POSIX layer does. It refuses a symlink, junction, or any
    other reparse point at every managed path component, and re-verifies the
    parent directory's identity immediately before and after each replacement.
    A same-user process that swaps the parent path inside the remaining window
    is the documented residual race (see docs/ADOPTER_INSTALL.md); a swap
    detected at any checkpoint fails closed.
    """

    def __init__(self, root: Path):
        info = os.lstat(root)
        if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
            raise InstallError(f"target root is a symlink or reparse point: {root}")
        self.root = root
        self._root_id = (info.st_dev, info.st_ino)

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def _parts(self, relative: str) -> tuple[str, ...]:
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts or any("\\" in part for part in pure.parts):
            raise InstallError(f"unsafe package path: {relative!r}")
        return pure.parts

    def _check(self, path: Path) -> os.stat_result:
        info = os.lstat(path)
        if _is_reparse(info):
            raise InstallError(f"refusing symlink or reparse point in managed target path: {path}")
        return info

    def _verify_root(self) -> None:
        info = os.lstat(self.root)
        if _is_reparse(info) or (info.st_dev, info.st_ino) != self._root_id:
            raise InstallError("target root changed during installation")

    def _parent(self, relative: str, *, create: bool = False) -> tuple[Path, str, tuple[int, int]]:
        parts = self._parts(relative)
        parent = self.root
        for part in parts[:-1]:
            parent = parent / part
            try:
                info = self._check(parent)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(parent)
                info = self._check(parent)
            if not stat.S_ISDIR(info.st_mode):
                raise InstallError(f"managed target path is not a directory: {relative}")
        info = self._check(parent)
        return parent, parts[-1], (info.st_dev, info.st_ino)

    def _verify_parent(self, parent: Path, expected: tuple[int, int]) -> None:
        info = os.lstat(parent)
        if _is_reparse(info) or (info.st_dev, info.st_ino) != expected:
            raise InstallError(f"managed target path changed during installation: {parent}")

    def read_bytes(self, relative: str) -> bytes | None:
        path = self.root
        try:
            for part in self._parts(relative):
                path = path / part
                self._check(path)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise InstallError(f"managed target path is not a regular file: {relative}")
        return path.read_bytes()

    def atomic_write(self, relative: str, data: bytes) -> None:
        parent, leaf, parent_id = self._parent(relative, create=True)
        temp_name = f".oio-tmp-{os.getpid()}-{os.urandom(8).hex()}"
        temp_path = parent / temp_name
        try:
            _after_parent_open(self.root, relative)
            self._verify_root()
            self._verify_parent(parent, parent_id)
            fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as handle:
                    fd = -1
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                if fd >= 0:
                    os.close(fd)
            leaf_path = parent / leaf
            try:
                info = os.lstat(leaf_path)
            except FileNotFoundError:
                pass
            else:
                if _is_reparse(info):
                    raise InstallError(f"refusing symlink or reparse point in managed target path: {relative}")
                if not stat.S_ISREG(info.st_mode):
                    raise InstallError(f"managed target path is not a regular file: {relative}")
            self._verify_parent(parent, parent_id)
            os.replace(temp_path, leaf_path)
            self._verify_parent(parent, parent_id)
        finally:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass

    def unlink(self, relative: str) -> None:
        parent, leaf, parent_id = self._parent(relative)
        self._verify_parent(parent, parent_id)
        leaf_path = parent / leaf
        if _is_reparse(os.lstat(leaf_path)):
            raise InstallError(f"refusing symlink or reparse point in managed target path: {relative}")
        os.unlink(leaf_path)


def _recover_transaction(target: Path, target_fs: _TargetFS) -> None:
    journal_bytes = target_fs.read_bytes(JOURNAL_REL)
    if journal_bytes is None:
        return
    try:
        journal = json.loads(journal_bytes.decode("utf-8"))
        if journal.get("schema_version") != "oio.installer-transaction.v1" or set(journal) != {"schema_version", "entries"}:
            raise InstallError("unsupported install recovery journal; preserve it and contact the OIO maintainer")
        entries = journal["entries"]
        if not isinstance(entries, dict) or not set(entries).issubset(RECOVERY_ALLOWED):
            raise InstallError("install recovery journal contains paths outside the OIO-managed allow-list")
        restore_actions: list[tuple[str, bytes | None]] = []
        for relative, entry in entries.items():
            path = _target_file(target, relative)
            if not isinstance(entry, dict) or set(entry) != {"before", "planned_sha256"}:
                raise InstallError(f"invalid recovery journal entry for {relative}")
            before = entry["before"]
            planned_hash = entry["planned_sha256"]
            if not (before is None or isinstance(before, str)) or not isinstance(planned_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", planned_hash):
                raise InstallError(f"invalid recovery journal hashes for {relative}")
            current_data = target_fs.read_bytes(relative)
            current_hash = _sha256(current_data) if current_data is not None else None
            before_bytes = base64.b64decode(before, validate=True) if before is not None else None
            before_hash = _sha256(before_bytes) if before_bytes is not None else None
            if current_hash == before_hash:
                continue
            if current_hash != planned_hash:
                raise InstallError(f"recovery conflict at {relative}; current file differs from both saved and planned transaction data")
            restore_actions.append((relative, before_bytes))
        for relative, before_bytes in restore_actions:
            if before_bytes is None:
                target_fs.unlink(relative)
            else:
                target_fs.atomic_write(relative, before_bytes)
        target_fs.unlink(JOURNAL_REL)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise InstallError(f"could not safely recover interrupted installation: {exc}") from exc


def priority_key(path: str) -> tuple[int, ...]:
    """Parse a hierarchical priority path without floating-point conversion."""
    if not isinstance(path, str) or not re.fullmatch(r"(?:[1-9]|[1-9][0-9]|100)(?:\.[1-9][0-9]*)*", path):
        raise ValueError(f"invalid priority path: {path!r}")
    return tuple(int(component) for component in path.split("."))


def _schema_validate(value: dict, schema_path: Path) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda error: list(map(str, error.path)))
    if errors:
        details = "; ".join(f"/{'/'.join(map(str, error.path)) or '<root>'}: {error.message}" for error in errors)
        raise InstallError(f"ontology schema validation failed: {details}")


def validate_project_ontology(value: dict, schema_path: Path | None = None, core: dict | None = None) -> None:
    """Validate extension structure and references beyond JSON Schema shape checks."""
    core = core or json.loads((DATA_ROOT / "ontology/default.json").read_text(encoding="utf-8"))
    schema_path = schema_path or DATA_ROOT / "schemas/v1/project-ontology.schema.json"
    _schema_validate(value, schema_path)

    namespace = value["namespace"]
    if namespace == "oio":
        raise InstallError("project ontology namespace 'oio' is reserved")
    if value["extends"]["ontology_id"] != core["ontology_id"] or value["extends"]["version"] not in ({core["version"]} | set(core.get("compatible_project_ontology_versions", []))):
        raise InstallError("project ontology must extend the exact installed OIO ontology ID and version")

    core_ids = {"oio:observational", "oio:operational", "oio:human-direct", "oio:human-via-agent", "oio:agent-proposed", "oio:agent-initiated"}
    own_ids: set[str] = set()
    for concept in value["concepts"]:
        concept_id = concept["id"]
        if not concept_id.startswith(f"{namespace}:"):
            raise InstallError(f"extension concept {concept_id!r} must use namespace {namespace!r}")
        if concept_id in own_ids or concept_id in core_ids:
            raise InstallError(f"duplicate or protected ontology concept ID: {concept_id}")
        own_ids.add(concept_id)

    parents: dict[str, str] = {}
    for concept in value["concepts"]:
        concept_id, broader = concept["id"], concept["broader"]
        if broader != "oio:issue-log" and broader not in own_ids and broader not in core_ids:
            raise InstallError(f"concept {concept_id!r} refers to undefined broader concept {broader!r}")
        parents[concept_id] = broader
    for start in parents:
        seen: set[str] = set()
        node = start
        while node in parents:
            if node in seen:
                raise InstallError(f"concept hierarchy contains a cycle at {node!r}")
            seen.add(node)
            node = parents[node]

    seen_paths: set[str] = set()
    for entry in value["priorities"]:
        path = entry["path"]
        try:
            priority_key(path)
        except ValueError as exc:
            raise InstallError(str(exc)) from exc
        if path in seen_paths:
            raise InstallError(f"duplicate priority path: {path}")
        seen_paths.add(path)
        concept_id = entry["concept_id"]
        if not concept_id.startswith(f"{namespace}:"):
            raise InstallError(f"priority concept {concept_id!r} must use namespace {namespace!r}")
        if concept_id in own_ids or concept_id in core_ids:
            raise InstallError(f"duplicate or protected ontology concept ID: {concept_id}")
        own_ids.add(concept_id)

    account_ids: set[str] = set()
    for account in value["account_authority"]:
        user_id = account["github_user_id"]
        if user_id in account_ids:
            raise InstallError(f"duplicate GitHub account ID in owner policy: {user_id}")
        account_ids.add(user_id)
    for entry in value["priorities"]:
        parts = entry["path"].split(".")
        for end in range(1, len(parts)):
            parent = ".".join(parts[:end])
            if parent not in seen_paths:
                raise InstallError(f"priority {entry['path']!r} has undefined parent priority {parent!r}")


def _repo_from_remote(target: Path) -> str:
    try:
        remote = subprocess.run(
            ["git", "-C", str(target), "remote", "get-url", "origin"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise InstallError("cannot identify this target's origin; supply --project-id OWNER/REPO")
    if remote.startswith("git@"):
        match = re.fullmatch(r"git@[^:]+:([^/]+/[^/]+?)(?:\.git)?", remote)
        repo = match.group(1) if match else ""
    else:
        parsed = urlparse(remote)
        repo = parsed.path.lstrip("/").removesuffix(".git") if parsed.scheme and parsed.netloc else ""
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", repo):
        raise InstallError(f"origin is not a recognizable owner/repository URL: {remote!r}")
    return repo


def _safe_target(root_arg: str) -> Path:
    raw = Path(root_arg).expanduser()
    if not raw.is_absolute():
        raise InstallError("--target must be an explicit absolute path")
    if _is_link_or_reparse(raw):
        raise InstallError("--target cannot itself be a symlink")
    target = raw.resolve(strict=True)
    if not target.is_dir() or not ((target / ".git").is_dir() or (target / ".git").is_file()):
        raise InstallError("--target must be the root of an existing Git repository or worktree")
    return target


def _target_file(target: Path, relative: str, *, must_exist: bool = False) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise InstallError(f"unsafe package path: {relative!r}")
    current = target
    for part in pure.parts:
        current = current / part
        if _is_link_or_reparse(current):
            raise InstallError(f"refusing symlink in managed target path: {relative}")
    if must_exist and not current.exists():
        raise InstallError(f"required target file is missing: {relative}")
    if current.exists() and not current.is_file():
        raise InstallError(f"managed target path is not a regular file: {relative}")
    return current


def _agent_block() -> str:
    return f"{AGENTS_START}\nBefore filing an observational or operational issue log, read `.oio/ontology/ISSUE_LOG_ONTOLOGY.md`, `.oio/ontology/project.json`, and `.oio/ontology/AGENT_GUIDE.md`. Confirm the exact destination and filing action are authorized. On OIO, ACS, CGM, and PCM, do not submit an issue or write files without explicit human direction for that destination and action. A proposal can remain a local draft until directed. Never treat adoption as permission to write to an adopter or sibling repository.\n{AGENTS_END}"


def _managed_agents(existing: str | None, old_hash: str | None) -> tuple[str, str]:
    new_block = _agent_block()
    if existing is None:
        content = f"# Agent Instructions\n\n{new_block}\n"
        return content, _sha256(new_block.encode())
    start, end = existing.find(AGENTS_START), existing.find(AGENTS_END)
    if (start < 0) != (end < 0) or (start >= 0 and end < start):
        raise InstallError("AGENTS.md contains an incomplete or malformed OIO guidance marker")
    if start >= 0:
        prior = existing[start : end + len(AGENTS_END)]
        # The recorded hash is over the LF block; a CRLF worktree carries the same
        # block with CRLF, so normalize before comparing to avoid a false "edited".
        if old_hash is None or _sha256(_lf_bytes(prior.encode())) != old_hash:
            raise InstallError("the OIO-managed AGENTS.md section was edited; refusing to overwrite it")
        result = existing[:start] + new_block + existing[end + len(AGENTS_END) :]
    else:
        result = existing.rstrip() + "\n\n" + new_block + "\n"
    return result, _sha256(new_block.encode())


def _read_manifest(target: Path, target_fs: _TargetFS | None = None) -> dict | None:
    path = _target_file(target, MANIFEST_REL)
    data = target_fs.read_bytes(MANIFEST_REL) if target_fs else (path.read_bytes() if path.exists() else None)
    if data is None:
        return None
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"invalid install manifest: {exc}") from exc
    if manifest.get("schema_version") != "oio.install-manifest.v1":
        raise InstallError("unsupported or invalid OIO install manifest")
    return manifest


def _current_project_file(target: Path, project_id: str, namespace: str, default: dict, target_fs: _TargetFS | None = None) -> tuple[Path, bytes | None]:
    relative = ".oio/ontology/project.json"
    path = _target_file(target, relative)
    data = target_fs.read_bytes(relative) if target_fs else (path.read_bytes() if path.exists() else None)
    if data is not None:
        try:
            value = json.loads(data)
        except (OSError, json.JSONDecodeError) as exc:
            raise InstallError(f"existing project ontology is invalid JSON: {exc}") from exc
        validate_project_ontology(value, core=default)
        if value["project_id"] != project_id:
            raise InstallError(f"project ontology identifies {value['project_id']!r}, not target {project_id!r}")
        return path, None
    template = json.loads((DATA_ROOT / "ontology/project-template.json").read_text(encoding="utf-8"))
    template["project_id"] = project_id
    template["namespace"] = namespace
    template["extends"]["version"] = default["version"]
    for entry in template["priorities"]:
        entry["concept_id"] = entry["concept_id"].replace("project:", f"{namespace}:", 1)
    validate_project_ontology(template, core=default)
    return path, (json.dumps(template, indent=2, ensure_ascii=False) + "\n").encode()


def install(target_arg: str, project_id: str | None = None, check_only: bool = False) -> list[str]:
    target = _safe_target(target_arg)
    if target == SOURCE_ROOT and not (check_only and SOURCE_IS_ADOPTER):
        raise InstallError("the OIO source repository is not an adopter target")
    try:
        target_fs_context = _TargetFS(target)
    except OSError as exc:
        raise InstallError(f"could not safely open target repository: {exc}") from exc
    with target_fs_context as target_fs:
        return _install_in_target(target, target_fs, project_id, check_only)


def _install_in_target(target: Path, target_fs: _TargetFS, project_id: str | None, check_only: bool) -> list[str]:
    journal_path = _target_file(target, JOURNAL_REL)
    journal_bytes = target_fs.read_bytes(JOURNAL_REL)
    if journal_bytes is not None:
        if check_only:
            raise InstallError("an interrupted install needs recovery; run the installer to restore its previous state first")
        _recover_transaction(target, target_fs)
    project_id = project_id or _repo_from_remote(target)
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", project_id):
        raise InstallError("--project-id must be OWNER/REPOSITORY")
    namespace = re.sub(r"[^a-z0-9-]+", "-", project_id.lower()).strip("-")
    namespace = re.sub(r"-+", "-", namespace)[:63]
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,62}", namespace):
        namespace = ("project-" + namespace)[:63]
    manifest = _read_manifest(target, target_fs)
    old_files = manifest.get("managed_files", {}) if manifest else {}

    default = json.loads((DATA_ROOT / "ontology/default.json").read_text(encoding="utf-8"))
    _schema_validate(default, DATA_ROOT / "schemas/v1/default-ontology.schema.json")
    project_path, project_new = _current_project_file(target, project_id, namespace, default, target_fs)
    if check_only and project_new is not None:
        raise InstallError("installed project ontology is missing; run the installer to recover the scaffold")
    planned: dict[str, bytes] = {}
    for relative, source_relative in PACKAGE_FILES.items():
        destination = _target_file(target, relative)
        source = _source_file(source_relative)
        planned[relative] = _lf_bytes(source.read_bytes())
        if manifest and relative in old_files:
            installed_data = target_fs.read_bytes(relative)
            if installed_data is None or not _matches_recorded(installed_data, old_files[relative]):
                raise InstallError(f"managed file changed since OIO installed it: {relative}")
        elif target_fs.read_bytes(relative) is not None:
            raise InstallError(f"unmanaged file already exists at {relative}; refusing to adopt or overwrite it")

    agents_path = _target_file(target, "AGENTS.md")
    agents_bytes = target_fs.read_bytes("AGENTS.md")
    agents_existing = agents_bytes.decode("utf-8") if agents_bytes is not None else None
    old_agents_hash = manifest.get("agents_block_sha256") if manifest else None
    agents_content, agents_hash = _managed_agents(agents_existing, old_agents_hash)
    planned["AGENTS.md"] = agents_content.encode("utf-8")
    if project_new is not None:
        planned[".oio/ontology/project.json"] = project_new

    installed_hashes = {path: _sha256(data) for path, data in planned.items() if path != "AGENTS.md" and path != ".oio/ontology/project.json"}
    install_manifest = {
        "schema_version": "oio.install-manifest.v1",
        "oio_version": OIO_VERSION,
        "ontology_id": default["ontology_id"],
        "ontology_version": default["version"],
        "project_id": project_id,
        "namespace": namespace,
        "managed_files": installed_hashes,
        "agents_block_sha256": agents_hash,
        "project_extension": ".oio/ontology/project.json",
    }
    planned[MANIFEST_REL] = (json.dumps(install_manifest, indent=2, ensure_ascii=False) + "\n").encode()
    _target_file(target, MANIFEST_REL)
    if check_only:
        if not manifest:
            raise InstallError("OIO is not installed in this repository")
        if manifest.get("oio_version") != OIO_VERSION:
            raise InstallError("installed OIO files differ from this pinned package; run the installer to update")
        mismatches: list[str] = []
        eol_only: list[str] = []
        for path, data in planned.items():
            if path == ".oio/ontology/project.json":
                continue
            installed_data = target_fs.read_bytes(path)
            if installed_data == data:
                continue
            if installed_data is not None and _lf_bytes(installed_data) == _lf_bytes(data):
                eol_only.append(path)
            else:
                mismatches.append(path)
        if mismatches:
            raise InstallError(f"installed OIO files differ from this pinned package: {', '.join(mismatches)}; run the installer to update")
        if eol_only:
            return [f"VALID: OIO package, project ontology, and managed files match; line endings differ at {', '.join(eol_only)} and the content is otherwise identical — rerun the installer to normalize them to LF"]
        return ["VALID: OIO package, project ontology, and managed files match"]

    # Journal prior bytes before the first managed write. A subsequent invocation
    # restores these bytes if the process is interrupted at any point.
    entries: dict[str, dict[str, str | None]] = {}
    for relative in planned:
        before_bytes = target_fs.read_bytes(relative)
        before = base64.b64encode(before_bytes).decode("ascii") if before_bytes is not None else None
        entries[relative] = {"before": before, "planned_sha256": _sha256(planned[relative])}
    target_fs.atomic_write(JOURNAL_REL, (json.dumps({"schema_version": "oio.installer-transaction.v1", "entries": entries}, sort_keys=True) + "\n").encode())
    try:
        ordered = [relative for relative in planned if relative != MANIFEST_REL] + [MANIFEST_REL]
        for index, relative in enumerate(ordered):
            target_fs.atomic_write(relative, planned[relative])
            if os.environ.get("OIO_INSTALL_TEST_INTERRUPT_AFTER") == str(index + 1):
                raise SystemExit("simulated process interruption for recovery test")
    except Exception:
        _recover_transaction(target, target_fs)
        raise
    target_fs.unlink(JOURNAL_REL)
    return [f"Installed OIO {OIO_VERSION} for {project_id} in {target}", "Default project priority paths 1–100 are available; replace their generic definitions with project meanings and maintain the owner account map before relying on authority-ranked triage."]


def _posix_precondition() -> str:
    """Why the POSIX backend cannot run here, or ``""`` when it can.

    The single source of truth for the POSIX precondition: ``_PosixTargetFS``
    refuses with it, and ``platform_support`` reports it. A caller that asks
    ``--check-platform`` therefore cannot be told something the install would
    then refuse.
    """
    required = {os.open, os.mkdir, os.stat, os.unlink, os.rename}
    missing = sorted(f.__name__ for f in required - set(os.supports_dir_fd))
    if missing:
        return "os.supports_dir_fd lacks " + ", ".join(missing)
    for flag in ("O_NOFOLLOW", "O_DIRECTORY"):
        if not hasattr(os, flag):
            return f"os.{flag} is unavailable"
    return ""


def platform_support() -> tuple[bool, str]:
    """Whether this platform can host the installer, and the backend it uses.

    Derived from the same dispatch ``_TargetFS`` performs, so a caller's probe
    cannot claim support the install would not deliver — which is how a
    hard-coded copy of this check went stale when Windows support landed.
    """
    if sys.platform == "win32":
        return True, "Windows reparse-point backend"
    why = _posix_precondition()
    if why:
        return False, why
    return True, "descriptor-relative no-follow backend"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", help="absolute path to one existing Git target repository")
    parser.add_argument("--project-id", help="owner/repository; otherwise inferred from target origin")
    parser.add_argument("--check", action="store_true", help="validate the installed files without writing")
    parser.add_argument(
        "--check-platform",
        action="store_true",
        help="report whether this platform can host the installer, then exit (no target needed)",
    )
    args = parser.parse_args(argv)

    if args.check_platform:
        supported, backend = platform_support()
        print(f"{'supported' if supported else 'unsupported'}: {backend}")
        return 0 if supported else 1

    if not args.target:
        parser.error("--target is required unless --check-platform is given")
    try:
        for line in install(args.target, args.project_id, args.check):
            print(line)
    except (InstallError, OSError) as exc:
        print(f"OIO install refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
