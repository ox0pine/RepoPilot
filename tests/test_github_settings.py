from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
import json
import os
import uuid

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed25519, rsa
import httpx
from pydantic import SecretStr, ValidationError
import pytest
import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import repopilot.api.app as app_module
from repopilot.config import AppSettings
from repopilot.domain.settings import (
    GitHubSettingsUpdate,
    InvalidGitHubSettingsError,
    normalize_github_api_token,
    normalize_ssh_public_key,
    validate_ssh_pair,
)
from repopilot.integration.github import GitHubClient
from repopilot.persistence.database import Database, GitHubSettingsRow
from repopilot.persistence.settings import (
    GitHubEncryptionUnavailableError,
    GitHubSettingsRepository,
    SettingsStorageError,
)


WORKBENCH_TOKEN = "test-workbench-token-not-a-real-credential"
MARKER_TOKEN = "test-github-token-not-a-real-credential"


def serialize_pair(key, private_format=serialization.PrivateFormat.OpenSSH, encryption=None):
    public = key.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    ).decode()
    private = key.private_bytes(
        serialization.Encoding.PEM,
        private_format,
        encryption or serialization.NoEncryption(),
    ).decode()
    return public, private


@pytest.fixture
def key_pair():
    return serialize_pair(ed25519.Ed25519PrivateKey.generate())


@pytest.fixture
def encryption_key():
    return Fernet.generate_key()


@pytest_asyncio.fixture
async def database():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL must point to a dedicated PostgreSQL test database")
    schema = "github_test_" + uuid.uuid4().hex
    admin = create_async_engine(url, hide_parameters=True)
    engine = None
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(
            url,
            hide_parameters=True,
            pool_pre_ping=True,
            connect_args={"server_settings": {"search_path": schema}},
        )
        # Bind the real Database implementation without opening its default engine.
        isolated = Database.__new__(Database)
        isolated.engine = engine
        isolated.sessions = async_sessionmaker(engine, expire_on_commit=False)
        await isolated.initialize()
        yield isolated
    finally:
        if engine is not None:
            await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.fixture
def repository(database, encryption_key):
    return GitHubSettingsRepository(database, SecretStr(encryption_key.decode()))


@pytest_asyncio.fixture
async def route_app(database, encryption_key, monkeypatch):
    redis_url = os.environ.get("TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("TEST_REDIS_URL must point to a dedicated Redis test instance")

    keys = []

    def register_key(request):
        if request.method == 'GET':
            return httpx.Response(200, json=keys)
        payload = json.loads(request.content)
        keys.append({'id': len(keys) + 1, 'key': payload['key']})
        return httpx.Response(201, json=keys[-1])

    @asynccontextmanager
    async def open_app(*, github_transport=None, master_key=encryption_key):
        key = master_key.decode() if isinstance(master_key, bytes) else master_key
        runtime = AppSettings(
            _env_file=None,
            api_token=SecretStr(WORKBENCH_TOKEN),
            github_credentials_key=SecretStr(key) if key is not None else None,
            database_url=os.environ["TEST_DATABASE_URL"],
            redis_url=redis_url,
        )
        with monkeypatch.context() as patch:
            patch.setattr(app_module, "Database", lambda url: database)
            patch.setattr(
                app_module, 'GitHubClient', lambda: GitHubClient(
                    transport=github_transport or httpx.MockTransport(register_key),
                )
            )
            app = app_module.create_app(runtime)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                    headers={"Authorization": f"Bearer {WORKBENCH_TOKEN}"},
                ) as client:
                    yield client

    yield open_app


@pytest_asyncio.fixture
async def route_client(route_app):
    async with route_app() as client:
        yield client


async def stored_row(database):
    async with database.session() as session:
        row = await session.scalar(select(GitHubSettingsRow).where(GitHubSettingsRow.id == 1))
        assert row is not None
        return row.public_key, row.private_key_ciphertext, row.api_token_ciphertext




@pytest.mark.parametrize("algorithm", ["ed25519", "rsa", "ecdsa"])
@pytest.mark.parametrize("private_format", [
    serialization.PrivateFormat.OpenSSH, serialization.PrivateFormat.PKCS8,
])
def test_supported_pairs_normalize_comments_and_crlf(algorithm, private_format):
    factories = {
        "ed25519": ed25519.Ed25519PrivateKey.generate,
        "rsa": lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048),
        "ecdsa": lambda: ec.generate_private_key(ec.SECP256R1()),
    }
    public, private = serialize_pair(factories[algorithm](), private_format)
    assert normalize_ssh_public_key(f"  {public} test@example.invalid  ") == public
    assert validate_ssh_pair(public + " another-comment", "\n" + private.replace("\n", "\r\n")) == (
        public, private,
    )
    assert validate_ssh_pair(public, "  \r\n ") == (public, "")
    assert normalize_ssh_public_key(" \n\t ") == ""


@pytest.mark.parametrize("private_format", [
    serialization.PrivateFormat.OpenSSH, serialization.PrivateFormat.PKCS8,
])
def test_encrypted_private_keys_are_rejected(private_format):
    public, private = serialize_pair(
        ed25519.Ed25519PrivateKey.generate(), private_format,
        serialization.BestAvailableEncryption(b"test-passphrase"),
    )
    with pytest.raises(InvalidGitHubSettingsError) as error:
        validate_ssh_pair(public, private)
    assert str(error.value) == "Passphrase-protected SSH private keys are not supported"
    assert private not in str(error.value)


def test_traditional_rsa_pem_is_accepted():
    public, private = serialize_pair(
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        serialization.PrivateFormat.TraditionalOpenSSL,
    )
    assert validate_ssh_pair(public, private) == (public, private)


def test_invalid_or_unsupported_keys_and_mismatched_pairs(key_pair):
    public, private = key_pair
    other_public, _ = serialize_pair(ed25519.Ed25519PrivateKey.generate())
    unsupported = dsa.generate_private_key(key_size=1024)
    unsupported_private = unsupported.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    numbers = unsupported.public_key().public_numbers()
    parameters = numbers.parameter_numbers
    fields = [b"ssh-dss"]
    for value in [parameters.p, parameters.q, parameters.g, numbers.y]:
        encoded = value.to_bytes((value.bit_length() + 7) // 8, "big")
        fields.append(b"\x00" + encoded if encoded[0] & 0x80 else encoded)
    blob = b"".join(len(field).to_bytes(4, "big") + field for field in fields)
    unsupported_public = "ssh-dss " + base64.b64encode(blob).decode()
    for invalid in ["not-a-public-key", public + "\n" + other_public, unsupported_public,
                    "sk-ssh-ed25519@openssh.com invalid-data"]:
        with pytest.raises(InvalidGitHubSettingsError, match="^Invalid SSH public key$"):
            normalize_ssh_public_key(invalid)
    for invalid in ["private-secret-marker", unsupported_private]:
        with pytest.raises(InvalidGitHubSettingsError) as error:
            validate_ssh_pair(public, invalid)
        assert str(error.value) == "Invalid or unsupported SSH private key"
        assert invalid not in str(error.value)
    with pytest.raises(InvalidGitHubSettingsError, match="public and private keys do not match"):
        validate_ssh_pair(other_public, private)
    with pytest.raises(InvalidGitHubSettingsError, match="public key is required"):
        validate_ssh_pair("", private)


@pytest.mark.parametrize("token", ["abc def", "abc\tdef", "abc\ndef", "abc\x00def", "abcé", "abc\x7f"])
def test_invalid_tokens_are_safe(token):
    with pytest.raises(InvalidGitHubSettingsError) as error:
        normalize_github_api_token(token)
    assert str(error.value) == "Invalid GitHub API token"
    assert token not in str(error.value)


def test_tokens_trim_without_requiring_a_provider_prefix():
    assert normalize_github_api_token(" \t custom-token-123 \n") == "custom-token-123"
    assert normalize_github_api_token(" \n") == ""


@pytest.mark.parametrize('payload', [
    {'api_token': 'x' * 4097}, {'public_key': 'ssh-public'}, {'private_key': 'secret'},
])
def test_update_rejects_oversized_token_and_manual_keys(payload):
    with pytest.raises(ValidationError):
        GitHubSettingsUpdate(**payload)


async def test_generated_pair_encrypted_and_reused_after_restart(database, repository, encryption_key):
    saved = await repository.save(GitHubSettingsUpdate(api_token=' ' + MARKER_TOKEN + ' '))
    private = saved.private_key.get_secret_value()
    assert isinstance(serialization.load_ssh_private_key(private.encode(), None), ed25519.Ed25519PrivateKey)
    assert validate_ssh_pair(saved.public_key, private) == (saved.public_key, private)
    first = await stored_row(database)
    cipher = Fernet(encryption_key)
    assert first[1] != private and cipher.decrypt(first[1].encode()).decode() == private
    assert first[2] != MARKER_TOKEN and cipher.decrypt(first[2].encode()).decode() == MARKER_TOKEN
    await repository.save(GitHubSettingsUpdate())
    await repository.save(GitHubSettingsUpdate(api_token=None))
    await database.initialize()
    assert await stored_row(database) == first
    rebuilt = GitHubSettingsRepository(database, SecretStr(encryption_key.decode()))
    assert await rebuilt.load() == saved
    await rebuilt.save(GitHubSettingsUpdate(api_token='replacement-token'))
    replaced = await stored_row(database)
    assert replaced[:2] == first[:2]
    assert cipher.decrypt(replaced[2].encode()) == b'replacement-token'


@pytest.mark.parametrize('algorithm', ['rsa', 'ecdsa'])
async def test_legacy_pairs_reused_without_secret_rewrite(database, repository, encryption_key, algorithm):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if algorithm == 'rsa' else ec.generate_private_key(ec.SECP256R1())
    public, private = serialize_pair(key, serialization.PrivateFormat.PKCS8)
    cipher = Fernet(encryption_key)
    async with database.session() as session, session.begin():
        row = await session.get(GitHubSettingsRow, 1)
        row.public_key = public
        row.private_key_ciphertext = cipher.encrypt(private.encode()).decode()
    before = await stored_row(database)
    saved = await repository.save(GitHubSettingsUpdate(api_token=MARKER_TOKEN))
    assert saved.public_key == public and saved.private_key.get_secret_value() == private
    assert (await stored_row(database))[:2] == before[:2]


async def test_public_only_legacy_config_generates_complete_pair(database, repository, key_pair):
    async with database.session() as session, session.begin():
        row = await session.get(GitHubSettingsRow, 1)
        row.public_key = key_pair[0]
    saved = await repository.save(GitHubSettingsUpdate(api_token=MARKER_TOKEN))
    assert saved.public_key != key_pair[0]
    assert validate_ssh_pair(saved.public_key, saved.private_key.get_secret_value())[0] == saved.public_key


async def test_malformed_legacy_pair_never_rotates_or_overwrites(database, repository, encryption_key):
    async with database.session() as session, session.begin():
        row = await session.get(GitHubSettingsRow, 1)
        row.private_key_ciphertext = Fernet(encryption_key).encrypt(b'malformed-private-marker').decode()
    before = await stored_row(database)
    with pytest.raises(InvalidGitHubSettingsError):
        await repository.save(GitHubSettingsUpdate(api_token=MARKER_TOKEN))
    assert await stored_row(database) == before


@pytest.mark.parametrize('token', ['', ' \n ', 'invalid token marker'])
async def test_token_validation_rolls_back(repository, database, token):
    await repository.save(GitHubSettingsUpdate(api_token=MARKER_TOKEN))
    before = await stored_row(database)
    with pytest.raises(InvalidGitHubSettingsError):
        await repository.save(GitHubSettingsUpdate(api_token=token))
    assert await stored_row(database) == before


async def test_missing_initial_token_does_not_generate_pair(repository, database):
    with pytest.raises(InvalidGitHubSettingsError):
        await repository.save(GitHubSettingsUpdate())
    assert await stored_row(database) == ('', '', '')


async def test_commit_failure_rolls_back_generated_pair(repository, database):
    async with database.engine.begin() as connection:
        await connection.execute(text("""
            CREATE FUNCTION reject_github_commit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'database-secret-error-marker'; END; $$;
        """))
        await connection.execute(text("""
            CREATE CONSTRAINT TRIGGER reject_github_commit
            AFTER UPDATE ON github_settings DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION reject_github_commit()
        """))
    with pytest.raises(SettingsStorageError):
        await repository.save(GitHubSettingsUpdate(api_token=MARKER_TOKEN))
    assert await stored_row(database) == ('', '', '')


async def test_encryption_failure_rolls_back_generated_pair(repository, database, encryption_key, monkeypatch):
    cipher = Fernet(encryption_key)
    class FailingCipher:
        calls = 0
        def encrypt(self, value):
            self.calls += 1
            if self.calls == 2:
                raise ValueError('secret-error-marker')
            return cipher.encrypt(value)
    monkeypatch.setattr(repository, '_fernet', FailingCipher())
    with pytest.raises(SettingsStorageError):
        await repository.save(GitHubSettingsUpdate(api_token=MARKER_TOKEN))
    assert await stored_row(database) == ('', '', '')


async def test_concurrent_first_saves_share_single_pair(repository, database):
    entered = 0
    both_waiting = asyncio.Event()
    def watch_lock(connection, cursor, statement, parameters, context, executemany):
        nonlocal entered
        if 'github_settings' in statement and 'FOR UPDATE' in statement:
            entered += 1
            if entered == 2:
                both_waiting.set()
    async with database.session() as blocker, blocker.begin():
        await blocker.scalar(select(GitHubSettingsRow).where(GitHubSettingsRow.id == 1).with_for_update())
        event.listen(database.engine.sync_engine, 'before_cursor_execute', watch_lock)
        tasks = [asyncio.create_task(repository.save(GitHubSettingsUpdate(api_token=token))) for token in ['first-token', 'second-token']]
        try:
            await asyncio.wait_for(both_waiting.wait(), 10)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            event.remove(database.engine.sync_engine, 'before_cursor_execute', watch_lock)
    results = await asyncio.wait_for(asyncio.gather(*tasks), 10)
    assert results[0].public_key == results[1].public_key
    assert results[0].private_key == results[1].private_key
    assert (await repository.load()).public_key == results[0].public_key


@pytest.mark.parametrize('key', [None, '', '  ', 'invalid-key'])
async def test_invalid_master_key_required_for_empty_storage(database, key):
    repository = GitHubSettingsRepository(database, SecretStr(key) if key is not None else None)
    for operation in [repository.load(), repository.save(GitHubSettingsUpdate(api_token=MARKER_TOKEN))]:
        with pytest.raises(GitHubEncryptionUnavailableError):
            await operation
    assert await stored_row(database) == ('', '', '')


async def test_wrong_key_cannot_read_or_overwrite(database, repository):
    await repository.save(GitHubSettingsUpdate(api_token=MARKER_TOKEN))
    before = await stored_row(database)
    wrong = GitHubSettingsRepository(database, SecretStr(Fernet.generate_key().decode()))
    for operation in [wrong.load(), wrong.save(GitHubSettingsUpdate(api_token='replacement'))]:
        with pytest.raises(SettingsStorageError):
            await operation
    assert await stored_row(database) == before


async def test_routes_require_authentication(route_client):
    for method, path, payload in [
        ('GET', '/api/settings/github', None),
        ('PUT', '/api/settings/github', {'api_token': MARKER_TOKEN}),
        ('POST', '/api/settings/github/authorize', None),
    ]:
        response = await route_client.request(method, path, json=payload, headers={'Authorization': ''})
        assert response.status_code == 401


async def test_save_and_replace_token_never_contact_github(route_app, database, repository):
    requests = []

    def unexpected_github_request(request):
        requests.append(request)
        return httpx.Response(401)

    async with route_app(github_transport=httpx.MockTransport(unexpected_github_request)) as client:
        response = await client.put('/api/settings/github', json={'api_token': MARKER_TOKEN})
        assert response.status_code == 200
        settings = response.json()
        assert set(settings) == {'public_key', 'private_key_configured', 'api_token_configured'}
        assert settings['private_key_configured'] and settings['api_token_configured']
        private = (await repository.load()).private_key.get_secret_value()
        assert private not in response.text and MARKER_TOKEN not in response.text
        before = await stored_row(database)
        replacement = await client.put('/api/settings/github', json={'api_token': 'replacement-token'})
        assert replacement.status_code == 200 and replacement.json() == settings
        assert (await repository.load()).api_token.get_secret_value() == 'replacement-token'
        assert (await stored_row(database))[:2] == before[:2]
        assert requests == []


async def test_authorize_reuses_saved_pair_without_writes_after_restart(route_app, database, repository):
    async with route_app() as client:
        settings = (await client.put('/api/settings/github', json={'api_token': MARKER_TOKEN})).json()
        before = await stored_row(database)
        response = await client.post('/api/settings/github/authorize')
        assert response.status_code == 200
        result = response.json()
        assert result['settings'] == settings
        assert result['registration']['status'] == 'created'
        assert result['registration']['public_key'] == settings['public_key']
        private = (await repository.load()).private_key.get_secret_value()
        assert private not in response.text and MARKER_TOKEN not in response.text
        assert await stored_row(database) == before
    async with route_app() as client:
        assert (await client.get('/api/settings/github')).json() == settings
        retry = await client.post('/api/settings/github/authorize')
        assert retry.json()['settings'] == settings
        assert retry.json()['registration']['status'] == 'already_exists'
        assert await stored_row(database) == before
        assert (await client.post('/api/settings/github/public-key')).status_code == 404


@pytest.mark.parametrize('missing', ['all', 'token', 'public_key', 'private_key'])
async def test_authorize_requires_saved_credentials_without_writes_or_network(route_app, database, repository, missing):
    if missing != 'all':
        await repository.save(GitHubSettingsUpdate(api_token=MARKER_TOKEN))
        async with database.session() as session, session.begin():
            row = await session.scalar(select(GitHubSettingsRow).with_for_update())
            if missing == 'token':
                row.api_token_ciphertext = ''
            elif missing == 'public_key':
                row.public_key = ''
            else:
                row.private_key_ciphertext = ''
    before = await stored_row(database)
    requests = []

    def unexpected_github_request(request):
        requests.append(request)
        return httpx.Response(401)

    async with route_app(github_transport=httpx.MockTransport(unexpected_github_request)) as client:
        response = await client.post('/api/settings/github/authorize')
        assert response.status_code == 422
        assert 'Save' in response.json()['detail']
        assert MARKER_TOKEN not in response.text
        assert requests == []
    assert await stored_row(database) == before


@pytest.mark.parametrize('payload', [
    {'public_key': 'ssh-marker', 'api_token': MARKER_TOKEN},
    {'private_key': 'private-secret-marker', 'api_token': MARKER_TOKEN},
    {'api_token': 'invalid token marker'}, {'api_token': 'secret-length-marker' * 300},
])
async def test_route_rejects_legacy_fields_and_redacts_secrets(route_client, database, payload):
    response = await route_client.put('/api/settings/github', json=payload)
    assert response.status_code == 422
    for secret in [MARKER_TOKEN, 'private-secret-marker', 'invalid token marker', 'secret-length-marker']:
        assert secret not in response.text
    assert await stored_row(database) == ('', '', '')


@pytest.mark.parametrize('master_key', [None, '', ' ', 'invalid-key'])
async def test_missing_master_key_only_disables_github(route_app, master_key):
    async with route_app(master_key=master_key) as client:
        for method, path, payload in [
            ('GET', '/api/settings/github', None),
            ('PUT', '/api/settings/github', {'api_token': MARKER_TOKEN}),
            ('POST', '/api/settings/github/authorize', None),
        ]:
            response = await client.request(method, path, json=payload)
            assert response.status_code == 503
            assert MARKER_TOKEN not in response.text
        assert (await client.post('/api/auth')).status_code == 200
        assert (await client.get('/api/settings')).status_code == 200


async def test_wrong_key_route_cannot_mutate_saved_credentials(route_app, database):
    async with route_app() as client:
        assert (await client.put('/api/settings/github', json={'api_token': MARKER_TOKEN})).status_code == 200
    before = await stored_row(database)
    async with route_app(master_key=Fernet.generate_key()) as client:
        assert (await client.get('/api/settings/github')).status_code == 500
        assert (await client.put('/api/settings/github', json={'api_token': 'replacement'})).status_code == 500
        assert (await client.post('/api/settings/github/authorize')).status_code == 500
    assert await stored_row(database) == before


async def test_timeout_after_remote_create_preserves_pair_and_retry_converges(route_app, database, repository):
    keys = []
    posts = []
    async def github(request):
        # Authorization must not hold a database write lock during network I/O.
        async with database.session() as session, session.begin():
            await session.execute(text("SET LOCAL lock_timeout = '1s'"))
            row = await session.scalar(select(GitHubSettingsRow).with_for_update())
            assert row.private_key_ciphertext and row.api_token_ciphertext
        assert request.url.host == 'api.github.com'
        assert request.headers['Authorization'] == f'Bearer {MARKER_TOKEN}'
        if request.method == 'GET':
            return httpx.Response(200, json=keys)
        body = json.loads(request.content)
        assert set(body) == {'title', 'key'}
        assert body['key'] == row.public_key
        posts.append(body)
        keys.append({'id': 101, 'key': body['key']})
        raise httpx.ReadTimeout('secret-remote-error', request=request)
    async with route_app(github_transport=httpx.MockTransport(github)) as client:
        saved = await client.put('/api/settings/github', json={'api_token': MARKER_TOKEN})
        assert saved.status_code == 200
        before = await stored_row(database)
        response = await client.post('/api/settings/github/authorize')
        assert response.status_code == 502
        assert MARKER_TOKEN not in response.text and 'secret-remote-error' not in response.text
        assert await stored_row(database) == before
        retry = await client.post('/api/settings/github/authorize')
        assert retry.status_code == 200 and retry.json()['registration']['status'] == 'already_exists'
        assert len(posts) == 1 and await stored_row(database) == before


async def test_upstream_unauthorized_preserves_local_configuration_and_session(route_app, database):
    async with route_app(github_transport=httpx.MockTransport(lambda request: httpx.Response(401))) as client:
        saved = await client.put('/api/settings/github', json={'api_token': MARKER_TOKEN})
        assert saved.status_code == 200
        before = await stored_row(database)
        response = await client.post('/api/settings/github/authorize')
        assert response.status_code == 502
        assert MARKER_TOKEN not in response.text
        current = await client.get('/api/settings/github')
        assert current.json() == saved.json()
        assert await stored_row(database) == before
        assert (await client.post('/api/auth')).status_code == 200


async def test_existing_model_contract_preserves_and_clears_key_by_base_url(route_client):
    response = await route_client.get("/api/settings")
    assert response.status_code == 200
    assert response.json() == {"base_url": "https://api.openai.com/v1", "model": "", "api_key_configured": False}
    response = await route_client.put("/api/settings/model", json={
        "base_url": "https://models.example.invalid/v1/", "model": " model-a ", "api_key": "model-secret-marker",
    })
    assert response.status_code == 200
    assert response.json() == {"base_url": "https://models.example.invalid/v1", "model": "model-a", "api_key_configured": True}
    assert "model-secret-marker" not in response.text
    response = await route_client.put("/api/settings/model", json={
        "base_url": "https://models.example.invalid/v1", "model": "model-b",
    })
    assert response.json() == {"base_url": "https://models.example.invalid/v1", "model": "model-b", "api_key_configured": True}
    response = await route_client.put("/api/settings/model", json={
        "base_url": "https://other.example.invalid/v1", "model": "model-c",
    })
    assert response.json() == {"base_url": "https://other.example.invalid/v1", "model": "model-c", "api_key_configured": False}
    assert (await route_client.get("/api/settings")).json() == response.json()


async def test_missing_saved_row_is_not_recreated_by_repository(database, repository):
    async with database.engine.begin() as connection:
        await connection.execute(text("DELETE FROM github_settings WHERE id = 1"))
    for operation in [repository.load(), repository.save(GitHubSettingsUpdate(api_token=MARKER_TOKEN))]:
        with pytest.raises(SettingsStorageError) as error:
            await operation
        assert str(error.value) == "Saved GitHub settings are unavailable"
    async with database.session() as session:
        assert await session.scalar(select(GitHubSettingsRow)) is None


async def test_real_route_storage_failure_returns_error(route_client, database):
    async with database.engine.begin() as connection:
        await connection.execute(text("DROP TABLE github_settings"))
    for method, path, payload, detail in [
        ("GET", "/api/settings/github", None, "Unable to load GitHub settings"),
        ("PUT", "/api/settings/github", {"api_token": MARKER_TOKEN},
         "Unable to save GitHub settings"),
        ("POST", "/api/settings/github/authorize", None, "Unable to load GitHub settings"),
    ]:
        response = await route_client.request(method, path, json=payload)
        assert response.status_code == 500
        assert response.json() == {"detail": detail}
        assert MARKER_TOKEN not in response.text
