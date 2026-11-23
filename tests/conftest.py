"""Pytest configuration and shared fixtures for delft3d-mcp."""

from __future__ import annotations

from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "requires_delft3d: tests executing local Delft3D 4.07.02 binaries")
    config.addinivalue_line("markers", "requires_gui: tests launching interactive Windows GUI windows")


@pytest.fixture()
def tmp_workspace(tmp_path: Path):
    from delft3d_mcp.config import WorkspaceRegistry

    state_file = tmp_path / "state" / "workspaces.json"
    reg = WorkspaceRegistry(state_file=state_file)
    ws_dir = tmp_path / "model_ws"
    reg.register(ws_dir, name="test_ws", create=True)
    return reg, ws_dir
