from io import BytesIO
import json

from nexus.core.command_bridge.http import create_command_bridge_app, CommandBridgeConfig


class DummyRouter:
    pass


def start_response(status, headers):
    start_response.status = status
    start_response.headers = dict(headers)


def make_environ(path: str, method: str = "POST", body: dict = None, auth_token: str = "test-token"):
    b = json.dumps(body or {}).encode("utf-8")
    return {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(b)),
        "wsgi.input": BytesIO(b),
        "HTTP_AUTHORIZATION": f"Bearer {auth_token}",
    }


def test_linkedin_publish_dry_run_success():
    config = CommandBridgeConfig(auth_token="test-token")
    app = create_command_bridge_app(DummyRouter(), config=config)
    payload = {"content": "Hello LinkedIn", "campaign_id": "camp-1", "nexus_id": "n-123"}
    env = make_environ("/api/v1/social/linkedin/publish", body=payload)
    result = app(env, start_response)
    body = b"".join(result)
    data = json.loads(body.decode())
    assert start_response.status.startswith("200")
    assert data.get("ok") is True
    assert data.get("dry_run") is True


def test_linkedin_publish_missing_content_bad_request():
    config = CommandBridgeConfig(auth_token="test-token")
    app = create_command_bridge_app(DummyRouter(), config=config)
    payload = {"campaign_id": "camp-1", "nexus_id": "n-123"}
    env = make_environ("/api/v1/social/linkedin/publish", body=payload)
    result = app(env, start_response)
    body = b"".join(result)
    data = json.loads(body.decode())
    assert start_response.status.startswith("400")
    assert data.get("ok") is False or data.get("error")
