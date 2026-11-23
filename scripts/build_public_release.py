"""Build binary-only public release wheel (zero Rust/Python business source code)."""

from __future__ import annotations

import base64
import compileall
import hashlib
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def _sha256_b64_nopad(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def build_public_release(repo_root: Path) -> dict[str, object]:
    dist_raw = repo_root / "dist" / "raw_wheels"
    dist_pub = repo_root / "dist" / "public"
    if dist_raw.exists():
        shutil.rmtree(dist_raw)
    dist_raw.mkdir(parents=True, exist_ok=True)
    dist_pub.mkdir(parents=True, exist_ok=True)

    # 1. Build release wheel via maturin (Rust compiled with strip=true, lto=thin)
    subprocess.run(
        [
            "uv",
            "run",
            "maturin",
            "build",
            "--release",
            "--out",
            str(dist_raw),
        ],
        cwd=str(repo_root),
        check=True,
    )

    wheels = list(dist_raw.glob("delft3d_mcp-*.whl"))
    if not wheels:
        raise FileNotFoundError("maturin build did not produce any wheel file.")
    src_whl = wheels[0]

    with tempfile.TemporaryDirectory() as tmpdir:
        stage = Path(tmpdir)
        with zipfile.ZipFile(src_whl, "r") as zf:
            zf.extractall(stage)

        pkg_dir = stage / "delft3d_mcp"
        # Remove internal type stub
        for pyi in pkg_dir.rglob("*.pyi"):
            pyi.unlink()

        # Compile all Python modules with optimize=2 (strips docstrings and asserts) into legacy .pyc locations
        ok = compileall.compile_dir(
            str(pkg_dir),
            force=True,
            quiet=1,
            legacy=True,
            optimize=2,
        )
        if not ok:
            raise RuntimeError("Failed to compile Python modules to bytecode.")

        # Remove all business-logic .py source files (keep only minimal __init__.py and __main__.py entrypoints)
        keep_py = {"__init__.py", "__main__.py"}
        removed_py_count = 0
        for py_file in list(pkg_dir.rglob("*.py")):
            if py_file.parent == pkg_dir and py_file.name in keep_py:
                continue
            py_file.unlink()
            removed_py_count += 1

        # Remove any leftover __pycache__ folders
        for cache_dir in list(pkg_dir.rglob("__pycache__")):
            shutil.rmtree(cache_dir, ignore_errors=True)

        # Regenerate PEP 427 RECORD
        dist_info_dirs = list(stage.glob("delft3d_mcp-*.dist-info"))
        if not dist_info_dirs:
            raise RuntimeError("Missing .dist-info directory in wheel.")
        dist_info = dist_info_dirs[0]
        record_path = dist_info / "RECORD"

        record_lines: list[str] = []
        for file_p in sorted(stage.rglob("*")):
            if not file_p.is_file() or file_p == record_path:
                continue
            rel = file_p.relative_to(stage).as_posix()
            raw_bytes = file_p.read_bytes()
            h = _sha256_b64_nopad(raw_bytes)
            record_lines.append(f"{rel},sha256={h},{len(raw_bytes)}")
        record_lines.append(f"{record_path.relative_to(stage).as_posix()},,")
        record_path.write_text("\n".join(record_lines) + "\n", encoding="utf-8")

        out_whl = dist_pub / src_whl.name
        if out_whl.exists():
            out_whl.unlink()
        with zipfile.ZipFile(out_whl, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for file_p in sorted(stage.rglob("*")):
                if file_p.is_file():
                    zout.write(file_p, file_p.relative_to(stage).as_posix())

    # Audit gate: verify zero .rs files and zero business-logic .py source files inside public wheel
    with zipfile.ZipFile(out_whl, "r") as zaudit:
        names = zaudit.namelist()
        rs_files = [n for n in names if n.endswith(".rs")]
        leaked_py = [
            n
            for n in names
            if n.endswith(".py")
            and n not in {"delft3d_mcp/__init__.py", "delft3d_mcp/__main__.py"}
        ]
        pyd_files = [n for n in names if n.endswith(".pyd")]
        pyc_files = [n for n in names if n.endswith(".pyc")]

    if rs_files or leaked_py or not pyd_files:
        raise AssertionError(
            f"Source leak detected in public wheel! rs={rs_files}, py={leaked_py}, pyd={pyd_files}"
        )

    # Copy public companion docs & 1-click installer
    for doc_name in ("README.md", "LICENSE", "SECURITY.md", "CHANGELOG.md"):
        src_doc = repo_root / doc_name
        if src_doc.exists():
            shutil.copy2(src_doc, dist_pub / doc_name)

    installer_ps1 = dist_pub / "install_public.ps1"
    installer_ps1.write_text(
        '$ErrorActionPreference = "Stop"\n'
        '$whl = Get-ChildItem -Path $PSScriptRoot -Filter "delft3d_mcp-*.whl" | Select-Object -First 1\n'
        'if (-not $whl) { throw "Wheel file not found in $PSScriptRoot" }\n'
        'Write-Host "Installing $($whl.Name) into isolated uv environment..."\n'
        'uv tool install --python 3.12 --force $whl.FullName\n'
        'Write-Host "Successfully installed delft3d-mcp! Run Command: delft3d-mcp"\n',
        encoding="utf-8",
    )

    shutil.rmtree(dist_raw, ignore_errors=True)
    return {
        "public_wheel": str(out_whl),
        "wheel_size_bytes": out_whl.stat().st_size,
        "pyd_binaries": pyd_files,
        "compiled_pyc_modules": len(pyc_files),
        "stripped_py_sources": removed_py_count,
        "leaked_rs_sources": len(rs_files),
        "leaked_business_py_sources": len(leaked_py),
    }


if __name__ == "__main__":
    import json

    root = Path(__file__).resolve().parent.parent
    summary = build_public_release(root)
    print(json.dumps(summary, indent=2))
