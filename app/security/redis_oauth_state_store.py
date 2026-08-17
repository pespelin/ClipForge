import json
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.security.oauth_state import OAuthAuthorizationState, OAuthStateStoreError


class RedisOAuthAuthorizationStateStore:
    """Redis adapter for TTL-bound, single-use OAuth authorization state."""

    _KEY_PREFIX = "clipforge:oauth:state:"

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client

    async def save(self, record: OAuthAuthorizationState, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise OAuthStateStoreError

        try:
            payload = json.dumps(
                {
                    "code_verifier": record.code_verifier,
                    "publishing_account_id": record.publishing_account_id,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            await self._redis.set(self._key(record.state), payload, ex=ttl_seconds)
        except (RedisError, TypeError, ValueError):
            raise OAuthStateStoreError from None

    async def consume(self, state: str) -> OAuthAuthorizationState | None:
        try:
            payload = await self._redis.getdel(self._key(state))
        except RedisError:
            raise OAuthStateStoreError from None

        if payload is None:
            return None

        try:
            data = self._deserialize(payload)
            return OAuthAuthorizationState(
                state=state,
                code_verifier=data["code_verifier"],
                publishing_account_id=data["publishing_account_id"],
            )
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            raise OAuthStateStoreError from None

    @classmethod
    def _key(cls, state: str) -> str:
        return f"{cls._KEY_PREFIX}{state}"

    @staticmethod
    def _deserialize(payload: str | bytes) -> dict[str, Any]:
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise TypeError

        code_verifier = data.get("code_verifier")
        publishing_account_id = data.get("publishing_account_id")
        if not isinstance(code_verifier, str) or not code_verifier:
            raise ValueError
        if not isinstance(publishing_account_id, int) or isinstance(publishing_account_id, bool):
            raise ValueError
        return data
