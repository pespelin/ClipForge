import json
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.security import (
    OAuthAuthorizationState,
    OAuthStateStoreError,
    RedisOAuthAuthorizationStateStore,
)

STATE = "clipforge-test-state-never-real"
VERIFIER = "clipforge-test-code-verifier-never-real"
KEY = f"clipforge:oauth:state:{STATE}"


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int]] = []
        self.getdel_calls: list[str] = []

    async def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.set_calls.append((key, value, ex))

    async def getdel(self, key: str) -> str | None:
        self.getdel_calls.append(key)
        return self.values.pop(key, None)


def make_record() -> OAuthAuthorizationState:
    return OAuthAuthorizationState(
        state=STATE,
        code_verifier=VERIFIER,
        publishing_account_id=7,
    )


async def test_save_uses_namespaced_key_deterministic_payload_and_ttl() -> None:
    redis = FakeRedis()
    store = RedisOAuthAuthorizationStateStore(redis)  # type: ignore[arg-type]

    await store.save(make_record(), ttl_seconds=600)

    assert len(redis.set_calls) == 1
    key, payload, ttl = redis.set_calls[0]
    assert key == KEY
    assert ttl == 600
    assert payload == (
        '{"code_verifier":"clipforge-test-code-verifier-never-real","publishing_account_id":7}'
    )
    assert json.loads(payload) == {
        "code_verifier": VERIFIER,
        "publishing_account_id": 7,
    }


async def test_consume_is_atomic_and_single_use() -> None:
    redis = FakeRedis()
    store = RedisOAuthAuthorizationStateStore(redis)  # type: ignore[arg-type]
    await store.save(make_record(), ttl_seconds=600)

    first = await store.consume(STATE)
    second = await store.consume(STATE)

    assert first == make_record()
    assert second is None
    assert redis.getdel_calls == [KEY, KEY]
    assert KEY not in redis.values


async def test_unknown_or_expired_state_returns_none_without_consuming_other_record() -> None:
    redis = FakeRedis()
    store = RedisOAuthAuthorizationStateStore(redis)  # type: ignore[arg-type]
    await store.save(make_record(), ttl_seconds=600)

    assert await store.consume("unknown-test-state") is None
    assert await store.consume(STATE) == make_record()


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        "[]",
        '{"publishing_account_id":7}',
        '{"code_verifier":"test","publishing_account_id":true}',
    ],
)
async def test_malformed_payload_raises_controlled_error_without_secret_leakage(
    payload: str,
) -> None:
    redis = FakeRedis()
    redis.values[KEY] = payload
    store = RedisOAuthAuthorizationStateStore(redis)  # type: ignore[arg-type]

    with pytest.raises(OAuthStateStoreError) as error:
        await store.consume(STATE)

    assert str(error.value) == "OAuth authorization state store operation failed"
    assert STATE not in str(error.value)
    assert VERIFIER not in repr(error.value)
    assert KEY not in repr(error.value)


@pytest.mark.parametrize("operation", ["save", "consume"])
async def test_redis_failures_are_translated_without_vendor_error_leakage(
    operation: str,
) -> None:
    redis = AsyncMock()
    getattr(redis, "set" if operation == "save" else "getdel").side_effect = RedisConnectionError(
        "test redis failure"
    )
    store = RedisOAuthAuthorizationStateStore(redis)

    with pytest.raises(OAuthStateStoreError) as error:
        if operation == "save":
            await store.save(make_record(), ttl_seconds=600)
        else:
            await store.consume(STATE)

    assert "test redis failure" not in str(error.value)
    assert STATE not in repr(error.value)
    assert VERIFIER not in repr(error.value)


async def test_non_positive_ttl_is_rejected_before_redis_write() -> None:
    redis = AsyncMock()
    store = RedisOAuthAuthorizationStateStore(redis)

    with pytest.raises(OAuthStateStoreError):
        await store.save(make_record(), ttl_seconds=0)

    redis.set.assert_not_awaited()


async def test_serialization_failure_is_controlled_without_redis_write() -> None:
    redis = AsyncMock()
    record = OAuthAuthorizationState(
        state=STATE,
        code_verifier=object(),  # type: ignore[arg-type]
        publishing_account_id=7,
    )
    store = RedisOAuthAuthorizationStateStore(redis)

    with pytest.raises(OAuthStateStoreError) as error:
        await store.save(record, ttl_seconds=600)

    assert STATE not in repr(error.value)
    assert redis.set.await_count == 0
