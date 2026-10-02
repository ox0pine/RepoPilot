from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx

from repopilot.execution.tools import ProtocolError, ToolCall, tool_definitions, validate_batch
from repopilot.persistence.settings import StoredModelSettings

MAX_RESPONSE_BYTES = 256 * 1024
MODEL_TIMEOUT_SECONDS = 60


class ModelError(Exception):
    status_code = 502


def strict_json(value: str | bytes) -> object:
    def constant(value: str) -> None:
        raise ValueError('Non-JSON constant')

    def unique(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = item
        return result

    return json.loads(value, parse_constant=constant, object_pairs_hook=unique)


@dataclass(frozen=True)
class ModelReply:
    content: str
    calls: tuple[ToolCall, ...]


class ModelClient:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def complete(self, settings: StoredModelSettings, messages: list[dict], seen_call_ids: set[str]) -> ModelReply:
        if not settings.model.strip():
            raise ModelError('请先在设置中配置并选择模型')
        payload = {'model': settings.model, 'messages': messages, 'tools': tool_definitions(), 'stream': False}
        headers = {'Authorization': f'Bearer {settings.api_key}'} if settings.api_key else {}
        try:
            async with asyncio.timeout(MODEL_TIMEOUT_SECONDS):
                async with httpx.AsyncClient(transport=self.transport, timeout=httpx.Timeout(60, connect=10),
                                            follow_redirects=False, trust_env=False) as client:
                    async with client.stream('POST', f"{settings.base_url.rstrip('/')}/chat/completions", headers=headers, json=payload) as response:
                        if response.status_code in (401, 403):
                            raise ModelError('模型服务拒绝访问，请检查设置中的端点和 API Key')
                        if response.status_code == 429:
                            raise ModelError('模型服务请求过于频繁，请稍后手动重试')
                        if not 200 <= response.status_code < 300:
                            raise ModelError('模型服务暂时不可用，请稍后手动重试')
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_RESPONSE_BYTES:
                                raise ModelError('模型响应超过保存上限，未执行工具')
                            chunks.append(chunk)
                        envelope = strict_json(b''.join(chunks))
            if not isinstance(envelope, dict):
                raise ValueError('Invalid envelope')
            choices = envelope.get('choices')
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise ValueError('Invalid choices')
            choice = choices[0]
            message = choice.get('message')
            if not isinstance(message, dict) or message.get('role') != 'assistant' or message.get('function_call') is not None:
                raise ValueError('Invalid message')
            content = message.get('content')
            if content is None:
                content = ''
            if not isinstance(content, str):
                raise ValueError('Invalid content')
            content.encode('utf-8')
            raw_calls = message.get('tool_calls')
            if raw_calls:
                if choice.get('finish_reason') != 'tool_calls':
                    raise ValueError('Incomplete tool batch')
                calls = validate_batch(raw_calls, seen_call_ids)
            else:
                if raw_calls not in (None, []) or choice.get('finish_reason') != 'stop' or not content.strip():
                    raise ValueError('Incomplete response')
                calls = ()
            return ModelReply(content, calls)
        except (TimeoutError, httpx.TimeoutException):
            raise ModelError('模型请求超时，结果未知，未自动重试') from None
        except httpx.HTTPError:
            raise ModelError('无法连接模型服务，结果未知，未自动重试') from None
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError, ProtocolError):
            raise ModelError('模型响应或工具批次无效，未执行工具') from None
