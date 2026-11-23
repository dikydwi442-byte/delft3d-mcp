"""Security tests: workspace sandbox, path traversal block, auto-backup, and stdout hygiene."""

from __future__ import annotations

from pathlib import Path

import pytest

from delft3d_mcp.backup import atomic_write_text, list_backups, restore_backup
from delft3d_mcp.config import DEFAULT_D3D_ROOT, SecurityError
from delft3d_mcp.logging_setup import setup_logging


def test_sandbox_blocks_traversal_and_protected_dirs(tmp_workspace, tmp_path: Path) -> None:
    reg, ws_dir = tmp_workspace
    with pytest.raises(SecurityError):
        reg.resolve_safe_path("../../outside.txt")

    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(SecurityError):
        reg.resolve_safe_path(str(outside))

    if DEFAULT_D3D_ROOT.exists():
        with pytest.raises(SecurityError):
            reg.register(DEFAULT_D3D_ROOT)

    with pytest.raises(SecurityError):
        reg.register("C:\\")

    safe, root = reg.resolve_safe_path("sub/model.mdf")
    assert root == ws_dir.resolve()
    assert safe == (ws_dir / "sub" / "model.mdf").resolve()


def test_atomic_backup_and_rollback(tmp_workspace) -> None:
    _, ws_dir = tmp_workspace
    mdf = ws_dir / "case.mdf"
    atomic_write_text(mdf, "Ident = #v1#\n", ws_dir, backup_label="init")
    assert mdf.read_text(encoding="utf-8") == "Ident = #v1#\n"

    atomic_write_text(mdf, "Ident = #v2#\n", ws_dir, backup_label="update")
    assert mdf.read_text(encoding="utf-8") == "Ident = #v2#\n"

    snaps = list_backups(ws_dir)
    assert len(snaps) == 1
    restored = restore_backup(ws_dir, snaps[0]["snapshot_id"])
    assert "case.mdf" in restored
    assert mdf.read_text(encoding="utf-8") == "Ident = #v1#\n"


def test_logger_never_writes_to_stdout(capsys, tmp_path: Path) -> None:
    logger = setup_logging(log_dir=tmp_path / "logs")
    logger.info("test_stdio_hygiene_message")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "test_stdio_hygiene_message" in captured.err
