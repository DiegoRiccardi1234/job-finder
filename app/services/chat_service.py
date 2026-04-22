"""Backwards-compatible facade.

The chat logic moved to ``app.services.chat`` (split into state, context,
intents, prompts, fallback, handler). This module re-exports the public
handler so existing imports keep working.
"""

from app.services.chat import handle_chat_message

__all__ = ["handle_chat_message"]
