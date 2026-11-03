from __future__ import annotations

import ast
import struct
from pathlib import Path

from PIL import Image

from scripts.build_windows_icon import DEFAULT_OUTPUT, ICON_SIZES, build_icon


def test_committed_icon_contains_every_native_size_and_real_transparency() -> None:
    with Image.open(DEFAULT_OUTPUT) as icon:
        assert icon.format == "ICO"
        assert icon.ico.sizes() == {(size, size) for size in ICON_SIZES}
        for size in ICON_SIZES:
            frame = icon.ico.getimage((size, size)).convert("RGBA")
            alpha = frame.getchannel("A")
            assert alpha.getextrema() == (0, 255)
            assert frame.getpixel((0, 0))[3] == 0
            assert frame.getpixel((size // 2, int(size * 0.75)))[3] >= 240
            # The handle opening is actually cut out, rather than drawn over
            # an opaque square background.
            assert frame.getpixel((size // 2, int(size * 0.23)))[3] < 10
        largest = icon.ico.getimage((256, 256)).convert("RGBA")
        assert largest.getpixel((128, 180)) == (99, 102, 241, 255)


def test_ico_directory_encodes_all_sizes_including_windows_256_sentinel() -> None:
    content = DEFAULT_OUTPUT.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", content)
    assert (reserved, kind, count) == (0, 1, len(ICON_SIZES))
    dimensions = []
    for index in range(count):
        width, height, _, _, _, _, byte_count, offset = struct.unpack_from(
            "<BBBBHHII", content, 6 + index * 16
        )
        dimensions.append((width or 256, height or 256))
        assert offset + byte_count <= len(content)
        assert content[offset : offset + 8] == b"\x89PNG\r\n\x1a\n"
    assert dimensions == [(size, size) for size in ICON_SIZES]


def test_icon_generation_is_byte_reproducible_and_matches_committed_asset(tmp_path: Path) -> None:
    first, second = tmp_path / "first.ico", tmp_path / "second.ico"
    build_icon(first)
    build_icon(second)
    assert first.read_bytes() == second.read_bytes()
    # Pillow's native PNG compressor can differ between Windows/Linux wheels.
    # Verify the committed artwork exactly after decoding, rather than requiring
    # a particular lossless compression stream from another platform.
    with Image.open(first) as generated, Image.open(DEFAULT_OUTPUT) as committed:
        assert generated.ico.sizes() == committed.ico.sizes()
        for size in ICON_SIZES:
            actual = generated.ico.getimage((size, size)).convert("RGBA")
            expected = committed.ico.getimage((size, size)).convert("RGBA")
            assert actual.tobytes() == expected.tobytes(), f"ICO frame {size} differs"


def test_all_three_frozen_executables_reference_the_verified_icon() -> None:
    spec = DEFAULT_OUTPUT.parents[1] / "JobFinder.spec"
    tree = ast.parse(spec.read_text(encoding="utf-8"))
    executables = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "EXE":
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            name, icon = keywords["name"], keywords["icon"]
            assert isinstance(name, ast.Constant) and isinstance(icon, ast.Constant)
            executables[name.value] = icon.value
    assert executables == {
        "JobFinder": "web/jobfinder.ico",
        "Updater": "web/jobfinder.ico",
        "JobFinderMCP": "web/jobfinder.ico",
    }
