"""Memory degraded-mode tests for unavailable embedding gateway."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import httpx
import pytest

CODE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE_DIR))


@pytest.fixture
def memory_module(tmp_path, monkeypatch):
    monkeypatch.setenv("S8_STATE_DIR", str(tmp_path))
    import memory

    module = importlib.reload(memory)
    module._EMBED_WARNING_EMITTED.clear()
    return module


def _embed_503() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://localhost:8108/v1/embed")
    response = httpx.Response(
        503,
        json={"detail": "all embedders unavailable"},
        request=request,
    )
    return httpx.HTTPStatusError(
        "Server error '503 Service Unavailable'",
        request=request,
        response=response,
    )


def test_read_503_uses_keyword_fallback(memory_module, monkeypatch, capsys):
    from schemas import MemoryItem

    memory_module._save([
        MemoryItem(
            id="mem_test",
            kind="fact",
            keywords=["alpha", "report"],
            descriptor="alpha report is available",
            value={"raw": "alpha report contents"},
            embedding=None,
            source="test",
            run_id="run_test",
        )
    ])
    monkeypatch.setattr(memory_module, "_gateway_embed", lambda *a, **k: (_ for _ in ()).throw(_embed_503()))

    hits = memory_module.read("alpha report", top_k=3)

    out = capsys.readouterr().out
    assert [hit.id for hit in hits] == ["mem_test"]
    assert "[memory] embedding unavailable; using keyword fallback" in out
    assert "503 from /v1/embed" in out
    assert "HTTPStatusError" not in out
    assert "item written" not in out
    assert "developer.mozilla.org" not in out


def test_write_503_persists_without_vector(memory_module, monkeypatch, capsys):
    monkeypatch.setattr(memory_module, "_gateway_embed", lambda *a, **k: (_ for _ in ()).throw(_embed_503()))

    item = memory_module.add_fact(
        "beta report is available",
        source="test",
        run_id="run_test",
    )

    out = capsys.readouterr().out
    stored = memory_module._load()
    assert item.embedding is None
    assert stored[-1].id == item.id
    assert stored[-1].embedding is None
    assert "[memory] embedding unavailable; saved item without vector" in out
    assert "HTTPStatusError" not in out
    assert "developer.mozilla.org" not in out


def test_embed_warning_emitted_once_per_operation(memory_module, monkeypatch, capsys):
    monkeypatch.setattr(memory_module, "_gateway_embed", lambda *a, **k: (_ for _ in ()).throw(_embed_503()))

    memory_module.add_fact("gamma report", source="test", run_id="run_test")
    memory_module.add_fact("delta report", source="test", run_id="run_test")

    out = capsys.readouterr().out
    assert out.count("[memory] embedding unavailable; saved item without vector") == 1
