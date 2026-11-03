"""Workspace-relative path rules shared by tool arguments, LSP and shell."""

from __future__ import annotations


def relative_path(
    value: str, *, allow_dot: bool = False, deny: frozenset[str] = frozenset(),
) -> str:
    """Normalize one workspace-relative path, rejecting anything unsafe.

    Interior ``.`` segments are folded so ``./a.py`` and ``a.py`` are the same
    file. A whole-string ``.`` is refused unless the caller may address the root.
    """
    if (
        not isinstance(value, str) or not value
        or '\x00' in value or '\\' in value or value.startswith('/')
    ):
        raise ValueError('Invalid workspace path')
    if value == '.':
        if not allow_dot:
            raise ValueError('Unsafe workspace path')
        return '.'
    parts: list[str] = []
    for part in value.split('/'):
        if not part or part == '..' or part in deny:
            raise ValueError('Unsafe workspace path')
        if part == '.':
            continue
        parts.append(part)
    if not parts:
        raise ValueError('Unsafe workspace path')
    return '/'.join(parts)
