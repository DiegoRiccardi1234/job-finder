from pathlib import Path

import pytest

from app.install_layout import installation_root, state_dir


def test_invalid_marker_cannot_silently_create_an_empty_legacy_database(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / ".jobfinder-layout.json").write_text('{"version":99}')
    with pytest.raises(ValueError, match="Unrecognized"):
        installation_root(app / "JobFinder.exe")
    with pytest.raises(ValueError, match="Unrecognized"):
        state_dir(tmp_path)
    assert not (tmp_path / "data").exists() and not (app / "data").exists()
