"""Atomic file writes and timestamped backup snapshots for safe model edits."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

BACKUP_DIR_NAME = ".d3d_mcp_backups"


def create_backup(file_path: Path, workspace_root: Path, label: str = "edit") -> Path | None:
    """Snapshot an existing file before mutation into <workspace>/.d3d_mcp_backups/<ts>/."""
    if not file_path.exists() or not file_path.is_file():
        return None
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S_%fZ")
    rel = file_path.resolve().relative_to(workspace_root.resolve())
    backup_target = workspace_root / BACKUP_DIR_NAME / f"{ts}_{label}" / rel
    backup_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(file_path, backup_target)
    return backup_target


def atomic_write_text(
    file_path: Path,
    content: str,
    workspace_root: Path,
    backup_label: str = "edit",
    encoding: str = "utf-8",
) -> Path | None:
    """Back up target if present and write new content atomically."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = create_backup(file_path, workspace_root, label=backup_label)
    tmp_path = file_path.with_name(f".{file_path.name}.tmp")
    tmp_path.write_text(content, encoding=encoding)
    tmp_path.replace(file_path)
    return backup_path


def list_backups(workspace_root: Path) -> list[dict[str, str]]:
    """List available backup snapshots inside the workspace."""
    broot = workspace_root / BACKUP_DIR_NAME
    if not broot.exists():
        return []
    snapshots: list[dict[str, str]] = []
    for entry in sorted(broot.iterdir(), reverse=True):
        if entry.is_dir():
            files = [
                str(p.relative_to(entry))
                for p in entry.rglob("*")
                if p.is_file()
            ]
            snapshots.append({"snapshot_id": entry.name, "files": ", ".join(files)})
    return snapshots


def restore_backup(workspace_root: Path, snapshot_id: str) -> list[str]:
    """Restore all files from a named backup snapshot."""
    snap_dir = (workspace_root / BACKUP_DIR_NAME / snapshot_id).resolve()
    broot = (workspace_root / BACKUP_DIR_NAME).resolve()
    if not snap_dir.is_relative_to(broot) or not snap_dir.is_dir():
        raise FileNotFoundError(f"Backup snapshot not found: {snapshot_id}")

    restored: list[str] = []
    for src in snap_dir.rglob("*"):
        if src.is_file():
            rel = src.relative_to(snap_dir)
            dst = (workspace_root / rel).resolve()
            if not dst.is_relative_to(workspace_root.resolve()):
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored.append(str(rel))
    return restored
