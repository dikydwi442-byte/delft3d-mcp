"""Async job manager for Delft3D kernels (FLOW, MPI, coupled FLOW+WAVE, WAQ, PART, utilities)."""

from __future__ import annotations

import json
import logging
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from delft3d_mcp.config import STATE_DIR, Delft3DPaths
from delft3d_mcp.runner.env import build_kernel_env, generate_config_d_hydro_xml

logger = logging.getLogger("delft3d_mcp.runner")

MAX_CONCURRENT_JOBS = 2


@dataclass
class SimJob:
    job_id: str
    mode: str
    work_dir: str
    command: list[str]
    log_path: str
    status: str = "running"
    pids: list[int] = field(default_factory=list)
    exit_code: int | None = None
    started_at: str = field(
        default_factory=lambda: datetime.now(tz=UTC).isoformat()
    )
    finished_at: str | None = None
    companion_command: list[str] | None = None
    diag_file: str | None = None


class JobManager:
    """Manages non-blocking Delft3D simulation processes with zero stdout pollution."""

    def __init__(self, paths: Delft3DPaths, registry_path: Path | None = None) -> None:
        self.paths = paths
        self.registry_path = registry_path or (STATE_DIR / "jobs.json")
        self._jobs: dict[str, SimJob] = {}
        self._handles: dict[str, list[subprocess.Popen[bytes]]] = {}
        self._load()

    def _load(self) -> None:
        if self.registry_path.exists():
            try:
                raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
                for jid, payload in raw.items():
                    self._jobs[jid] = SimJob(**payload)
            except (json.JSONDecodeError, OSError, TypeError):
                self._jobs = {}

    def _save(self) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {jid: asdict(job) for jid, job in self._jobs.items()}
        tmp = self.registry_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.registry_path)

    def _refresh_job(self, job: SimJob) -> None:
        if job.status != "running":
            return
        handles = self._handles.get(job.job_id)
        if handles:
            codes = [h.poll() for h in handles]
            if all(c is not None for c in codes):
                job.exit_code = codes[0]
                job.status = "completed" if all(c == 0 for c in codes) else "failed"
                job.finished_at = datetime.now(tz=UTC).isoformat()
                self._save()
            return

        # Fallback: check OS PIDs if server restarted
        alive = any(psutil.pid_exists(pid) for pid in job.pids)
        if not alive:
            job.status = "exited"
            job.finished_at = datetime.now(tz=UTC).isoformat()
            self._save()

    def active_running_count(self) -> int:
        cnt = 0
        for job in self._jobs.values():
            self._refresh_job(job)
            if job.status == "running":
                cnt += 1
        return cnt

    def start_simulation(
        self,
        mode: str,
        work_dir: Path,
        input_file: Path,
        wave_mdw_file: Path | None = None,
        num_procs: int = 1,
        single_precision: bool = False,
        extra_args: list[str] | None = None,
    ) -> SimJob:
        if self.active_running_count() >= MAX_CONCURRENT_JOBS:
            raise RuntimeError(
                f"Concurrent job limit ({MAX_CONCURRENT_JOBS}) reached. Wait or stop an active job."
            )

        job_id = uuid.uuid4().hex[:10]
        jobs_dir = work_dir / ".d3d_mcp_jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        log_path = jobs_dir / f"{job_id}.log"

        env = build_kernel_env(self.paths)
        rel_input = input_file.name
        diag_file: str | None = None
        companion_cmd: list[str] | None = None

        if mode in {"flow_single", "flow_mpi", "flow_dd", "flow_wave_coupled"}:
            cfg_path = work_dir / "config_d_hydro.xml"
            is_ddb = input_file.suffix.lower() == ".ddb"
            xml_txt = generate_config_d_hydro_xml(
                mdf_name=None if is_ddb else rel_input,
                ddb_name=rel_input if is_ddb else None,
                single_precision=single_precision,
                enable_delft_online=False,
            )
            cfg_path.write_text(xml_txt, encoding="iso-8859-1")
            diag_file = str(work_dir / f"tri-diag.{input_file.stem}")

            if mode in {"flow_mpi", "flow_wave_coupled"} and num_procs > 1:
                cmd = [
                    str(self.paths.mpiexec),
                    "-n",
                    str(num_procs),
                    "-localonly",
                    str(self.paths.d_hydro),
                    cfg_path.name,
                ]
            else:
                cmd = [str(self.paths.d_hydro), cfg_path.name]

            if mode == "flow_wave_coupled":
                if wave_mdw_file is None:
                    raise ValueError("wave_mdw_file is required for flow_wave_coupled mode.")
                # Verified from run_dflow2d3d_parallel_dwaves.bat: wave.exe <mdw> 1
                companion_cmd = [str(self.paths.wave_exe), wave_mdw_file.name, "1"]

        elif mode == "wave_standalone":
            # Verified from run_dwaves.bat: wave.exe <mdw> 0
            cmd = [str(self.paths.wave_exe), rel_input, "0"]
        elif mode == "waq":
            cmd = [str(self.paths.delwaq_exe), rel_input, *(extra_args or [])]
        elif mode == "part":
            arg = [] if rel_input.lower() == "runid.par" else [rel_input]
            cmd = [str(self.paths.delpar_exe), *arg, *(extra_args or [])]
        else:
            raise ValueError(f"Unsupported simulation mode: {mode}")

        log_fh = log_path.open("ab")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        handles: list[subprocess.Popen[bytes]] = []
        try:
            p1 = subprocess.Popen(
                cmd,
                cwd=str(work_dir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                shell=False,
                creationflags=creationflags,
            )
            handles.append(p1)
            if companion_cmd is not None:
                comp_log = (jobs_dir / f"{job_id}_wave.log").open("ab")
                p2 = subprocess.Popen(
                    companion_cmd,
                    cwd=str(work_dir),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=comp_log,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    creationflags=creationflags,
                )
                handles.append(p2)
                comp_log.close()
        finally:
            log_fh.close()

        job = SimJob(
            job_id=job_id,
            mode=mode,
            work_dir=str(work_dir),
            command=cmd,
            companion_command=companion_cmd,
            log_path=str(log_path),
            pids=[h.pid for h in handles],
            diag_file=diag_file,
        )
        self._jobs[job_id] = job
        self._handles[job_id] = handles
        self._save()
        logger.info("Started job %s (%s) in %s", job_id, mode, work_dir)
        return job

    def get_status(self, job_id: str | None = None) -> dict[str, Any]:
        if job_id:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(f"Job not found: {job_id}")
            self._refresh_job(job)
            return asdict(job)
        for j in self._jobs.values():
            self._refresh_job(j)
        return {"jobs": [asdict(j) for j in self._jobs.values()]}

    def stop_job(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if not job:
            raise KeyError(f"Job not found: {job_id}")
        for pid in job.pids:
            try:
                proc = psutil.Process(pid)
                for child in proc.children(recursive=True):
                    child.terminate()
                proc.terminate()
            except psutil.NoSuchProcess:
                pass
        job.status = "stopped"
        job.finished_at = datetime.now(tz=UTC).isoformat()
        self._save()
        return asdict(job)

    def run_utility_sync(
        self,
        utility: str,
        work_dir: Path,
        args: list[str],
        timeout_sec: int = 120,
    ) -> dict[str, Any]:
        """Run short Delft3D CLI utilities synchronously with captured output."""
        exe_map: dict[str, Path] = {
            "agrhyd": self.paths.agrhyd_exe,
            "ddcouple": self.paths.ddcouple_exe,
            "mormerge": self.paths.mormerge_exe,
            "nesthd1": self.paths.nesthd1_exe,
            "nesthd2": self.paths.nesthd2_exe,
            "wes": self.paths.wes_exe,
            "tide_analysis": self.paths.tide_analysis_exe,
            "tide_predict": self.paths.tide_predict_exe,
            "maptonetcdf": self.paths.maptonetcdf_exe,
            "sim2ugrid": self.paths.sim2ugrid_exe,
            "delft3d2shape": self.paths.delft3d2shape_exe,
            "delwaq2raster": self.paths.delwaq2raster_exe,
            "vs": self.paths.vs_exe,
        }
        exe = exe_map.get(utility.lower())
        if not exe or not exe.exists():
            raise FileNotFoundError(f"Utility '{utility}' not found at {exe}")

        env = build_kernel_env(self.paths)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            [str(exe), *args],
            cwd=str(work_dir),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            shell=False,
            creationflags=creationflags,
        )
        return {
            "utility": utility,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
        }
