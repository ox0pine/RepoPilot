"""One bounded child-process runner for trusted control-plane commands."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Callable
from pathlib import Path


class OutputLimitExceeded(Exception):
    """A stream crossed the caller's byte budget; the process has been stopped."""


async def run_command(
    args: list[str],
    *,
    timeout: float,
    limit: int,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    data: bytes | None = None,
    merge_stderr: bool = False,
    poll: Callable[[], None] | None = None,
) -> tuple[int, bytes, bytes]:
    """Run one command in its own session; never leak a process past an error.

    ``limit`` applies per collected stream, and the stderr value is empty when
    ``merge_stderr`` folded it into stdout. ``poll`` runs every 0.1s while the
    process is alive (used to bound a destination directory during a clone).
    Availability and exit-code semantics stay with the caller.
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT if merge_stderr else asyncio.subprocess.PIPE,
        cwd=cwd, env=env, start_new_session=True,
    )
    assert process.stdout is not None

    async def collect(stream: asyncio.StreamReader) -> bytes:
        value = bytearray()
        while chunk := await stream.read(65536):
            if len(value) + len(chunk) > limit:
                raise OutputLimitExceeded
            value.extend(chunk)
        return bytes(value)

    async def send() -> None:
        assert process.stdin is not None
        try:
            process.stdin.write(data or b'')
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()
            try:
                await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass

    stdout = asyncio.create_task(collect(process.stdout))
    stderr = None if process.stderr is None else asyncio.create_task(collect(process.stderr))
    waiter = asyncio.create_task(process.wait())
    writer = asyncio.create_task(send()) if data is not None else None
    tasks: list[asyncio.Task] = [task for task in (stdout, stderr, waiter, writer) if task is not None]
    try:
        async with asyncio.timeout(timeout):
            if poll is None:
                await waiter
            else:
                while not waiter.done():
                    # Surface an oversized stream now instead of after exit.
                    for task in (stdout, stderr):
                        if task is not None and task.done():
                            task.result()
                    poll()
                    await asyncio.sleep(0.1)
                await waiter
            output = await stdout
            error = b'' if stderr is None else await stderr
            if writer is not None:
                await asyncio.gather(writer, return_exceptions=True)
        assert process.returncode is not None
        return process.returncode, output, error
    except BaseException:
        for task in tasks:
            task.cancel()
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                # Already gone, or no longer signalable from this session.
                pass
        await process.wait()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
