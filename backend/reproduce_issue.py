from backend.protocols import EmbeddingObject
from pydantic import ValidationError


def test_base64_embedding_handling():
    # Create a dummy embedding
    # Pack as floatLE (standard for some) or just assume the string from error is base64
    # The error showed a string starting with +stgPD, which is valid base64 chars.

    # Let's try to pass a string to the current model and expect failure
    try:
        EmbeddingObject(embedding="somebase64string", index=0)
    except ValidationError as e:
        print("Caught expected ValidationError:", e)
    else:
        print("Did not catch ValidationError, which is unexpected for current code.")


if __name__ == "__main__":
    test_base64_embedding_handling()
