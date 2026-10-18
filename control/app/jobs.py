"""Long-running shell jobs (compose build/up) with output buffered for live streaming."""
import asyncio
import itertools
import time

from .config import STACK_DIR

_ids = itertools.count(1)
JOBS: dict = {}


class Job:
    def __init__(self, title, steps):
        self.id = str(next(_ids))
        self.title = title
        self.steps = steps  # list of argv lists, run sequentially, stop on failure
        self.lines = []
        self.done = False
        self.ok = None
        self.started = time.time()
        self._cond = asyncio.Condition()

    async def _emit(self, line):
        async with self._cond:
            self.lines.append(line)
            if len(self.lines) > 5000:
                del self.lines[:1000]
            self._cond.notify_all()

    async def run(self):
        try:
            for argv in self.steps:
                await self._emit(f"$ {' '.join(argv)}")
                proc = await asyncio.create_subprocess_exec(
                    *argv, cwd=STACK_DIR, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)  # COMPOSE_FILE paths are relative to cwd
                async for raw in proc.stdout:
                    await self._emit(raw.decode(errors="replace").rstrip("\n"))
                rc = await proc.wait()
                if rc != 0:
                    await self._emit(f"✗ step failed (exit {rc})")
                    self.ok = False
                    return
            self.ok = True
            await self._emit("✓ done")
        except Exception as e:  # noqa: BLE001
            self.ok = False
            await self._emit(f"✗ {e}")
        finally:
            self.done = True
            async with self._cond:
                self._cond.notify_all()

    async def follow(self):
        i = 0
        while True:
            async with self._cond:
                while i >= len(self.lines) and not self.done:
                    await self._cond.wait()
                chunk, i = self.lines[i:], len(self.lines)
                finished = self.done and i >= len(self.lines)
            for line in chunk:
                yield line
            if finished:
                return


def start(title, steps):
    running = [j for j in JOBS.values() if not j.done]
    if running:
        raise RuntimeError(f"Already running: {running[0].title}")
    job = Job(title, steps)
    JOBS[job.id] = job
    asyncio.get_running_loop().create_task(job.run())
    return job


def latest():
    return max(JOBS.values(), key=lambda j: j.started) if JOBS else None
