import re

from app.security import (
    OAuthAuthorizationState,
    OAuthAuthorizationStateStore,
    create_pkce_code_challenge,
    generate_oauth_state,
    generate_pkce_code_verifier,
)


class FakeStateStore:
    async def save(self, record: OAuthAuthorizationState, ttl_seconds: int) -> None:
        pass

    async def consume(self, state: str) -> OAuthAuthorizationState | None:
        return None


def test_oauth_state_record_and_store_contract_are_provider_neutral() -> None:
    record = OAuthAuthorizationState(
        state="test-state",
        code_verifier="test-code-verifier",
        publishing_account_id=7,
    )
    store: OAuthAuthorizationStateStore = FakeStateStore()

    assert record.state == "test-state"
    assert record.code_verifier == "test-code-verifier"
    assert record.publishing_account_id == 7
    assert callable(store.save)
    assert callable(store.consume)


def test_oauth_state_generation_is_secure_random_and_url_safe() -> None:
    first = generate_oauth_state()
    second = generate_oauth_state()

    assert first != second
    assert len(first) >= 43
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first)
    assert re.fullmatch(r"[A-Za-z0-9_-]+", second)


def test_pkce_verifier_generation_is_secure_random_and_rfc_7636_compatible() -> None:
    first = generate_pkce_code_verifier()
    second = generate_pkce_code_verifier()

    assert first != second
    assert 43 <= len(first) <= 128
    assert 43 <= len(second) <= 128
    assert re.fullmatch(r"[A-Za-z0-9._~-]+", first)
    assert re.fullmatch(r"[A-Za-z0-9._~-]+", second)


def test_pkce_s256_challenge_matches_rfc_7636_vector_without_padding() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"

    challenge = create_pkce_code_challenge(verifier)

    assert challenge == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert "=" not in challenge
