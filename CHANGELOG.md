# Changelog

All notable changes to `delft3d-mcp` will be documented in this file.

## [0.1.0] - 2026-11-23

### Added
- Hybrid Polyglot architecture: Rust PyO3 core (`delft3d_core`) + Python 3.12 FastMCP host.
- Rust modules for `.grd`/`.enc`, TEKAL (`.ldb`, `.spl`, `.pol`), TFI curvilinear generator, Winslow elliptic grid orthogonalizer, `.dep`/`.xyz` parallel interpolation, native NEFIS `.dat`/`.def` reader, and non-blocking `tri-diag` log tailer.
- Domain file engines for FLOW, WAVE, WAQ, PART, TIDE/WES, and boundaries.
- Direct kernel runner with MPI `-localonly`, coupled FLOW+WAVE (`wave.exe <mdw> 1`), pre-run CFL/dimension validator, and job concurrency limiter.
- Smart Windows GUI bridge (`PrintWindow` + `mss`) for Qt5 (`d3d_menu`, `mfe_app` RGFGRID/QUICKIN + calibrated canvas actions, `d3d_qp`) and Tcl/Tk lifecycle capture.
- 45 modular MCP tools, 4 resources, 3 prompts, CI workflow, and automated E2E verification.
