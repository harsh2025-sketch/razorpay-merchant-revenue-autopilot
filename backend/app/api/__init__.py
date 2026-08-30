"""Product API package (Task 15).

``schemas`` defines the strict, secret-free request/response contract and
``router`` exposes it under ``/api/v1``. Both modules are presentation only:
every lifecycle decision stays in ``app.engines`` and ``app.services``.
"""

from app.api.router import router

__all__ = ["router"]
