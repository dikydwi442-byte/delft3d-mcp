# delft3d-mcp

Production-grade **Hybrid Polyglot Model Context Protocol (MCP)** server for **Deltares Delft3D 4.07.02** on Windows.

## Architecture

1. **Rust PyO3 Core (`delft3d_core` via `maturin` + `rayon` + `memmap2`)**
   - Fast parser/writer for RGFGRID (`.grd`, `.enc`), TEKAL (`.ldb`, `.spl`, `.pol`), Transfinite Interpolation (TFI) curvilinear grid generator, Winslow elliptic grid orthogonalizer, and parallel grid quality metrics.
   - Parallel QUICKIN (`.dep`, `.xyz`) IDW/nearest interpolation and Laplace smoothing/gap filling.
   - Native memory-mapped NEFIS binary reader (`.dat`/`.def` for `trim-*`, `trih-*`, `wavm-*`).
   - Non-blocking `tri-diag` / log scanner.
2. **Python 3.12 MCP Host (`fastmcp` + `numpy`/`scipy`/`netCDF4`/`pydantic`)**
   - 45 modular tools across 7 Delft3D domains + 4 resources + 3 workflow prompts.
   - Strict Workspace Sandbox Guard, Atomic Writes, Timestamped Auto-Backups & Rollback.
   - Zero `stdout` leakage (all logs routed to `stderr` + rotating log file; subprocesses write to job logs).
3. **Direct Native Kernel Runner & Smart Windows GUI Bridge**
   - Executes `d_hydro.exe`, `mpiexec.exe -localonly`, `wave.exe` (`0` standalone / `1` online coupled), `delwaq.exe`, `delpar.exe`, and utilities directly (`shell=False`) without batch path-quoting fragility.
   - Qt5 UIA & Win32/`mss` screenshot bridge for `d3d_menu.exe`, `mfe_app.exe` (`RGFGRID`/`QUICKIN`/`DIDO`), `d3d_qp.exe`, and Tcl/Tk lifecycle control.

## Quick Setup (Windows)

```powershell
uv sync --all-groups
uv run pytest tests/ -v
uv run python scripts/smoke_mcp_stdio.py
```

## MCP Client Configuration (`mcp_config.json`)

```json
{
  "mcpServers": {
    "delft3d": {
      "command": "uv",
      "args": ["--directory", "D:\\AI Project\\delft3d-mcp", "run", "delft3d-mcp"]
    }
  }
}
```
