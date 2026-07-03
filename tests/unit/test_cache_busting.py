"""Cache-busting: asset URLs must carry ?v=<__version__>, injected at serve time.

The version token is bound to ``app.version.__version__`` — a release bump must
propagate to every app-owned asset (index.html, app.js + its module imports,
styles.css + its @import of chat.css) WITHOUT anyone editing the HTML/CSS/JS by
hand. These tests pin both the serve-time mechanism and the committed sources.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.version import __version__

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_web(tmp_path: Path) -> None:
    web = tmp_path / "web"
    (web / "styles").mkdir(parents=True)
    (web / "index.html").write_text(
        '<link rel="stylesheet" href="/web/styles.css?v={{VERSION}}"/>'
        '<span id="infoVersionNumber">{{VERSION}}</span>'
        '<script type="module" src="/web/app.js?v={{VERSION}}"></script>',
        encoding="utf-8",
    )
    (web / "app.js").write_text(
        'import { api } from "./modules/helpers.js";\n'
        'import { initTheme } from "./modules/theme.js";\n',
        encoding="utf-8",
    )
    (web / "modules").mkdir()
    # A module that imports a SIBLING module: this cross-module import must get
    # the same ?v= token, otherwise the browser loads a second i18n.js instance.
    (web / "modules" / "providers.js").write_text(
        'import { t } from "./i18n.js";\nexport const x = t;\n', encoding="utf-8"
    )
    (web / "modules" / "i18n.js").write_text("export const t = (k) => k;\n", encoding="utf-8")
    (web / "styles.css").write_text(
        '@import url("./styles/chat.css?v={{VERSION}}");\n', encoding="utf-8"
    )
    (web / "styles" / "chat.css").write_text("body{color:red}\n", encoding="utf-8")
    (web / "favicon.svg").write_text("<svg/>", encoding="utf-8")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    from app.main import create_app

    _make_web(tmp_path)
    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


def test_index_html_injects_version(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "{{VERSION}}" not in r.text  # placeholder fully replaced
    assert f"/web/styles.css?v={__version__}" in r.text
    assert f"/web/app.js?v={__version__}" in r.text


def test_app_js_injects_version_into_module_imports(client: TestClient) -> None:
    r = client.get("/web/app.js")
    assert r.status_code == 200
    assert f'"./modules/helpers.js?v={__version__}"' in r.text
    assert f'"./modules/theme.js?v={__version__}"' in r.text
    assert "javascript" in r.headers["content-type"]


def test_module_sibling_imports_are_busted_consistently(client: TestClient) -> None:
    """Modules are served through the versioned route too, so a cross-module
    import (``./i18n.js``) carries the SAME ?v= as app.js's import of it — one
    shared module instance, no duplicated i18n state."""
    r = client.get("/web/modules/providers.js")
    assert r.status_code == 200
    assert f'"./i18n.js?v={__version__}"' in r.text
    assert "javascript" in r.headers["content-type"]


def test_unknown_module_returns_404(client: TestClient) -> None:
    assert client.get("/web/modules/does-not-exist.js").status_code == 404


def test_styles_css_injects_version_into_chat_import(client: TestClient) -> None:
    r = client.get("/web/styles.css")
    assert r.status_code == 200
    assert f'"./styles/chat.css?v={__version__}"' in r.text
    assert "css" in r.headers["content-type"]


def test_committed_index_uses_placeholder_not_hardcoded_version() -> None:
    """The real index.html must use the {{VERSION}} token, never a frozen ?v=1.x."""
    html = (_REPO_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "{{VERSION}}" in html
    assert "?v=1." not in html  # no hand-bumped version left behind


def test_committed_styles_busts_chat_css() -> None:
    css = (_REPO_ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    assert "chat.css?v={{VERSION}}" in css
