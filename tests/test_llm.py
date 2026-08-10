from app import llm as llm_module
from app.config import Settings
from app.llm import LLMClient


def test_per_request_thinking_mode_overrides_default(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "有证据的回答 [1]"}}]}

    def fake_post(_url, headers, json, timeout):
        captured.update({"headers": headers, "payload": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(llm_module.httpx, "post", fake_post)
    client = LLMClient(
        Settings(llm_base_url="https://example.invalid/v1", llm_api_key="test", llm_model="test-model", llm_thinking="disabled")
    )

    response = client.generate("问题", [{"title": "证据", "content": "内容"}], [], thinking_mode="enabled")

    assert response == "有证据的回答 [1]"
    assert captured["payload"]["thinking"] == {"type": "enabled"}
