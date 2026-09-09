"""Updated client contract: cache the initial vault session, just like an open UI.

Session-security tests use the raw TestClient to exercise missing headers.
"""
from fastapi.testclient import TestClient as BaseTestClient

class TestClient(BaseTestClient):
    __test__ = False

    def __init__(self, app, *args, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        runtime = getattr(app.state, "runtime", None)
        if runtime is not None:
            headers.setdefault("X-LocalNote-Vault-Session", runtime.session_id)
        super().__init__(app, *args, headers=headers, **kwargs)
