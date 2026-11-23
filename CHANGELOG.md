# Changelog

All notable changes to `delft3d-mcp` will be documented in this file.

## [1.0.0] - 2026-11-23

### Added
- Multi-Version & Multi-Layout Delft3D 4.x Auto-Discovery (`4.01` classic `x64`/`w32` through `4.07.02+` modern `kernels`/`guis` split, plus `headless_file_only` mode).
- Binary-only public distribution builder (`scripts/build_public_release.py`) generating zero-source-leak precompiled wheels (`dist/public/`).
- Visual architecture banner (`assets/banner.svg`) and comprehensive public documentation with compatibility matrix and 48-tool reference table.
- Extended `sim_run_utility` support for `plct`, `nestwq1`, `nestwq2`, `waqmerge`, `triana`, `kubint`, `lint`, and `datsel`.

### Fixed
- Fixed 1-based leaf pointer offset calculation (`vd_1based = vd_index_0based + 1`) in `nefis.rs` so time-step `0` (`vd=0`) reads actual initial values instead of `NaN` and all `vd_total` steps are accessible.
- Fixed `m2b` coordinate formatting typo in `format_ddb` (`domain/mdf.py`).
- Fixed numeric string quoting in `_format_mdf_value` (`domain/mdf.py`) so numeric keywords passed as strings (e.g. `Dt="0.5"`) are not wrapped in `#...#`.
- Optimized parallel IDW interpolation in `dep.rs` to use bounded $O(K)$ neighbor buffers per grid node instead of full-sample heap allocations.
- Hardened `JobManager` (`runner/job_manager.py`) with companion-process rollback on spawn failure and Windows PID-reuse verification against `ALLOWED_KERNEL_PROCESS_NAMES`.

### Changed
- Removed unused `xarray` dependency (~50 MB transitive reduction), removed dead `matlab_vs_let_bridge` function, and consolidated thin 1-function wrapper files (`wave.py`, `gis.py`, `tcltk_adapter.py`).

## [0.2.0] - 2026-11-23

### Added
- Full QUICKIN Operations parity: TIN (Delaunay) & Grid Cell Averaging interpolation, `bathy_operations` (`volumes_areas`, `combine`, `samples_from_tekal`).
- RGFGRID parity: `grid_transform_or_mask` (shift/scale/rotate + block delete interior/exterior).
- FLOW `.bcc`, `.bch`, `.src`/`.dis` discharge support, least-squares tidal harmonic fit & prediction, and Delft3D-RTC config generator.
- Direct NEFIS-to-NetCDF4 exporter (`nefis_to_netcdf`) and skill metrics (`post_validate_skill`).
- Qt5 UIA `CheckBox` toolbar action support (`uia_toggle`/`uia_invoke`), `read_status_bar`, and `Win32 PostMessage` coordinate click fallback.

## [0.1.0] - 2026-11-23

### Added
- Initial Hybrid Polyglot architecture: Rust PyO3 core (`delft3d_core`) + Python 3.12 FastMCP host.
