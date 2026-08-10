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


def test_query_rewrite_uses_prior_user_questions_and_disables_thinking(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"standalone_query":"HoloQuant 在 GSM8K 上表现如何？","rewritten":true,"reason":"resolved_prior_entity"}'}}]}

    def fake_post(_url, headers, json, timeout):
        captured.update({"headers": headers, "payload": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(llm_module.httpx, "post", fake_post)
    client = LLMClient(Settings(llm_base_url="https://example.invalid/v1", llm_api_key="test", llm_model="test-model"))

    rewrite = client.rewrite_query("它在 GSM8K 上表现如何？", ["介绍 HoloQuant 的量化方法"])

    assert rewrite == {
        "retrieval_query": "HoloQuant 在 GSM8K 上表现如何？",
        "rewritten": True,
        "reason": "resolved_prior_entity",
    }
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert "介绍 HoloQuant 的量化方法" in captured["payload"]["messages"][1]["content"]


def test_session_title_is_short_and_disables_thinking(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ResearchFlow 检索排序机制"}}]}

    def fake_post(_url, headers, json, timeout):
        captured.update({"headers": headers, "payload": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(llm_module.httpx, "post", fake_post)
    client = LLMClient(Settings(llm_base_url="https://example.invalid/v1", llm_api_key="test", llm_model="test-model"))

    assert client.generate_session_title("ResearchFlow Agent 是如何检索和排序的？") == "ResearchFlow 检索排序机制"
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["payload"]["max_tokens"] == 48
