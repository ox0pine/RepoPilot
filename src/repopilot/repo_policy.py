"""Repository directories that never carry reviewable source changes.

Single host-side definition. The container-side programs (``container_helper.py``
and ``environment.py``) run under ``python3 -I -S`` inside the runtime image,
which has no ``repopilot`` package on ``sys.path``; they therefore keep an
identical literal in ``container_helper.IGNORED_DIRECTORIES``.
"""

IGNORED_DIRECTORIES = frozenset({
    '.git', 'node_modules', '.venv', 'venv', '__pycache__', 'dist', 'build',
    '.next', '.nuxt', 'vendor', '.pytest_cache', '.mypy_cache', '.ruff_cache',
    '.yarn', '.pnpm-store',
})
