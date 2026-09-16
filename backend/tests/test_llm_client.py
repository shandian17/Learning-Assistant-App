import httpx
import pytest

from app.services import LLMClient, LLMServiceError, parse_json_content


def test_llm_client_builds_chat_endpoint_from_base_url(app):
    app.config.update(
        LLM_BASE_URL="https://provider.example/v1/",
        LLM_API_KEY="test-key",
        LLM_MODEL="test-model",
    )
    with app.app_context():
        client = LLMClient()

    assert client.base_url == "https://provider.example/v1"
    assert client.api_url == "https://provider.example/v1/chat/completions"


@pytest.mark.parametrize(
    "content",
    [
        '{"ok":true}',
        'json\n{"ok":true}',
        '```json\n{"ok":true}\n```',
    ],
)
def test_json_response_cleanup(content):
    assert parse_json_content(content) == {"ok": True}


def test_llm_client_retries_transient_failures_three_attempts(app, monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        request = httpx.Request("POST", url)
        if len(calls) < 3:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    monkeypatch.setattr("app.services.llm_client.httpx.post", fake_post)
    monkeypatch.setattr("app.services.llm_client.time.sleep", lambda _seconds: None)
    with app.app_context():
        assert LLMClient().chat_text([{"role": "user", "content": "test"}]) == "ok"
    assert len(calls) == 3


def test_llm_client_does_not_retry_auth_error(app, monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return httpx.Response(401, request=httpx.Request("POST", url))

    monkeypatch.setattr("app.services.llm_client.httpx.post", fake_post)
    with app.app_context(), pytest.raises(LLMServiceError):
        LLMClient().chat_text([{"role": "user", "content": "test"}])
    assert len(calls) == 1
