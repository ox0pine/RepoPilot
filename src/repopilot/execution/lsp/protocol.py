from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

MAX_MESSAGE = 4 * 1024 * 1024


class ProtocolError(Exception):
    pass


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class JsonRpcClient:
    def __init__(self, reader: asyncio.StreamReader, writer, *, request_handler: Callable[[str, object], Awaitable[object]] | None = None, notification_handler: Callable[[str, object], Awaitable[None]] | None = None) -> None:
        self.reader, self.writer = reader, writer
        self.request_handler, self.notification_handler = request_handler, notification_handler
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._reader_task: asyncio.Task | None = None
        self._handlers: set[asyncio.Task] = set()
        self._write_lock = asyncio.Lock()
        self.closed = False
        self.failure: str | None = None

    def start(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._read_loop())

    async def _send(self, message: dict) -> None:
        if self.closed:
            raise ProtocolError(self.failure or 'Language server closed')
        body = json.dumps(message, ensure_ascii=False, separators=(',', ':')).encode()
        if len(body) > MAX_MESSAGE:
            raise ProtocolError('JSONRPC message exceeds limit')
        async with self._write_lock:
            self.writer.write(f'Content-Length: {len(body)}\r\n\r\n'.encode() + body)
            try:
                await asyncio.wait_for(self.writer.drain(), 10)
            except (TimeoutError, BrokenPipeError, ConnectionResetError) as exc:
                raise ProtocolError('Language server stdio write failed') from exc

    async def notify(self, method: str, params: object = None) -> None:
        await self._send({'jsonrpc': '2.0', 'method': method, 'params': params})

    async def request(self, method: str, params: object = None, *, timeout: float = 30) -> object:
        self.start()
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params})
            return await asyncio.wait_for(asyncio.shield(future), timeout)
        except (TimeoutError, asyncio.CancelledError):
            if not self.closed:
                await asyncio.shield(self.notify('$/cancelRequest', {'id': request_id}))
            raise
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def _dispatch(self, message: dict) -> None:
        if 'method' not in message:
            future = self._pending.get(message.get('id'))
            if future is not None and not future.done():
                if 'error' in message:
                    error = message['error']
                    future.set_exception(JsonRpcError(error.get('code', -32603), str(error.get('message', 'Server error'))[:4096]))
                else:
                    future.set_result(message.get('result'))
            return
        method, params = message['method'], message.get('params')
        if 'id' in message:
            try:
                if self.request_handler is None:
                    raise JsonRpcError(-32601, 'Unsupported client request')
                result = await self.request_handler(method, params)
                await self._send({'jsonrpc': '2.0', 'id': message['id'], 'result': result})
            except JsonRpcError as exc:
                await self._send({'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': exc.code, 'message': str(exc)}})
            except Exception:
                await self._send({'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32603, 'message': 'Client request failed'}})
        elif self.notification_handler is not None:
            await self.notification_handler(method, params)

    async def _read_loop(self) -> None:
        try:
            while True:
                header = await self.reader.readuntil(b'\r\n\r\n')
                if len(header) > 8192:
                    raise ProtocolError('JSONRPC header exceeds limit')
                lengths = []
                for line in header[:-4].split(b'\r\n'):
                    key, separator, value = line.partition(b':')
                    if not separator:
                        raise ProtocolError('Malformed JSONRPC header')
                    if key.lower() == b'content-length':
                        lengths.append(int(value.strip()))
                if len(lengths) != 1 or not 0 < lengths[0] <= MAX_MESSAGE:
                    raise ProtocolError('Invalid Content-Length')
                message = json.loads(await self.reader.readexactly(lengths[0]))
                if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
                    raise ProtocolError('Malformed JSONRPC message')
                if 'id' in message:
                    ident = message['id']
                    if isinstance(ident, bool) or not isinstance(ident, (int, str)) or (isinstance(ident, str) and (not ident or len(ident) > 128)) or (isinstance(ident, int) and abs(ident) > 2 ** 53 - 1):
                        raise ProtocolError('Invalid JSONRPC id')
                if 'method' in message:
                    if not isinstance(message['method'], str) or len(message['method']) > 256:
                        raise ProtocolError('Invalid JSONRPC method')
                else:
                    if 'id' not in message or ('result' in message) == ('error' in message):
                        raise ProtocolError('Malformed JSONRPC response')
                    if 'error' in message and (not isinstance(message['error'], dict) or not isinstance(message['error'].get('code'), int) or not isinstance(message['error'].get('message'), str)):
                        raise ProtocolError('Malformed JSONRPC error')
                if len(self._handlers) >= 128:
                    raise ProtocolError('Too many server messages')
                task = asyncio.create_task(self._dispatch(message))
                self._handlers.add(task)
                task.add_done_callback(self._handler_done)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.failure = str(exc)[:4096] or 'Language server disconnected'
        finally:
            self.closed = True
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ProtocolError(self.failure or 'Language server disconnected'))

    def _handler_done(self, task: asyncio.Task) -> None:
        self._handlers.discard(task)
        if not task.cancelled():
            task.exception()  # Always consume asynchronous handler exceptions.

    async def close(self) -> None:
        self.closed = True
        tasks = [task for task in [self._reader_task, *self._handlers] if task is not None and task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ProtocolError('Language server closed'))
        self._pending.clear()
        self.writer.close()
        try:
            await asyncio.wait_for(self.writer.wait_closed(), 2)
        except (AttributeError, BrokenPipeError, ConnectionResetError):
            pass
