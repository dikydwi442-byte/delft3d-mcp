# Security Policy

## Workspace Sandbox & Data Protection

`delft3d-mcp` enforces strict isolation for all filesystem and subprocess operations:

1. **Workspace Allowlist:** File mutations and simulation runs are restricted to explicitly registered project directories (`project_init_workspace`). Path traversal (`..`, symlinks/junctions escaping root) is rejected.
2. **Atomic Write & Auto-Backup:** Every mutation to Delft3D input files (`.mdf`, `.grd`, `.dep`, `.mdw`, `.inp`, boundaries) creates a timestamped snapshot in `<project>/.d3d_mcp_backups/` before writing via atomic temp-file replacement.
3. **No Shell Execution:** All native Deltares binaries (`d_hydro.exe`, `mpiexec.exe`, `wave.exe`, `delwaq.exe`, `delpar.exe`) are spawned directly with `shell=False` and validated argument vectors.
4. **Stdio Protocol Integrity:** Server logging is strictly routed to `stderr` and rotating log files; subprocess `stdout`/`stderr` are redirected to job log files so MCP JSON-RPC `stdout` framing cannot be corrupted.
5. **No Third-Party Proprietary/GPL Artifacts:** This repository contains original MCP integration code only and invokes the user's local Delft3D installation dynamically via `D3D_HOME`.
