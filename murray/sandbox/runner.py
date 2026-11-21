"""Run one model-written script in a throwaway directory under hard limits, and collect the images it wrote.

Layers, outermost first: the pod (no network, no secrets, read-only root, tmpfs scratch, its own uid, cgroup limits);
this runner (fresh directory per job, `python -I`, minimal environment, rlimits, process-group kill on a wall clock);
the AST policy (policy.py). Whatever comes back is still treated as untrusted by the caller.
"""

from __future__ import annotations

import asyncio
import base64
import os
import resource
import shutil
import signal
import stat
import sys
import tempfile
import time
from pathlib import Path

from . import policy

WALL_S = int(os.environ.get("MURRAY_SANDBOX_WALL_S", "45"))
WALL_FRAMES_S = int(os.environ.get("MURRAY_SANDBOX_WALL_FRAMES_S", "90"))
CPU_S = int(os.environ.get("MURRAY_SANDBOX_CPU_S", "100"))
MEM_BYTES = int(os.environ.get("MURRAY_SANDBOX_MEM_BYTES", str(3 * 1024**3)))
FSIZE_BYTES = 32 * 1024**2
MAX_FRAMES = 40
MAX_FILE_BYTES = 16 * 1024**2
MAX_TOTAL_BYTES = 48 * 1024**2
FONT_CACHE = Path("/opt/mpl")



def _limits() -> None:
    """Applied in the child between fork and exec."""
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_S, CPU_S + 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (FSIZE_BYTES, FSIZE_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    resource.setrlimit(resource.RLIMIT_AS, (MEM_BYTES, MEM_BYTES))


def _read_regular(path: Path) -> bytes | None:
    """The bytes of `path` only if it is an ordinary file of sane size (never follows a link)."""
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size <= 0 or st.st_size > MAX_FILE_BYTES:
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            return os.read(fd, MAX_FILE_BYTES + 1)[:MAX_FILE_BYTES]
        finally:
            os.close(fd)
    except OSError:
        return None


async def run_job(code: str, frames: bool = False) -> dict:
    """Returns {status, error, stderr_tail, stdout_tail, wall_ms, exit_code, files:[{name,b64}]}.
    status: ok | policy | error | timeout | no_output."""
    violations = policy.check(code)
    if violations:
        return {"status": "policy", "error": policy.describe(violations), "stderr_tail": "", "stdout_tail": "",
                "wall_ms": 0, "exit_code": None, "files": []}
    wall = WALL_FRAMES_S if frames else WALL_S
    job = Path(tempfile.mkdtemp(prefix="job-"))
    t0 = time.monotonic()
    try:
        (job / "frames").mkdir()
        if FONT_CACHE.is_dir():
            shutil.copytree(FONT_CACHE, job / "mpl", dirs_exist_ok=True)
        else:
            (job / "mpl").mkdir()
        (job / "main.py").write_text(code, encoding="utf-8")   # MPLBACKEND=Agg comes from the environment
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(job), "MPLCONFIGDIR": str(job / "mpl"),
            "MPLBACKEND": "Agg", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "LANG": "C.UTF-8",
        }
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-B", "main.py", cwd=str(job), env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, preexec_fn=_limits, start_new_session=True)
        timed_out = False
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=wall)
        except asyncio.TimeoutError:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            out, err = await proc.communicate()
        wall_ms = int((time.monotonic() - t0) * 1000)
        stderr_tail = err.decode("utf-8", "replace")[-1500:]
        stdout_tail = out.decode("utf-8", "replace")[-600:]
        files: list[dict] = []
        total = 0
        single = _read_regular(job / "out.png")
        if single is not None:
            files.append({"name": "out.png", "b64": base64.b64encode(single).decode("ascii")})
            total += len(single)
        else:
            names = sorted(p.name for p in (job / "frames").glob("frame_*.png"))[:MAX_FRAMES]
            for name in names:
                data = _read_regular(job / "frames" / name)
                if data is None or total + len(data) > MAX_TOTAL_BYTES:
                    continue
                files.append({"name": f"frames/{name}", "b64": base64.b64encode(data).decode("ascii")})
                total += len(data)
        if timed_out:
            status, error = "timeout", f"the script was stopped after {wall} seconds of wall time"
        elif proc.returncode != 0:
            status, error = "error", f"the script exited with code {proc.returncode}"
        elif not files:
            status, error = "no_output", "the script finished but wrote neither out.png nor frames/frame_NNN.png"
        else:
            status, error = "ok", ""
        return {"status": status, "error": error, "stderr_tail": stderr_tail, "stdout_tail": stdout_tail,
                "wall_ms": wall_ms, "exit_code": proc.returncode, "files": files}
    finally:
        shutil.rmtree(job, ignore_errors=True)
