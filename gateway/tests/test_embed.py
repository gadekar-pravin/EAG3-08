"""V7 embed-endpoint tests. Run from llm_gatewayV7/:  uv run pytest -v tests/test_embed.py

Markers:
  - local:   requires `ollama` running locally with `nomic-embed-text` pulled
  - network: requires GEMINI_API_KEY in ../.env and outbound HTTPS

The tests start an in-process httpx ASGI client against the V7 FastAPI app —
they do NOT require V7 to be running on port 8107. This keeps the test suite
fast (~5s for the four tests) and self-contained.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from dotenv import load_dotenv

# Add parent dir to path so `import main` finds V7's modules.
HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))
load_dotenv(HERE.parent / ".env")  # same .env as V3

EXPECTED_OLLAMA_DIM = 768  # nomic-embed-text
EXPECTED_FALLBACK_DIM = 768  # gemini-embedding-001 with outputDimensionality=768


class _FakeEmbedder:
    name = "fake"
    model = "fake-embedder"

    def __init__(self, outcomes):
        import embedders as E
        self.outcomes = list(outcomes)
        self.calls = 0
        self.state = E.EmbedRateState(rpm=0, cooldown=0.0)

    async def embed(self, text: str, task_type: str) -> dict:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _embedding() -> dict:
    return {"embedding": [0.1, 0.2, 0.3], "model": "fake-embedder", "dim": 3}


def _embedder_configured(name: str) -> bool:
    import main as M
    return any(e.name == name for e in M.app.state.embedders)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def client():
    """In-process ASGI client. Manually drives FastAPI's lifespan so
    app.state.embedders is wired before any test sends a request."""
    import main as M
    transport = httpx.ASGITransport(app=M.app)
    async with M.app.router.lifespan_context(M.app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            timeout=60,
        ) as c:
            yield c


@pytest.fixture
def set_embedders():
    import main as M

    original_embedders = M.app.state.embedders
    original_order = M.app.state.embed_order

    def apply(embedders, order):
        M.app.state.embedders = embedders
        M.app.state.embed_order = order

    yield apply

    M.app.state.embedders = original_embedders
    M.app.state.embed_order = original_order


@pytest.mark.asyncio
async def test_embed_retries_transient_provider_failure_once(client, set_embedders):
    import embedders as E

    fake = _FakeEmbedder([
        E.EmbedderError("gemini HTTP 503: overloaded", status=503),
        _embedding(),
    ])
    set_embedders([fake], ["fake"])

    r = await client.post("/v1/embed", json={
        "text": "retry me",
        "task_type": "retrieval_document",
    })

    assert r.status_code == 200, r.text
    d = r.json()
    assert d["provider"] == "fake"
    assert d["retries"] == 1
    assert fake.calls == 2
    assert fake.state.snapshot()["backoff_remaining"] == 0.0


@pytest.mark.asyncio
async def test_embed_marks_failure_after_retry_exhausted(client, set_embedders):
    import embedders as E

    fake = _FakeEmbedder([
        E.EmbedderError("gemini HTTP 503: overloaded", status=503),
        E.EmbedderError("gemini HTTP 503: still overloaded", status=503),
    ])
    set_embedders([fake], ["fake"])

    r = await client.post("/v1/embed", json={
        "text": "retry me",
        "task_type": "retrieval_query",
    })

    assert r.status_code == 503
    assert fake.calls == 2
    assert fake.state.snapshot()["backoff_remaining"] > 0


@pytest.mark.asyncio
async def test_embed_does_not_retry_non_retryable_provider_failure(client, set_embedders):
    import embedders as E

    fake = _FakeEmbedder([
        E.EmbedderError("bad request", status=400),
    ])
    set_embedders([fake], ["fake"])

    r = await client.post("/v1/embed", json={
        "text": "do not retry me",
        "provider": "fake",
    })

    assert r.status_code == 400
    assert fake.calls == 1


@pytest.mark.local
@pytest.mark.asyncio
async def test_ollama_embed(client):
    """Hits the live Ollama endpoint; asserts shape and dim = 768."""
    if not _embedder_configured("ollama"):
        pytest.skip("ollama embedder not configured")
    r = await client.post("/v1/embed", json={
        "text": "the quick brown fox",
        "task_type": "retrieval_document",
        "provider": "ollama",
    })
    assert r.status_code == 200, r.text
    d = r.json()
    print("ollama:", {k: v for k, v in d.items() if k != "embedding"}, "vec[0:3]:", d["embedding"][:3])
    assert d["provider"] == "ollama"
    assert d["model"]
    assert d["dim"] == EXPECTED_OLLAMA_DIM
    assert isinstance(d["embedding"], list) and len(d["embedding"]) == EXPECTED_OLLAMA_DIM
    assert all(isinstance(x, (int, float)) for x in d["embedding"][:5])


@pytest.mark.network
@pytest.mark.asyncio
async def test_fallback_embed(client):
    """Hits Gemini gemini-embedding-001; asserts shape and dim > 0 (stable)."""
    if not os.getenv("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY not set")
    r = await client.post("/v1/embed", json={
        "text": "the quick brown fox",
        "task_type": "retrieval_document",
        "provider": "gemini",
    })
    assert r.status_code == 200, r.text
    d = r.json()
    print("gemini:", {k: v for k, v in d.items() if k != "embedding"}, "vec[0:3]:", d["embedding"][:3])
    assert d["provider"] == "gemini"
    assert d["model"]
    assert d["dim"] == EXPECTED_FALLBACK_DIM > 0
    assert isinstance(d["embedding"], list) and len(d["embedding"]) == d["dim"]


@pytest.mark.network
@pytest.mark.asyncio
async def test_failover(client, monkeypatch, set_embedders):
    """Point Ollama at an unused port → ring should fall over to Gemini."""
    if not os.getenv("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY not set")
    # Rebuild embedders with a broken Ollama URL, install onto app state.
    import embedders as E
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:1")  # unbound
    monkeypatch.setenv("EMBED_ORDER", "ollama,gemini")
    broken_embedders, order = E.build_embedders()
    set_embedders(broken_embedders, order)

    r = await client.post("/v1/embed", json={
        "text": "fall over please",
        "task_type": "retrieval_query",
    })
    assert r.status_code == 200, r.text
    d = r.json()
    print("failover:", {k: v for k, v in d.items() if k != "embedding"})
    assert d["provider"] == "gemini"
    assert len(d["attempted"]) >= 1 and d["attempted"][0]["provider"] == "ollama"


@pytest.mark.local
@pytest.mark.asyncio
async def test_provider_explicit(client):
    """Pinned provider should appear in the response provider field."""
    if not _embedder_configured("ollama"):
        pytest.skip("ollama embedder not configured")
    r = await client.post("/v1/embed", json={
        "text": "hello world",
        "provider": "ollama",
    })
    assert r.status_code == 200, r.text
    d = r.json()
    print("explicit:", {k: v for k, v in d.items() if k != "embedding"})
    assert d["provider"] == "ollama"
    assert d["dim"] == EXPECTED_OLLAMA_DIM
