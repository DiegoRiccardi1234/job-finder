"""Per-domain FastAPI routers.

Each module exposes ``build_router(container) -> APIRouter``; ``app.main``
wires them onto the app. Route bodies are unchanged from the original
monolithic ``app.main`` — they still close over the shared ``AppContainer``.
"""
