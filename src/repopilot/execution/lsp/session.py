from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from urllib.parse import quote

from .protocol import JsonRpcClient, JsonRpcError, ProtocolError


def document_uri(path: str) -> str:
    return 'file://' + quote('/workspace/' + path, safe='/')


class Session:
    def __init__(self, workspace, project: dict, *, companion: bool = False) -> None:
        self.workspace, self.project, self.companion = workspace, project, companion
        self.process = None
        self.client: JsonRpcClient | None = None
        self.capabilities: dict = {}
        self.documents: dict[str, dict] = {}
        self.diagnostics: dict[str, dict] = {}
        self.snapshot: dict[str, str] = {}
        self.analysis_event = asyncio.Event()
        self.versionless_diagnostics = False
        self.shutdown_unconfirmed = False
        self.stderr = bytearray()
        self.stderr_task = None
        self.bridge: Session | None = None
        self.bridge_failure: str | None = None
        self.settings = {'python': {'pythonPath': project.get('interpreter'), 'analysis': {'diagnosticMode': 'openFilesOnly', 'autoSearchPaths': True}}, 'pyright': {'disableOrganizeImports': False}}
        self.settings.update({
            'json': {'validate': {'enable': True}, 'format': {'enable': True}, 'schemas': []},
            'css': {'validate': True}, 'scss': {'validate': True}, 'less': {'validate': True},
            'html': {'validate': {'scripts': True, 'styles': True}, 'format': {'enable': True}},
        })

    async def start(self) -> None:
        language = self.project['language']
        servers = self.project.get('servers', {})
        argv = self.project.get('companion_argv') if self.companion else self.project.get('server_argv')
        if not argv:
            key = 'typescript' if self.companion or language in ('javascript', 'typescript') else ('pyright' if language == 'python' else 'vue')
            argv = servers.get(key)
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and '\x00' not in item for item in argv):
            raise ProtocolError('No configured language server')
        root = self.project['root']
        cwd = '/workspace' if root == '.' else '/workspace/' + root
        self.process = await asyncio.create_subprocess_exec('docker', 'exec', '-i', '--user=1000:1000', '--workdir=' + cwd, self.workspace.container_name, *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=8192)
        self.client = JsonRpcClient(self.process.stdout, self.process.stdin, request_handler=self._request, notification_handler=self._notification)
        self.client.start()
        self.stderr_task = asyncio.create_task(self._stderr())
        sdk = self.project.get('typescript_sdk')
        # TLS dynamically routes definitions to its syntax-only server while
        # projects load, yielding import aliases rather than real definitions.
        # A single semantic server queues requests behind project analysis.
        options = {'typescript': {'tsdk': sdk}, 'tsserver': {'path': (sdk + '/tsserver.js') if sdk else None, 'useSyntaxServer': 'never'}, 'disableAutomaticTypingAcquisition': True, 'preferences': {'includeCompletionsForModuleExports': True}}
        if language in ('html', 'css', 'json'):
            options = {'provideFormatter': True}
            if language == 'html':
                options['embeddedLanguages'] = {'css': True, 'javascript': True}
            if language == 'json':
                options['handledSchemaProtocols'] = []
        if self.companion and self.project.get('vue_plugin'):
            options['plugins'] = [{'name': '@vue/typescript-plugin', 'location': self.project['vue_plugin'], 'languages': ['vue']}]
        capabilities = {
            'general': {'positionEncodings': ['utf-16']},
            'workspace': {
                'configuration': True, 'workspaceFolders': True, 'applyEdit': False,
                'workspaceEdit': {'documentChanges': True, 'resourceOperations': ['create', 'rename', 'delete'], 'failureHandling': 'transactional'},
            },
            'textDocument': {
                'publishDiagnostics': {'versionSupport': True},
                'definition': {'linkSupport': True},
                'typeDefinition': {'linkSupport': True},
                'implementation': {'linkSupport': True},
                'rename': {'prepareSupport': True},
                'callHierarchy': {'dynamicRegistration': False},
                'diagnostic': {'dynamicRegistration': False, 'relatedDocumentSupport': True},
                'codeAction': {
                    'resolveSupport': {'properties': ['edit', 'command']},
                    'codeActionLiteralSupport': {'codeActionKind': {'valueSet': ['', 'quickfix', 'refactor', 'source', 'source.organizeImports']}},
                },
            },
        }
        root_uri = document_uri('' if root == '.' else root)
        result = await self.client.request('initialize', {
            'processId': None, 'rootUri': root_uri,
            'workspaceFolders': [{'uri': root_uri, 'name': PurePosixPath(cwd).name}],
            'clientInfo': {'name': 'RepoPilot', 'version': '1'},
            'capabilities': capabilities, 'initializationOptions': options,
        })
        self.capabilities = result.get('capabilities', {})
        if self.capabilities.get('positionEncoding', 'utf-16') != 'utf-16':
            raise ProtocolError('Server does not support required UTF16 positions')
        await self.client.notify('initialized', {})
        await self.client.notify('workspace/didChangeConfiguration', {'settings': self.settings})

    async def _stderr(self) -> None:
        while chunk := await self.process.stderr.read(4096):
            self.stderr.extend(chunk)
            if len(self.stderr) > 16384:
                del self.stderr[:-16384]

    async def _request(self, method: str, params) -> object:
        if method == 'vscode/content':
            from .manager import uri_path
            path = uri_path(params) if isinstance(params, str) else None
            if path is None:
                raise JsonRpcError(-32602, 'Schema outside workspace is unavailable')
            return (await self.workspace.helper_action('lsp_read', {'path': path}))['content']
        if method == 'workspace/configuration':
            results = []
            for item in (params or {}).get('items', []):
                section = item.get('section', '')
                value = self.settings
                # Exact dotted keys are allowed in LSP configuration sections.
                if section in value:
                    value = value[section]
                else:
                    for key in section.split('.') if section else []:
                        value = value.get(key) if isinstance(value, dict) else None
                results.append(value)
            return results
        if method == 'workspace/workspaceFolders':
            root = self.project['root']
            return [{'uri': document_uri('' if root == '.' else root), 'name': root}]
        if method in ('client/registerCapability', 'client/unregisterCapability', 'window/workDoneProgress/create'):
            if method == 'client/registerCapability':
                mapping = {'textDocument/diagnostic': 'diagnosticProvider', 'workspace/diagnostic': 'diagnosticProvider'}
                for registration in (params or {}).get('registrations', []):
                    key = mapping.get(registration.get('method'))
                    if key:
                        self.capabilities[key] = registration.get('registerOptions') or True
            return None
        if method == 'workspace/applyEdit':
            return {'applied': False, 'failureReason': 'Controller approval plan required'}
        if method == 'window/showMessageRequest':
            return None
        raise JsonRpcError(-32601, 'Client request unsupported')

    async def _notification(self, method: str, params) -> None:
        if method == 'textDocument/publishDiagnostics' and isinstance(params, dict):
            uri = params.get('uri')
            document = next((doc for doc in self.documents.values() if doc['uri'] == uri), None)
            if self.diagnostics.get(uri, {}).get('authoritative'):
                return
            if document is not None:
                version = params.get('version')
                if version is None:
                    self.versionless_diagnostics = True
                if version == document['version'] or (version is None and document['version'] == 1):
                    self.diagnostics[uri] = {'version': document['version'], 'diagnostics': params.get('diagnostics', [])[:500], 'received': asyncio.get_running_loop().time()}
                    self.analysis_event.set()
        elif method == 'tsserver/request':
            # Official Vue v3 bridge and TLS 4.4.0 custom request command:
            # https://github.com/vuejs/language-tools/discussions/5456
            # https://github.com/typescript-language-server/typescript-language-server/blob/v4.4.0/src/commands/tsserverRequests.ts
            # vscode-jsonrpc's string-method API wraps its single array argument
            # in positional params. The pinned Vue 3.0.8 server uses this API.
            if isinstance(params, list) and len(params) == 1 and isinstance(params[0], list):
                params = params[0]
            if not self.bridge or not isinstance(params, list) or len(params) != 3:
                self.bridge_failure = 'Vue TypeScript bridge unavailable'
                return
            sequence, command, arguments = params
            try:
                response = await self.bridge.client.request('workspace/executeCommand', {'command': 'typescript.tsserverRequest', 'arguments': [command, arguments, {'isAsync': True, 'lowPriority': True}]})
                await self.client.notify('tsserver/response', [[sequence, response.get('body') if isinstance(response, dict) else None]])
            except Exception as exc:
                self.bridge_failure = str(exc)[:1024]
                await self.client.notify('tsserver/response', [[sequence, None]])

    async def sync(self, path: str, value: dict) -> dict:
        previous = self.documents.get(path)
        if previous and previous['sha256'] == value['sha256']:
            return previous
        if previous and self.versionless_diagnostics:
            retained = {key: document for key, document in self.documents.items() if key != path}
            await self.close()
            await self.start()
            self.versionless_diagnostics = False
            for key, document in retained.items():
                await self.sync(key, document)
            previous = None
        if previous is None and len(self.documents) >= 200:
            raise ProtocolError('Session exceeds 200 synchronized documents; close sessions before expanding scope')
        total = sum(len(doc['content'].encode()) for key, doc in self.documents.items() if key != path)
        if total + len(value['content'].encode()) > 16 * 1024 * 1024:
            raise ProtocolError('Session document memory exceeds 16 MiB')
        version = previous['version'] + 1 if previous else 1
        document = {**value, 'version': version, 'uri': document_uri(path)}
        self.documents[path] = document
        self.diagnostics.pop(document['uri'], None)
        suffix = PurePosixPath(path).suffix
        language = {'.py': 'python', '.pyi': 'python', '.js': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript', '.jsx': 'javascriptreact', '.ts': 'typescript', '.tsx': 'typescriptreact', '.vue': 'vue', '.html': 'html', '.htm': 'html', '.css': 'css', '.scss': 'scss', '.less': 'less', '.json': 'json', '.jsonc': 'jsonc'}.get(suffix, 'plaintext')
        if previous:
            await self.client.notify('textDocument/didChange', {'textDocument': {'uri': document['uri'], 'version': version}, 'contentChanges': [{'text': value['content']}]})
        else:
            await self.client.notify('textDocument/didOpen', {'textDocument': {'uri': document['uri'], 'languageId': language, 'version': version, 'text': value['content']}})
        return document

    async def close(self) -> None:
        unconfirmed = self.shutdown_unconfirmed
        if self.client is not None and not self.client.closed:
            try:
                await self.client.request('shutdown', None, timeout=2)
                await self.client.notify('exit', None)
            except Exception:
                pass
        if self.client is not None:
            await self.client.close()
        if self.process is not None:
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except TimeoutError:
                unconfirmed = True
                self.shutdown_unconfirmed = True
                self.process.kill()
                await asyncio.wait_for(self.process.wait(), 2)
        if self.stderr_task:
            self.stderr_task.cancel()
            await asyncio.gather(self.stderr_task, return_exceptions=True)
        self.documents.clear()
        self.diagnostics.clear()
        if unconfirmed:
            raise ProtocolError('Cannot confirm container language server shutdown')
