# Security policy

`delft3d-mcp` limits all file edits and subprocess launches to registered workspaces.

Reads, writes, and simulation runs only execute inside folders registered with `project_init_workspace`. Paths using `..` or symlinks pointing outside the workspace root are rejected, and system directories or drive roots (`C:\`, `C:\Windows`, `C:\Program Files`, and the Delft3D installation path) cannot be registered.

Before modifying `.mdf`, `.grd`, `.dep`, `.mdw`, `.inp`, or boundary files, the server copies the original file to `<project>/.d3d_mcp_backups/` and writes updates via temporary file replacement.

Deltares executables (`d_hydro.exe`, `mpiexec.exe`, `wave.exe`, `delwaq.exe`, `delpar.exe`) are spawned directly with `shell=False`. Server diagnostics go to `stderr` and rotating log files while kernel output goes to per-job log files so MCP `stdio` JSON-RPC framing cannot be corrupted. When stopping a running job, `JobManager` checks the PID's executable name against a Delft3D kernel allowlist before sending termination signals.

This package contains integration code only and invokes the user's local Delft3D installation dynamically.
