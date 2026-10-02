import json

import pytest

from app.providers import google_provider
from app.providers.google_provider import GoogleProvider


def test_catalog_excludes_models_without_text_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "models": [
            {
                "name": "models/gemini-2.5-flash-lite",
                "supportedGenerationMethods": ["generateContent"],
            },
            {"name": "models/gemini-embedding", "supportedGenerationMethods": ["embedContent"]},
        ]
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode()

    monkeypatch.setattr(google_provider.request, "urlopen", lambda *a, **kw: Response())
    provider = GoogleProvider(api_key=None)
    provider.api_key = "test-key"
    assert provider.list_models() == ["gemini-2.5-flash-lite"]


def test_empty_catalog_uses_supported_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GoogleProvider(api_key=None)
    monkeypatch.setattr(provider, "list_models", list)
    assert provider.select_model() == "gemini-3.5-flash-lite"
    assert provider.select_model("explicit-choice") == "explicit-choice"
