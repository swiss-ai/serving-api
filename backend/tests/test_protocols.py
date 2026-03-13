from backend.protocols import LLMRequest, LLMCompletionsRequest, ModelResponse


def test_llm_request_to_payload():
    req = LLMRequest(
        model="test-model",
        messages=[{"role": "user", "content": "hello"}],
        user_id="user1",
    )
    payload = req.to_payload()
    assert payload["model"] == "test-model"
    assert payload["messages"] == [{"role": "user", "content": "hello"}]


def test_llm_completions_request_to_payload():
    req = LLMCompletionsRequest(
        model="test-model",
        prompt="hello",
        user_id="user1",
    )
    payload = req.to_payload()
    assert payload["model"] == "test-model"
    assert payload["prompt"] == "hello"


def test_model_response_parses():
    data = {
        "id": "123",
        "created": 0,
        "object": "chat.completion",
        "model": "test-model",
        "choices": [{"message": {"role": "assistant", "content": "hi"}}],
    }
    resp = ModelResponse(**data)
    assert resp.model == "test-model"
    assert resp.id == "123"
