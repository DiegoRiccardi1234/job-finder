"""GLM/Zhipu base_url is configurable (env GLM_BASE_URL) — the China console
uses a different endpoint than the hardcoded international default."""

from __future__ import annotations

from pathlib import Path

from app.config import load_settings
from app.providers.openai_compat import GLMProvider

_CN = "https://open.bigmodel.cn/api/paas/v4"
_INTL = "https://api.z.ai/api/paas/v4"


def test_glm_base_url_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GLM_BASE_URL", _CN)
    assert load_settings(tmp_path).glm_base_url == _CN
    monkeypatch.delenv("GLM_BASE_URL", raising=False)
    assert load_settings(tmp_path).glm_base_url is None


def test_glm_provider_base_url_override() -> None:
    assert GLMProvider(api_key=None).base_url == _INTL  # default preserved
    assert GLMProvider(api_key=None, base_url=_CN).base_url == _CN
