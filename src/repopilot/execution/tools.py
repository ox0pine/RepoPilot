from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from repopilot.execution.workspace import ToolError, Workspace


class Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ReadArguments(Arguments):
    path: StrictStr
    start_line: StrictInt = Field(default=1, ge=1)
    end_line: StrictInt = Field(default=200, ge=1)


class SearchArguments(Arguments):
    query: StrictStr = Field(min_length=1)
    path: StrictStr = '.'


class EditArguments(Arguments):
    path: StrictStr
    expected_sha256: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    old_text: StrictStr = Field(min_length=1)
    new_text: StrictStr


class WriteArguments(Arguments):
    path: StrictStr
    content: StrictStr


class ShellArguments(Arguments):
    command: StrictStr = Field(min_length=1, max_length=4000)


ARGUMENT_MODELS = {
    'read_file': ReadArguments, 'search': SearchArguments,
    'edit_file': EditArguments, 'write_file': WriteArguments, 'shell': ShellArguments,
}


class ProtocolError(Exception):
    """Invalid complete model batch; no tool from the batch may execute."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


def validate_batch(raw_calls: object, seen_call_ids: set[str]) -> tuple[ToolCall, ...]:
    if not isinstance(raw_calls, list) or not raw_calls or len(raw_calls) > 64:
        raise ProtocolError('模型工具批次格式无效')
    calls: list[ToolCall] = []
    ids: set[str] = set()
    # Import locally to keep the JSON parser shared with the HTTP adapter.
    from repopilot.execution.model import strict_json
    for raw in raw_calls:
        if not isinstance(raw, dict) or set(raw) != {'id', 'type', 'function'} or raw['type'] != 'function':
            raise ProtocolError('模型工具调用格式无效')
        call_id = raw['id']
        function = raw['function']
        if not isinstance(call_id, str) or not call_id.strip() or len(call_id.encode()) > 512 or call_id in seen_call_ids or call_id in ids:
            raise ProtocolError('模型工具调用 ID 为空或重复')
        if not isinstance(function, dict) or set(function) != {'name', 'arguments'}:
            raise ProtocolError('模型工具函数格式无效')
        name, encoded = function['name'], function['arguments']
        if not isinstance(name, str) or name not in ARGUMENT_MODELS or not isinstance(encoded, str):
            raise ProtocolError('模型工具名称或参数无效')
        try:
            args = ARGUMENT_MODELS[name].model_validate(strict_json(encoded)).model_dump()
            for key, value in args.items():
                if isinstance(value, str) and '\x00' in value:
                    raise ValueError('NUL')
                if key in {'content', 'old_text', 'new_text'} and len(value.encode()) > 1024 * 1024:
                    raise ValueError('Too large')
            if 'path' in args:
                path = args['path']
                if not path or path.startswith('/') or '\\' in path or any(part == '..' for part in path.split('/')):
                    raise ValueError('Unsafe path')
                if path == '.' and name != 'search':
                    raise ValueError('Not a file')
            if name == 'read_file' and not 0 <= args['end_line'] - args['start_line'] < 200:
                raise ValueError('Invalid line range')
        except (ValueError, TypeError, UnicodeError, ValidationError):
            raise ProtocolError('模型工具参数无效，未执行此批次') from None
        ids.add(call_id)
        calls.append(ToolCall(call_id, name, args))
    seen_call_ids.update(ids)
    return tuple(calls)


def tool_definitions() -> list[dict]:
    descriptions = {
        'read_file': 'Read at most 200 lines/16KiB of a UTF-8 file; use returned whole-file sha256 for editing.',
        'search': 'Search a literal string in workspace files, at most 100 matches/16KiB.',
        'edit_file': 'Replace exactly one occurrence after reading the file, requiring its unchanged sha256.',
        'write_file': 'Create a new file only; existing files require read_file and edit_file.',
        'shell': 'Run /bin/sh -lc in /workspace, at most 60 seconds; output at most 32KiB.',
    }
    return [{'type': 'function', 'function': {'name': name, 'description': descriptions[name], 'parameters': model.model_json_schema()}}
            for name, model in ARGUMENT_MODELS.items()]


class ToolDispatcher:
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    async def execute(self, call: ToolCall) -> dict:
        try:
            if call.name == 'shell':
                result = asdict(await self.workspace.shell(call.arguments['command'], timeout=60))
            else:
                result = await self.workspace.tool(call.name, call.arguments)
            return {'ok': True, **result}
        except ToolError as exc:
            return {'ok': False, 'error': str(exc)}
