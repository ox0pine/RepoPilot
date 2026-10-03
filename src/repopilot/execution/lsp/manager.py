from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from .positions import apply_text_edits, from_lsp_position, to_lsp_position
from .protocol import JsonRpcError, ProtocolError
from .session import Session, document_uri

METHODS = {
    'hover': ('textDocument/hover', 'hoverProvider'),
    'definition': ('textDocument/definition', 'definitionProvider'),
    'type_definition': ('textDocument/typeDefinition', 'typeDefinitionProvider'),
    'implementation': ('textDocument/implementation', 'implementationProvider'),
    'references': ('textDocument/references', 'referencesProvider'),
    'document_symbols': ('textDocument/documentSymbol', 'documentSymbolProvider'),
    'workspace_symbols': ('workspace/symbol', 'workspaceSymbolProvider'),
    'rename': ('textDocument/rename', 'renameProvider'),
    'code_actions': ('textDocument/codeAction', 'codeActionProvider'),
    'format': ('textDocument/formatting', 'documentFormattingProvider'),
    'call_hierarchy': ('textDocument/prepareCallHierarchy', 'callHierarchyProvider'),
}
AUXILIARY_ROLES = {'.html': 'html', '.htm': 'html', '.css': 'css', '.scss': 'css', '.less': 'css', '.json': 'json', '.jsonc': 'json'}
SOURCE_SUFFIXES = {'.py', '.pyi', '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.vue', *AUXILIARY_ROLES}


def safe_path(path: str, *, directory: bool = False) -> str:
    if directory and path in ('', '.'):
        return '.'
    if not isinstance(path, str) or '\x00' in path or '\\' in path or path.startswith('/'):
        raise ValueError('Invalid workspace path')
    parts = path.split('/')
    if any(part in ('', '.', '..', '.git', 'node_modules', '.venv') for part in parts):
        raise ValueError('Unsafe workspace path')
    return path


def uri_path(uri: str) -> str | None:
    parsed = urlsplit(uri)
    if parsed.scheme != 'file' or parsed.netloc not in ('', 'localhost'):
        return None
    path = unquote(parsed.path)
    if not path.startswith('/workspace/'):
        return None
    try:
        return safe_path(path[len('/workspace/'):])
    except ValueError:
        return None


class LspManager:
    def __init__(self, workspace) -> None:
        self.workspace = workspace
        self.sessions: dict[str, Session] = {}
        self.plans: dict[str, dict] = {}
        self.actions: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        sessions = list(self.sessions.values())
        self.sessions.clear()
        self.plans.clear()
        self.actions.clear()
        outcomes = await asyncio.gather(*(session.close() for session in sessions), return_exceptions=True)
        if any(isinstance(outcome, BaseException) for outcome in outcomes):
            await self.workspace.cleanup()
            raise ProtocolError('Language server shutdown was unconfirmed; execution container stopped')

    async def execute(self, name: str, arguments: dict) -> dict:
        name = name.removeprefix('lsp_')
        from ..workspace import ToolError
        async with self._lock:
            try:
                result = await self._execute(name, arguments)
                if len(json.dumps(result, ensure_ascii=False).encode()) > 128 * 1024:
                    return {'status': 'error', 'message': 'LSP result exceeds safe output limit'}
                return result
            except asyncio.CancelledError:
                await asyncio.shield(self.close())
                raise
            except JsonRpcError as exc:
                status = 'unsupported' if exc.code == -32601 else ('not_ready' if exc.code in (-32801, -32802) else 'error')
                return {'status': status, 'message': str(exc)[:4096], 'code': exc.code}
            except ProtocolError as exc:
                try:
                    await self.close()
                except ProtocolError:
                    pass  # close already stopped the container on an uncertain shutdown.
                return {'status': 'error', 'message': str(exc)[:4096]}
            except (ValueError, KeyError, TypeError, TimeoutError, OSError, ToolError) as exc:
                return {'status': 'error', 'message': str(exc)[:4096]}

    async def _snapshot(self) -> dict[str, str]:
        result = await self.workspace.helper_action('lsp_snapshot', {})
        files = result.get('files', [])
        if isinstance(files, dict):
            return files
        return {item['path']: item['sha256'] for item in files}

    async def _read(self, path: str) -> dict:
        value = await self.workspace.helper_action('lsp_read', {'path': safe_path(path)})
        if not isinstance(value.get('content'), str) or len(value['content'].encode()) > 1024 * 1024:
            raise ValueError('Document exceeds safe read limit')
        return value

    def _project(self, projects: list[dict], path: str) -> dict | None:
        candidates = [p for p in projects if p['root'] == '.' or path == p['root'] or path.startswith(p['root'] + '/')]
        if not candidates:
            return None
        if PurePosixPath(path).suffix in ('.py', '.pyi'):
            candidates = [p for p in candidates if p['language'] == 'python']
        elif PurePosixPath(path).suffix == '.vue':
            candidates = [p for p in candidates if p['language'] == 'vue']
        else:
            frontend = [p for p in candidates if p['language'] in ('javascript', 'typescript', 'vue')]
            if frontend or PurePosixPath(path).suffix in SOURCE_SUFFIXES:
                candidates = frontend
        return max(candidates, key=lambda p: len(p['root'])) if candidates else None

    async def _session(self, project: dict, *, formatter: bool = False, server_role: str | None = None) -> Session:
        key = project['root'] + ':' + project['language'] + (':formatter' if formatter else '') + (':' + server_role if server_role else '')
        if server_role:
            argv = project.get('servers', {}).get(server_role)
            if not argv:
                raise ProtocolError('No configured ' + server_role + ' language server')
            project = {**project, 'language': server_role, 'server_argv': argv}
        if formatter:
            project = {**project, 'server_argv': project.get('servers', {}).get('ruff')}
        existing = self.sessions.get(key)
        if existing and existing.client and not existing.client.closed:
            return existing
        if existing:
            await existing.close()
        if len(self.sessions) + (2 if project['language'] == 'vue' else 1) > 32:
            raise ProtocolError('Workspace exceeds 32 language server sessions')
        session = Session(self.workspace, project)
        self.sessions[key] = session
        try:
            if project['language'] == 'vue':
                bridge = Session(self.workspace, project, companion=True)
                self.sessions[key + ':typescript'] = bridge
                await bridge.start()
                session.bridge = bridge
            await session.start()
        except BaseException:
            await self.close()
            raise
        return session

    async def _sync(self) -> dict[str, str]:
        snapshot = await self._snapshot()
        for session in list(self.sessions.values()):
            for path, document in list(session.documents.items()):
                if path not in snapshot:
                    await session.client.notify('textDocument/didClose', {'textDocument': {'uri': document['uri']}})
                    session.documents.pop(path, None)
                    session.diagnostics.pop(document['uri'], None)
                elif snapshot[path] != document['sha256']:
                    await session.sync(path, await self._read(path))
            if session.client and not session.client.closed:
                changes = [{'uri': document_uri(path), 'type': 1 if path not in session.snapshot else 2} for path, digest in snapshot.items() if session.snapshot.get(path) != digest]
                changes.extend({'uri': document_uri(path), 'type': 3} for path in session.snapshot if path not in snapshot)
                for offset in range(0, len(changes), 1000):
                    await session.client.notify('workspace/didChangeWatchedFiles', {'changes': changes[offset:offset + 1000]})
                    if session.project['language'] == 'json':
                        await session.client.notify('json/schemaContent', [change['uri'] for change in changes[offset:offset + 1000]])
                session.snapshot = snapshot.copy()
        return snapshot

    async def _execute(self, name: str, args: dict) -> dict:
        if name == 'apply_workspace_edit':
            return await self._apply(args['plan_id'])
        environment = await self.workspace.environment_info()
        projects = environment.get('projects', [])
        path = safe_path(args.get('path', '.'), directory=True)
        if name == 'status':
            return {'status': 'ok', 'data': {'projects': projects, 'sessions': [{'root': s.project['root'], 'language': s.project['language'], 'ready': bool(s.client and not s.client.closed), 'documents': len(s.documents), 'bridge_error': s.bridge_failure} for s in self.sessions.values()]}}
        if name == 'diagnostics':
            return await self._diagnostics(projects, path)
        if name not in METHODS:
            return {'status': 'unsupported', 'message': 'Unknown LSP tool'}
        if name == 'workspace_symbols':
            query = args.get('query', '')
            if not isinstance(query, str) or len(query) > 1024:
                raise ValueError('Invalid symbol query')
            selected = [p for p in projects if path == '.' or p['root'] == path or p['root'].startswith(path + '/')]
            if not selected:
                project = self._project(projects, path)
                selected = [project] if project else []
            if not selected or any(not p.get('ready', True) for p in selected):
                return {'status': 'not_ready', 'message': 'Workspace language environments are not ready'}
            symbols = []
            supported = False
            for project in selected:
                session = await self._session(project)
                await self._sync()
                session = session.bridge or session
                if session.capabilities.get('workspaceSymbolProvider'):
                    supported = True
                    symbols.extend((await session.client.request('workspace/symbol', {'query': query}) or [])[:200])
            return {'status': 'ok' if supported else 'unsupported', 'data': await self._normalize(symbols[:200]), 'truncated': len(symbols) > 200}
        project = self._project(projects, path)
        if project is None or not project.get('ready', True):
            return {'status': 'not_ready', 'message': (project or {}).get('blocked_reason') or 'No configured project language environment'}
        session = await self._session(project, formatter=name == 'format' and project['language'] == 'python', server_role=AUXILIARY_ROLES.get(PurePosixPath(path).suffix))
        snapshot = await self._sync()
        method, capability = METHODS[name]
        if path == '.':
            raise ValueError('A document path is required')
        document = await session.sync(path, await self._read(path))
        if session.bridge:
            await session.bridge.sync(path, document)
        if session.bridge and name in ('definition', 'type_definition', 'implementation', 'references', 'rename', 'call_hierarchy'):
            session = session.bridge
            document = session.documents[path]
        if not session.capabilities.get(capability):
            return {'status': 'unsupported', 'message': 'Language server does not support ' + name}
        params = {'textDocument': {'uri': document['uri']}}
        if name not in ('document_symbols', 'format'):
            params['position'] = to_lsp_position(document['content'], args['line'], args['column'])
        if name == 'references':
            params['context'] = {'includeDeclaration': bool(args.get('include_declaration', False))}
        elif name == 'rename':
            new_name = args['new_name']
            if not isinstance(new_name, str) or not new_name or len(new_name) > 1024:
                raise ValueError('Invalid new name')
            provider = session.capabilities.get('renameProvider')
            if isinstance(provider, dict) and provider.get('prepareProvider'):
                prepared = await session.client.request('textDocument/prepareRename', params)
                if prepared is None:
                    return {'status': 'unsupported', 'message': 'Symbol cannot be renamed'}
            params['newName'] = new_name
        elif name == 'format':
            tab_size = args.get('tab_size', 4)
            if isinstance(tab_size, bool) or not isinstance(tab_size, int) or not 1 <= tab_size <= 16:
                raise ValueError('Invalid tab size')
            params['options'] = {'tabSize': tab_size, 'insertSpaces': bool(args.get('insert_spaces', True))}
        elif name == 'code_actions':
            if args.get('action_id'):
                return await self._resolve_action(args['action_id'], snapshot)
            params.pop('position')
            params['range'] = {'start': to_lsp_position(document['content'], args['line'], args['column']), 'end': to_lsp_position(document['content'], args.get('end_line') or args['line'], args.get('end_column') or args['column'])}
            params['context'] = {'diagnostics': session.diagnostics.get(document['uri'], {}).get('diagnostics', [])}
            if args.get('only'):
                params['context']['only'] = args['only']
        result = await session.client.request(method, params)
        if name == 'hover' and result is None and session.bridge and session.bridge.capabilities.get('hoverProvider'):
            result = await session.bridge.client.request(method, params)
        if session.bridge_failure:
            return {'status': 'not_ready', 'message': 'Vue TypeScript bridge failed: ' + session.bridge_failure}
        if name == 'rename':
            return await self._plan(result or {}, snapshot)
        if name == 'format':
            return await self._plan({'changes': {document['uri']: result or []}}, snapshot)
        if name == 'code_actions':
            action_sources = [(session, action) for action in (result or [])[:100]]
            if session.bridge and session.bridge.capabilities.get('codeActionProvider'):
                semantic_actions = await session.bridge.client.request(method, params)
                action_sources.extend((session.bridge, action) for action in (semantic_actions or [])[:100])
            output = []
            if len(self.actions) > 100:
                self.actions.clear()
            for action_session, action in action_sources[:100]:
                action_id = uuid4().hex
                self.actions[action_id] = {'session': action_session, 'action': action, 'snapshot': snapshot}
                output.append({'action_id': action_id, 'title': str(action.get('title', ''))[:1024], 'kind': action.get('kind'), 'disabled': action.get('disabled')})
            return {'status': 'ok', 'data': {'actions': output}}
        if name == 'call_hierarchy':
            direction = args.get('direction', 'incoming')
            if direction not in ('incoming', 'outgoing'):
                raise ValueError('Invalid call hierarchy direction')
            calls = []
            for item in (result or [])[:20]:
                values = await session.client.request('callHierarchy/' + ('incomingCalls' if direction == 'incoming' else 'outgoingCalls'), {'item': item})
                calls.append({'item': item, 'calls': (values or [])[:100]})
            result = calls
        return {'status': 'ok', 'data': await self._normalize(result, path)}

    async def _diagnostics(self, projects: list[dict], path: str) -> dict:
        snapshot = await self._sync()
        paths = [path] if path != '.' and PurePosixPath(path).suffix in SOURCE_SUFFIXES else [p for p in snapshot if PurePosixPath(p).suffix in SOURCE_SUFFIXES and (path == '.' or p.startswith(path + '/'))]
        if len(paths) > 100:
            return {'status': 'not_ready', 'message': 'Diagnostics scope exceeds 100 documents; select a smaller project or file'}
        if not paths:
            return {'status': 'not_ready', 'message': 'No supported source documents in scope'}
        opened = []
        for file in paths:
            project = self._project(projects, file)
            if not project or not project.get('ready', True):
                return {'status': 'not_ready', 'message': (project or {}).get('blocked_reason') or 'No project environment for ' + file}
            session = await self._session(project, server_role=AUXILIARY_ROLES.get(PurePosixPath(file).suffix))
            document = await session.sync(file, await self._read(file))
            if session.bridge:
                await session.bridge.sync(file, document)
            if session.project['language'] in ('javascript', 'typescript', 'vue'):
                diagnostics = []
                diagnostic_session = session.bridge or session
                for command in ('syntacticDiagnosticsSync', 'semanticDiagnosticsSync', 'suggestionDiagnosticsSync'):
                    response = await diagnostic_session.client.request('workspace/executeCommand', {'command': 'typescript.tsserverRequest', 'arguments': [command, {'file': '/workspace/' + file}, {'isAsync': False}]})
                    if not isinstance(response, dict) or not isinstance(response.get('body'), list):
                        return {'status': 'not_ready', 'message': 'TypeScript analysis did not return completed diagnostics'}
                    for item in response['body'][:500]:
                        start, end = item.get('start'), item.get('end')
                        if not isinstance(start, dict) or not isinstance(end, dict):
                            continue
                        diagnostics.append({'range': {'start': {'line': start['line'] - 1, 'character': start['offset'] - 1}, 'end': {'line': end['line'] - 1, 'character': end['offset'] - 1}}, 'message': item.get('text', ''), 'code': item.get('code'), 'severity': {'error': 1, 'warning': 2, 'suggestion': 4, 'message': 3}.get(item.get('category'), 1), 'source': 'typescript'})
                session.diagnostics[document['uri']] = {'version': document['version'], 'diagnostics': diagnostics[:500], 'authoritative': True}
            if session.capabilities.get('diagnosticProvider'):
                report = await session.client.request('textDocument/diagnostic', {'textDocument': {'uri': document['uri']}})
                if report.get('kind') == 'full':
                    completed = session.diagnostics.get(document['uri'], {}).get('diagnostics', []) if session.project['language'] in ('javascript', 'typescript', 'vue') else []
                    session.diagnostics[document['uri']] = {'version': document['version'], 'diagnostics': (completed + report.get('items', []))[:500], 'authoritative': True}
            opened.append((file, session, document))
        deadline = asyncio.get_running_loop().time() + 12
        while any(doc['uri'] not in session.diagnostics for _, session, doc in opened):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return {'status': 'not_ready', 'message': 'Language analysis has not published diagnostics for the current document versions'}
            await asyncio.sleep(min(.1, remaining))
        data = []
        for file, session, document in opened:
            if session.bridge_failure:
                return {'status': 'not_ready', 'message': session.bridge_failure}
            value = session.diagnostics[document['uri']]
            data.append({'path': file, 'version': value['version'], 'diagnostics': await self._normalize(value['diagnostics'], file)})
        return {'status': 'ok', 'data': data}

    async def _normalize(self, value, context_path: str | None = None, *, depth: int = 0):
        if depth > 20:
            raise ValueError('LSP result nesting exceeds limit')
        if isinstance(value, list):
            return [await self._normalize(item, context_path, depth=depth + 1) for item in value[:200]]
        if not isinstance(value, dict):
            return value[:16384] if isinstance(value, str) else value
        if set(value) == {'line', 'character'} and context_path:
            return from_lsp_position((await self._read(context_path))['content'], value)
        origin_path = context_path
        output = {}
        uri = value.get('uri', value.get('targetUri'))
        if uri:
            context_path = uri_path(uri)
            output['path' if context_path else 'external_uri'] = context_path or str(uri)[:4096]
        for key, item in value.items():
            if key in ('uri', 'targetUri'):
                continue
            if uri and context_path is None and key in ('range', 'selectionRange', 'targetRange', 'targetSelectionRange'):
                continue
            range_path = origin_path if key == 'originSelectionRange' else context_path
            if key == 'fromRanges' and isinstance(value.get('from'), dict):
                range_path = uri_path(value['from'].get('uri', ''))
                output[key] = await self._normalize(item, range_path, depth=depth + 1)
                continue
            if key in ('range', 'selectionRange', 'targetRange', 'targetSelectionRange', 'originSelectionRange') and isinstance(item, dict) and range_path:
                document = await self._read(range_path)
                output[key] = {'start': from_lsp_position(document['content'], item['start']), 'end': from_lsp_position(document['content'], item['end'])}
            elif key == 'data':
                continue  # Server-private opaque data never leaks into model arguments.
            else:
                output[key] = await self._normalize(item, context_path, depth=depth + 1)
        return output

    async def _resolve_action(self, action_id: str, snapshot: dict) -> dict:
        stored = self.actions.get(action_id)
        if not stored:
            raise ValueError('Unknown or expired code action')
        if stored['snapshot'] != snapshot:
            raise ValueError('Code action is stale; request actions again')
        action, session = stored['action'], stored['session']
        provider = session.capabilities.get('codeActionProvider')
        if isinstance(provider, dict) and provider.get('resolveProvider') and 'data' in action:
            action = await session.client.request('codeAction/resolve', action)
        command = action.get('command')
        if command:
            # Arbitrary server commands can mutate files before approval. None are
            # permitted without a demonstrated side-effect-free command contract.
            return {'status': 'unsupported', 'message': 'Code action requires an unsupported server command', 'command': command if isinstance(command, str) else command.get('command')}
        if action.get('disabled'):
            return {'status': 'unsupported', 'message': action['disabled'].get('reason', 'Action disabled')}
        return await self._plan(action.get('edit') or {}, snapshot)

    async def _plan(self, edit: dict, snapshot: dict) -> dict:
        if not isinstance(edit, dict):
            raise ValueError('Invalid workspace edit')
        operations = []
        expected = {}
        virtual: dict[str, dict | None] = {}
        async def load(path):
            if path not in virtual:
                virtual[path] = await self._read(path) if path in snapshot else None
                expected[path] = snapshot.get(path)
            return virtual[path]
        changes = [{'textDocument': {'uri': uri}, 'edits': edits} for uri, edits in edit.get('changes', {}).items()]
        changes.extend(edit.get('documentChanges', []))
        if len(changes) > 100:
            raise ValueError('Workspace edit exceeds 100 files')
        for change in changes:
            kind = change.get('kind')
            uri = change.get('uri') if kind else change.get('textDocument', {}).get('uri')
            path = uri_path(uri) if uri else None
            if kind == 'rename':
                old = uri_path(change.get('oldUri', ''))
                path = uri_path(change.get('newUri', ''))
                if not old or not path:
                    raise ValueError('Workspace edit targets outside workspace')
                source, destination = await load(old), await load(path)
                if source is None or destination is not None:
                    raise ValueError('Rename requires existing source and absent destination')
                operations.append({'kind': 'rename', 'old_path': old, 'path': path, 'expected_sha256': source['sha256']})
                virtual[path], virtual[old] = source, None
            elif not path:
                raise ValueError('Workspace edit targets outside workspace')
            elif kind == 'create':
                if await load(path) is not None:
                    raise ValueError('Create target already exists')
                virtual[path] = {'content': '', 'sha256': hashlib.sha256(b'').hexdigest()}
                operations.append({'kind': 'create', 'path': path, 'content': ''})
            elif kind == 'delete':
                source = await load(path)
                if source is None:
                    raise ValueError('Delete target missing')
                operations.append({'kind': 'delete', 'path': path, 'expected_sha256': source['sha256']})
                virtual[path] = None
            elif kind is None:
                source = await load(path)
                if source is None:
                    raise ValueError('Text edit target missing')
                version = change['textDocument'].get('version')
                if version is not None:
                    versions = [s.documents[path]['version'] for s in self.sessions.values() if path in s.documents]
                    if not versions or any(v != version for v in versions):
                        raise ValueError('Workspace edit document version is stale')
                content = apply_text_edits(source['content'], change.get('edits', []))
                operations.append({'kind': 'text', 'path': path, 'expected_sha256': source['sha256'], 'content': content})
                virtual[path] = {'content': content, 'sha256': hashlib.sha256(content.encode()).hexdigest()}
            else:
                return {'status': 'unsupported', 'message': 'Unknown workspace resource operation'}
        if len(json.dumps(operations).encode()) > 4 * 1024 * 1024:
            raise ValueError('Workspace plan exceeds limit')
        if not operations:
            return {'status': 'ok', 'data': {'operations': [], 'files': 0}, 'plan_id': None}
        if len(self.plans) >= 32:
            self.plans.clear()
        plan_id = uuid4().hex
        self.plans[plan_id] = {'operations': operations, 'expected': expected, 'snapshot': snapshot}
        return {'status': 'ok', 'plan_id': plan_id, 'data': {'operations': [{k: v for k, v in op.items() if k != 'content'} for op in operations], 'files': len(expected)}}

    async def _apply(self, plan_id: str) -> dict:
        plan = self.plans.get(plan_id)
        if not plan:
            raise ValueError('Unknown or expired workspace edit plan')
        if await self._snapshot() != plan['snapshot']:
            self.plans.pop(plan_id, None)
            raise ValueError('Workspace changed since plan creation; request a new plan')
        await self.close()
        if await self._snapshot() != plan['snapshot']:
            raise ValueError('Workspace changed during language server shutdown; request a new plan')
        result = await self.workspace.helper_action('lsp_apply', {'operations': plan['operations'], 'expected': plan['expected']})
        return {'status': 'ok', 'data': result}
