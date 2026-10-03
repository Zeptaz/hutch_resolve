from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.resolve.app.body_limit import RequestBodyLimitMiddleware


def test_rejects_declared_oversize_before_route_reads_body():
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=8)
    app.state.called = False

    @app.post("/body")
    async def read(request: Request):
        app.state.called = True
        return {"size": len(await request.body())}

    with TestClient(app) as client:
        response = client.post("/body", content=b"012345678")

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
    assert app.state.called is False


def test_rejects_chunked_oversize_and_accepts_body_at_limit():
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=8)

    @app.post("/body")
    async def read(request: Request):
        return {"size": len(await request.body())}

    with TestClient(app) as client:
        accepted = client.post("/body", content=b"12345678", headers={"content-length": "8"})
        rejected = client.post("/body", content=b"123456789", headers={"transfer-encoding": "chunked"})

    assert accepted.status_code == 200 and accepted.json()["size"] == 8
    assert rejected.status_code == 413
