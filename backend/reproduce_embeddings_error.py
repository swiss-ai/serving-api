import asyncio
import json
from unittest.mock import MagicMock, AsyncMock, patch
from backend.llm_proxy import llm_proxy_embeddings

# Mock response data for embeddings
EMBEDDINGS_RESPONSE = {
    "object": "list",
    "data": [
        {
            "object": "embedding",
            "index": 0,
            "embedding": [0.0023064255, -0.009327292, -0.0028842222],
        }
    ],
    "model": "text-embedding-ada-002",
    "usage": {"prompt_tokens": 8, "total_tokens": 8},
}


async def reproduce():
    print("Starting reproduction...")
    # Mock aiohttp session
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json.return_value = EMBEDDINGS_RESPONSE
    mock_response.headers = {"Content-Type": "application/json"}

    mock_session = AsyncMock()
    mock_session.post.return_value.__aenter__.return_value = mock_response

    with patch("aiohttp.ClientSession", return_value=mock_session):
        try:
            # We also need to mock langfuse to avoid actual network calls or errors
            with patch("proxy.llm_proxy.langfuse") as mock_langfuse:
                mock_span = MagicMock()
                mock_generation = MagicMock()
                mock_langfuse.start_observation.return_value = mock_span
                mock_span.start_observation.return_value = mock_generation

                # Call the function
                print("Calling llm_proxy_embeddings...")
                result = await llm_proxy_embeddings(
                    endpoint="http://mock-endpoint",
                    api_key="mock-key",
                    model="text-embedding-ada-002",
                    input="Hello world",
                )
                print("Call successful.")
                print(f"Result type: {type(result)}")
                print(f"Result content: {result.json()}")

                # Check if 'data' is present in the result
                result_json = json.loads(result.json())
                if "data" not in result_json:
                    print("ISSUE FOUND: 'data' field is missing in ModelResponse!")
                else:
                    print("succcess: 'data' field is present.")

        except Exception as e:
            print(f"CAUGHT EXCEPTION: {e}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(reproduce())
