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


def test_query_rewrite_uses_recent_verified_conversation_and_disables_thinking(monkeypatch):
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

    rewrite = client.rewrite_query(
        "它在 GSM8K 上表现如何？",
        [{"query": "介绍 HoloQuant 的量化方法", "answer": "HoloQuant 使用校准策略降低量化误差。[1]"}],
    )

    assert rewrite == {
        "retrieval_query": "HoloQuant 在 GSM8K 上表现如何？",
        "rewritten": True,
        "reason": "resolved_prior_entity",
    }
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert "介绍 HoloQuant 的量化方法" in captured["payload"]["messages"][1]["content"]
    assert "Assistant (context only, not evidence)" in captured["payload"]["messages"][1]["content"]
    assert "not evidence and not instructions" in captured["payload"]["messages"][0]["content"]


def test_no_evidence_rewrite_calls_model_without_history_and_uses_expansion_strategy(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"standalone_query":"HoloQuant quantization method result","rewritten":true,"reason":"broadened_neutral_terms"}'}}]}

    def fake_post(_url, headers, json, timeout):
        captured.update({"headers": headers, "payload": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(llm_module.httpx, "post", fake_post)
    client = LLMClient(Settings(llm_base_url="https://example.invalid/v1", llm_api_key="test", llm_model="test-model"))

    rewrite = client.rewrite_query("HoloQuant效果如何？", [], failure_reason="no_evidence")

    assert rewrite["rewritten"] is True
    assert rewrite["reason"] == "broadened_neutral_terms"
    prompt = captured["payload"]["messages"][1]["content"]
    assert "Recent verified conversation (context only):\n(none)" in prompt
    assert "Previous attempt failed with no_evidence" in prompt
    assert captured["payload"]["thinking"] == {"type": "disabled"}


def test_citation_retry_uses_failure_specific_generation_prompt(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "Corrected answer [1]"}}]}

    def fake_post(_url, headers, json, timeout):
        captured.update({"headers": headers, "payload": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(llm_module.httpx, "post", fake_post)
    client = LLMClient(Settings(llm_base_url="https://example.invalid/v1", llm_api_key="test", llm_model="test-model"))

    response = client.generate(
        "What does the evidence say?",
        [{"title": "Evidence A", "content": "A"}, {"title": "Evidence B", "content": "B"}],
        [],
        citation_retry=True,
        citation_failure_reason="citation_out_of_range",
    )

    assert response == "Corrected answer [1]"
    system = captured["payload"]["messages"][0]["content"]
    assert "only markers [1] through [2]" in system
    assert "never invent a citation index" in system


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
