"""Unit tests for `app.cv_ingest`."""

from __future__ import annotations

import pytest

from app.cv_ingest import extract_markdown_from_upload, summarize_profile


def test_extract_txt_roundtrip() -> None:
    text = "Diego Developer\nPython, SQL, QA"
    out = extract_markdown_from_upload("cv.txt", text.encode("utf-8"))
    assert out == text


def test_extract_md_roundtrip() -> None:
    text = "# CV\n\n- Python\n- Selenium"
    out = extract_markdown_from_upload("cv.md", text.encode("utf-8"))
    assert out == text


def test_extract_rejects_unsupported_format() -> None:
    with pytest.raises(RuntimeError):
        extract_markdown_from_upload("cv.jpg", b"not-supported")


def test_extract_is_case_insensitive() -> None:
    out = extract_markdown_from_upload("CV.TXT", b"hello")
    assert out == "hello"


def test_summarize_profile_detects_skills_and_roles() -> None:
    md = "I have experience with Python, React, QA automation and Selenium."
    result = summarize_profile(md)

    assert "python" in result["skills"]
    assert "react" in result["skills"]
    assert "qa" in result["skills"] or "testing" in result["skills"]

    preferred = " ".join(result["preferred_roles"]).lower()
    assert "python" in preferred or "automation" in preferred or "qa" in preferred


def test_summarize_profile_extracts_last_year() -> None:
    md = "Graduated 2021. Worked 2022-2024."
    result = summarize_profile(md)
    assert result["graduation_year"] == "2024"


def test_summarize_profile_empty_markdown() -> None:
    result = summarize_profile("")
    assert result["skills"] == []
    assert result["preferred_roles"] == []
    assert result["graduation_year"] == ""
