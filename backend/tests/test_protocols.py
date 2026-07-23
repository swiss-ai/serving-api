from backend.models.protocols import (
    LLMCompletionsRequest,
    LLMRequest,
    ModelResponse,
    Usage,
)


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


def test_completions_logprobs_pass_through():
    # /v1/completions with echo+logprobs (vLLM upstream): the classic OpenAI
    # logprobs fields must survive the round-trip — lm-eval-harness reads
    # choices[0].logprobs.token_logprobs for loglikelihood scoring.
    data = {
        "id": "cmpl-1",
        "created": 0,
        "object": "text_completion",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "length",
                "text": "The capital of France is Paris",
                "logprobs": {
                    "text_offset": [0, 3, 11],
                    "token_logprobs": [None, -1.94, -0.88],
                    "tokens": ["The", " capital", " of"],
                    "top_logprobs": [None, {" following": -1.94}, {" of": -0.88}],
                },
            }
        ],
    }
    resp = ModelResponse(**data)
    dumped = resp.model_dump()["choices"][0]["logprobs"]
    assert dumped["token_logprobs"] == [None, -1.94, -0.88]
    assert dumped["tokens"] == ["The", " capital", " of"]
    assert dumped["top_logprobs"] == [None, {" following": -1.94}, {" of": -0.88}]
    assert dumped["text_offset"] == [0, 3, 11]
    # and the serialized JSON body keeps them too
    assert '"token_logprobs"' in resp.model_dump_json()


def test_chat_logprobs_still_parse():
    # Chat-style logprobs (content list) keep validating as before.
    data = {
        "id": "chatcmpl-1",
        "created": 0,
        "object": "chat.completion",
        "model": "test-model",
        "choices": [
            {
                "message": {"role": "assistant", "content": "hi"},
                "logprobs": {
                    "content": [
                        {
                            "token": "hi",
                            "logprob": -0.1,
                            "top_logprobs": [{"token": "hi", "logprob": -0.1}],
                        }
                    ]
                },
            }
        ],
    }
    resp = ModelResponse(**data)
    assert resp.choices[0].logprobs.content[0].token == "hi"
    assert resp.choices[0].logprobs.content[0].logprob == -0.1


def test_usage_preserves_token_details():
    # Newer engines return *_tokens_details (cached_tokens, reasoning_tokens);
    # they must survive the round-trip rather than being dropped (#74).
    usage = Usage(
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        prompt_tokens_details={"cached_tokens": 3},
        completion_tokens_details={"reasoning_tokens": 2},
    )
    dumped = usage.model_dump()
    assert dumped["prompt_tokens_details"] == {"cached_tokens": 3}
    assert dumped["completion_tokens_details"] == {"reasoning_tokens": 2}


def test_response_does_not_leak_headers_or_internal_fields():
    # Upstream HTTP headers and internal tracking fields must never appear in
    # the serialized response body (#77).
    data = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "m",
        "choices": [{"message": {"role": "assistant", "content": "hi"}}],
    }
    resp = ModelResponse(**data)
    resp.headers = {"X-Computing-Node": "node-7", "Server": "internal"}
    dumped = resp.model_dump()
    # headers still accessible internally (used for node-id metrics)...
    assert resp.headers["X-Computing-Node"] == "node-7"
    # ...but never serialized.
    assert "headers" not in dumped
    assert "raw_prompt" not in dumped
    assert "raw_output" not in dumped
    assert "data" not in dumped  # null unless embeddings
    assert "enhancements" not in dumped["choices"][0]
    assert "node-7" not in resp.model_dump_json()


def test_completions_choice_has_no_message_stub():
    # /v1/completions choices carry `text`, not a chat-style `message` (#77).
    data = {
        "id": "cmpl-1",
        "object": "text_completion",
        "model": "m",
        "choices": [{"index": 0, "text": "hello", "finish_reason": "stop"}],
    }
    resp = ModelResponse(**data)
    dumped = resp.model_dump()
    assert dumped["choices"][0]["text"] == "hello"
    assert "message" not in dumped["choices"][0]


def test_chat_choice_keeps_message():
    data = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "m",
        "choices": [{"message": {"role": "assistant", "content": "hi"}}],
    }
    resp = ModelResponse(**data)
    dumped = resp.model_dump()
    assert dumped["choices"][0]["message"]["content"] == "hi"


def test_missing_id_uses_spec_prefix_per_endpoint():
    # Absent upstream id → cmpl- for completions, chatcmpl- for chat (#77).
    completion = ModelResponse(object="text_completion", model="m")
    assert completion.id.startswith("cmpl-")
    assert not completion.id.startswith("chatcmpl-")

    chat = ModelResponse(object="chat.completion", model="m")
    assert chat.id.startswith("chatcmpl-")
