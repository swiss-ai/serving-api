from backend.protocols import LLMRequest, LLMCompletionsRequest
import json


def test_protocols():
    print("Testing LLMRequest with multi-modal input...")
    # Test case 1: Text only (legacy)
    req1 = LLMRequest(
        model="gpt-4", messages=[{"role": "user", "content": "Hello world"}]
    )
    payload1 = req1.to_payload()
    print(f"Legacy payload: {payload1}")
    assert payload1["messages"][0]["content"] == "Hello world"

    # Test case 2: Multi-modal (text + image)
    req2 = LLMRequest(
        model="gpt-4-vision-preview",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is in this image?"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/image.png"},
                    },
                ],
            }
        ],
    )
    payload2 = req2.to_payload()
    print(f"Multi-modal payload: {json.dumps(payload2, indent=2)}")
    assert isinstance(payload2["messages"][0]["content"], list)
    assert payload2["messages"][0]["content"][1]["type"] == "image_url"

    print("\nTesting LLMCompletionsRequest with varied prompts...")
    # Test case 3: String prompt
    req3 = LLMCompletionsRequest(
        model="gpt-3.5-turbo-instruct", prompt="Once upon a time"
    )
    print(f"String prompt payload: {req3.to_payload()}")
    assert req3.prompt == "Once upon a time"

    # Test case 4: List of strings prompt
    req4 = LLMCompletionsRequest(
        model="gpt-3.5-turbo-instruct", prompt=["Story 1", "Story 2"]
    )
    print(f"List prompt payload: {req4.to_payload()}")
    assert isinstance(req4.prompt, list)

    # Test case 5: Token IDs (List of integers) - simulated as Any
    req5 = LLMCompletionsRequest(model="gpt-3.5-turbo-instruct", prompt=[101, 102, 103])
    print(f"Token ID prompt payload: {req5.to_payload()}")
    assert isinstance(req5.prompt, list)
    assert req5.prompt[0] == 101

    print("\nAll tests passed successfully!")


if __name__ == "__main__":
    test_protocols()
