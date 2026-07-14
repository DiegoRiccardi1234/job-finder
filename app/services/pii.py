"""Redact personally identifiable information from CV text before it reaches an
LLM provider, with a token map so the candidate's own name can be restored in
generated output (cover letters, tailored resumes).

Used by Privacy Mode (default ON). Scoring and profile-summary calls need no
restore — they consume structured data, not the name in prose — so they discard
the token map. Generation calls redact the input, then ``restore_pii`` the
output so the real name reappears in the text the user actually sends.

Pure-python (regex + str ops only) so mypyc can compile it.
"""

from __future__ import annotations

import re

# Bracketed, all-caps sentinel: unlikely to be reworded by the model and easy to
# restore verbatim. Only the name is ever restored; contacts stay redacted.
NAME_TOKEN = "[[CV_NAME]]"

_URL_RE = re.compile(r"https?://\S+|\bwww\.\S+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Broad phone-ish run (optional +, digits with spaces/dots/dashes/parens); the
# digit-count guard below rejects short sequences (dates, year ranges) so only
# real phone numbers (>= 9 digits) are redacted.
_PHONE_RE = re.compile(r"(?<![\w])\+?\(?\d[\d\s().\-]{7,}\d(?![\w])")
# Address lines: a line starting with a common street prefix (IT + EN). Redacts
# the whole line — CV headers put the address on its own line.
_ADDRESS_RE = re.compile(
    r"(?im)^\s*(?:via|viale|piazza|p\.zza|corso|c\.so|strada|vicolo|largo|"
    r"street|st\.|road|rd\.|avenue|ave\.)\s+.+$"
)


def _sub_phone(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return "[PHONE]" if len(digits) >= 9 else match.group(0)


def redact_pii(text: str, name: str | None = None) -> tuple[str, dict[str, str]]:
    """Return ``(redacted_text, token_map)``.

    Email/phone/address/URL become fixed sentinels (never restored). The name,
    when given and long enough to be safe, becomes ``NAME_TOKEN`` and is recorded
    in ``token_map`` so callers that surface prose can restore it. URL is redacted
    first so digits inside links can't be mistaken for a phone number.
    """
    if not text:
        return text, {}
    redacted = _URL_RE.sub("[URL]", text)
    redacted = _EMAIL_RE.sub("[EMAIL]", redacted)
    redacted = _PHONE_RE.sub(_sub_phone, redacted)
    redacted = _ADDRESS_RE.sub("[ADDRESS]", redacted)

    token_map: dict[str, str] = {}
    clean = (name or "").strip()
    if len(clean) >= 3:
        # Replace the full name first, then each part >= 3 chars, longest-first,
        # word-bounded and case-insensitive, so "Mario Rossi" and stray "Mario"
        # both go. Short parts (initials, "Li") are left to avoid mangling words.
        parts = [clean] + [p for p in re.split(r"\s+", clean) if len(p) >= 3]
        for part in sorted(set(parts), key=len, reverse=True):
            redacted = re.sub(
                r"\b" + re.escape(part) + r"\b", NAME_TOKEN, redacted, flags=re.IGNORECASE
            )
        token_map[NAME_TOKEN] = clean
    return redacted, token_map


def restore_pii(text: str, token_map: dict[str, str]) -> str:
    """Reinstate real values for the tokens recorded by :func:`redact_pii`."""
    if not text or not token_map:
        return text
    for token, value in token_map.items():
        text = text.replace(token, value)
    return text


def restore_contacts(text: str, source_text: str) -> str:
    """Replace fixed contact sentinels in ``text`` with the real values found in
    ``source_text`` (the un-redacted CV).

    Contacts are normally redacted for good — but a tailored résumé is a document
    the user actually sends, so ``[EMAIL]``/``[PHONE]``/``[ADDRESS]``/``[URL]``
    placeholders must become the candidate's real details. The LLM still never
    saw them (redaction happens on the way in); this only touches the local
    output. Uses the first match of each kind (a résumé header carries one set).
    """
    if not text or not source_text:
        return text
    for sentinel, pattern in (
        ("[EMAIL]", _EMAIL_RE),
        ("[URL]", _URL_RE),
        ("[ADDRESS]", _ADDRESS_RE),
    ):
        if sentinel in text:
            match = pattern.search(source_text)
            if match:
                text = text.replace(sentinel, match.group(0).strip())
    if "[PHONE]" in text:
        for match in _PHONE_RE.finditer(source_text):
            if len(re.sub(r"\D", "", match.group(0))) >= 9:
                text = text.replace("[PHONE]", match.group(0).strip())
                break
    return text
