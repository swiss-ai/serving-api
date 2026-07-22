from backend.models.protocols import LLMRequest, LLMCompletionsRequest, ModelResponse


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
