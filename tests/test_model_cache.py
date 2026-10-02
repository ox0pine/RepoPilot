from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from redis.asyncio import Redis

from repopilot.application.settings import SettingsService
from repopilot.integration.cache import ModelCache
from repopilot.integration.models import ModelProviderError
import repopilot.integration.models as models_module
from repopilot.persistence.settings import StoredModelSettings


@pytest_asyncio.fixture
async def model_cache(monkeypatch):
    url = os.environ.get('TEST_REDIS_URL')
    if not url:
        pytest.skip('TEST_REDIS_URL must point to a dedicated test Redis')
    client = Redis.from_url(
        url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5,
    )
    base_url = f'https://provider.example/{uuid4().hex}/v1'
    other_url = base_url.replace('provider.example', 'other-provider.example')
    tokens = ('cache-test-token-a', 'cache-test-token-b', '')
    cache = ModelCache(client)
    requests = []
    provider = {'status': 200, 'models': ['model-original']}

    def respond(request):
        requests.append((str(request.url), request.headers.get('Authorization')))
        return httpx.Response(
            provider['status'],
            json={'data': [{'id': model} for model in provider['models']]},
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        models_module.httpx, 'AsyncClient',
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )

    async def load():
        return StoredModelSettings(base_url=base_url, api_key=tokens[0])

    service = SettingsService(SimpleNamespace(load=load), cache)
    try:
        await client.ping()
        yield SimpleNamespace(
            client=client, cache=cache, service=service, base_url=base_url,
            other_url=other_url, tokens=tokens, requests=requests, provider=provider,
        )
    finally:
        # Never flush a database: remove only this fixture's unique cache entries.
        try:
            await client.delete(*[
                cache.key(endpoint, token)
                for endpoint in (base_url, other_url) for token in tokens
            ])
        finally:
            await client.aclose()


async def test_cache_hit_avoids_provider_request(model_cache):
    fixture = model_cache
    assert await fixture.service.refresh_models(
        base_url=fixture.base_url + '/', api_key=None,
    ) == ['model-original']
    fixture.provider['status'] = 503
    assert await fixture.service.refresh_models(
        base_url=fixture.base_url, api_key=None,
    ) == ['model-original']
    assert fixture.requests == [
        (fixture.base_url + '/models', 'Bearer ' + fixture.tokens[0]),
    ]


async def test_cache_isolates_endpoint_and_credentials(model_cache):
    fixture = model_cache
    assert await fixture.service.refresh_models(
        base_url=fixture.base_url, api_key=fixture.tokens[0],
    ) == ['model-original']
    fixture.provider['models'] = ['model-other-key']
    assert await fixture.service.refresh_models(
        base_url=fixture.base_url, api_key=fixture.tokens[1],
    ) == ['model-other-key']
    fixture.provider['models'] = ['model-other-endpoint']
    assert await fixture.service.refresh_models(
        base_url=fixture.other_url, api_key=fixture.tokens[0],
    ) == ['model-other-endpoint']
    fixture.provider['models'] = ['model-without-key']
    assert await fixture.service.refresh_models(
        base_url=fixture.other_url, api_key=None,
    ) == ['model-without-key']
    fixture.provider['status'] = 503
    for endpoint, token, expected in (
        (fixture.base_url, fixture.tokens[0], ['model-original']),
        (fixture.base_url, fixture.tokens[1], ['model-other-key']),
        (fixture.other_url, fixture.tokens[0], ['model-other-endpoint']),
        (fixture.other_url, None, ['model-without-key']),
    ):
        assert await fixture.service.refresh_models(base_url=endpoint, api_key=token) == expected
    assert fixture.requests == [
        (fixture.base_url + '/models', 'Bearer ' + fixture.tokens[0]),
        (fixture.base_url + '/models', 'Bearer ' + fixture.tokens[1]),
        (fixture.other_url + '/models', 'Bearer ' + fixture.tokens[0]),
        (fixture.other_url + '/models', None),
    ]


async def test_expired_cache_does_not_mask_provider_failure(model_cache):
    fixture = model_cache
    fixture.cache.ttl_seconds = 1
    assert await fixture.service.refresh_models(
        base_url=fixture.base_url, api_key=fixture.tokens[0],
    ) == ['model-original']
    await asyncio.sleep(1.1)
    fixture.provider['status'] = 503
    with pytest.raises(ModelProviderError):
        await fixture.service.refresh_models(
            base_url=fixture.base_url, api_key=fixture.tokens[0],
        )
    assert await fixture.cache.get(fixture.base_url, fixture.tokens[0]) is None
    fixture.provider.update(status=200, models=['model-fresh'])
    assert await fixture.service.refresh_models(
        base_url=fixture.base_url, api_key=fixture.tokens[0],
    ) == ['model-fresh']
    assert fixture.requests == [
        (fixture.base_url + '/models', 'Bearer ' + fixture.tokens[0]),
    ] * 3


@pytest.mark.parametrize('invalid', [
    'not-json', json.dumps({'models': ['stale']}), json.dumps(['']),
    json.dumps([' \t']), json.dumps(['stale', 3]), b'\xff',
])
async def test_invalid_cache_is_replaced_by_provider_data(model_cache, invalid):
    fixture = model_cache
    await fixture.client.set(fixture.cache.key(fixture.base_url, fixture.tokens[0]), invalid)
    assert await fixture.service.refresh_models(
        base_url=fixture.base_url, api_key=fixture.tokens[0],
    ) == ['model-original']
    fixture.provider['status'] = 503
    assert await fixture.service.refresh_models(
        base_url=fixture.base_url, api_key=fixture.tokens[0],
    ) == ['model-original']
    assert fixture.requests == [
        (fixture.base_url + '/models', 'Bearer ' + fixture.tokens[0]),
    ]
