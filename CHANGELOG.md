# Changelog

All notable changes to `delft3d-mcp` are recorded in this file.

## [1.0.0] - 2026-11-23

### Added
- Multi-version Delft3D 4.x discovery covering `4.01` classic (`x64`/`w32`) through `4.07.02+` (`kernels`/`guis`), plus a `headless_file_only` mode when no local installation is present.
- Binary-only release builder (`scripts/build_public_release.py`) that outputs a compiled wheel in `dist/public/` without Rust or Python business source files.
- Updated repository banner (`assets/banner.svg`) and documentation with compatibility tables and all 48 MCP tools.
- Added `plct`, `nestwq1`, `nestwq2`, `waqmerge`, `triana`, `kubint`, `lint`, and `datsel` to `sim_run_utility`.

### Fixed
- Fixed 1-based leaf pointer offset (`vd_1based = vd_index_0based + 1`) in `nefis.rs` so step 0 (`vd=0`) reads initial field values instead of `NaN` and all time steps remain accessible.
- Fixed the `m2b` coordinate field typo in `format_ddb` (`domain/mdf.py`).
- Stopped `_format_mdf_value` (`domain/mdf.py`) from wrapping numeric strings (such as `Dt="0.5"`) in `#...#` delimiters.
- Replaced full-sample `Vec` allocations per grid cell in `dep.rs` with bounded `O(K)` top-K neighbor buffers for parallel IDW.
- Added companion-process rollback on spawn errors and PID executable name checks in `JobManager` (`runner/job_manager.py`).

### Changed
- Dropped the unused `xarray` dependency and its transitive packages (`pandas`, `cftime`, `tzdata`) from `pyproject.toml`.
- Removed dead code (`matlab_vs_let_bridge`) and merged single-function wrapper modules (`wave.py`, `gis.py`, `tcltk_adapter.py`) into their parent domain/post/gui modules.

## [0.2.0] - 2026-11-23

### Added
- Added TIN (Delaunay) and `cell_average` bathymetry interpolation along with `bathy_operations` (`volumes_areas`, `combine`, `samples_from_tekal`).
- Added `grid_transform_or_mask` (affine transforms and `[m1, m2, n1, n2]` block masking).
- Added `.bcc`, `.bch`, `.src`/`.dis` discharge formatting, least-squares tidal harmonic fitting, and RTC config output.
- Added direct NEFIS-to-NetCDF4 export (`nefis_to_netcdf`) and `post_validate_skill`.
- Added Qt5 toolbar `CheckBox` toggling (`uia_toggle`/`uia_invoke`), status bar reading, and Win32 `PostMessage` click fallback.

## [0.1.0] - 2026-11-23

### Added
- Initial Rust PyO3 core (`delft3d_core`) and Python 3.12 FastMCP server.
