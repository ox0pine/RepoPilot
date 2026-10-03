from __future__ import annotations


def _lines(text: str) -> list[str]:
    # split('\n') retains the valid empty final LSP line.
    return text.split('\n')


def to_lsp_position(text: str, line: int, column: int) -> dict[str, int]:
    if isinstance(line, bool) or isinstance(column, bool) or not isinstance(line, int) or not isinstance(column, int):
        raise ValueError('Positions must be integers')
    lines = _lines(text)
    if not 1 <= line <= len(lines):
        raise ValueError('Line outside document')
    value = lines[line - 1].removesuffix('\r')
    if not 1 <= column <= len(value) + 1:
        raise ValueError('Column outside document')
    return {'line': line - 1, 'character': len(value[:column - 1].encode('utf-16-le')) // 2}


def position_offset(text: str, position: dict) -> int:
    line, character = position['line'], position['character']
    lines = _lines(text)
    if isinstance(line, bool) or isinstance(character, bool) or not isinstance(line, int) or not isinstance(character, int) or line < 0 or line >= len(lines) or character < 0:
        raise ValueError('Invalid LSP position')
    value = lines[line].removesuffix('\r')
    units = 0
    for index, char in enumerate(value):
        if units == character:
            return sum(len(part) + 1 for part in lines[:line]) + index
        units += 2 if ord(char) > 0xffff else 1
        if units > character:
            raise ValueError('Position splits UTF16 surrogate pair')
    if units != character:
        raise ValueError('LSP column outside document')
    return sum(len(part) + 1 for part in lines[:line]) + len(value)


def from_lsp_position(text: str, position: dict) -> dict[str, int]:
    offset = position_offset(text, position)
    prefix = text[:offset]
    return {'line': prefix.count('\n') + 1, 'column': len(prefix.rsplit('\n', 1)[-1]) + 1}


def apply_text_edits(text: str, edits: list[dict]) -> str:
    if len(edits) > 2000:
        raise ValueError('Too many text edits')
    prepared = []
    for edit in edits:
        start = position_offset(text, edit['range']['start'])
        end = position_offset(text, edit['range']['end'])
        replacement = edit['newText']
        if end < start or not isinstance(replacement, str):
            raise ValueError('Invalid text edit')
        prepared.append((start, end, replacement))
    prepared.sort(key=lambda item: (item[0], item[1]))
    for previous, current in zip(prepared, prepared[1:]):
        if previous[1] > current[0] or previous[0] == current[0]:
            raise ValueError('Overlapping text edits')
    for start, end, replacement in reversed(prepared):
        text = text[:start] + replacement + text[end:]
    if len(text.encode('utf-8')) > 1024 * 1024:
        raise ValueError('Edited document exceeds limit')
    return text
