import json
import uuid
import time
from typing import Dict, List, Literal, Optional, Union, Any
import base64
import struct
from pydantic import BaseModel, Field, field_validator


def _generate_id():  # private helper function
    return "chatcmpl-" + str(uuid.uuid4())


class LLMRequest(BaseModel):
    model: str
    messages: List[Dict[str, Any]] = []
    stream: bool = False
    stream_options: Optional[Dict] = None
    logprobs: bool = False
    top_logprobs: Optional[int] = None
    max_tokens: Optional[int] = None
    temperature: float = 0.7
    top_p: float = 1.0
    seed: int = 42
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0
    extra_body: Dict = {}

    # Tracking fields
    user_id: str = ""
    opt_out: bool = False
    app_title: str = ""
    tags: List[str] = []

    def to_payload(self) -> Dict:
        """Convert to payload for the LLM API"""
        data = self.model_dump(
            exclude={"user_id", "opt_out", "app_title", "tags", "extra_body"}
        )
        data.update(self.extra_body)
        return data


class LLMCompletionsRequest(BaseModel):
    model: str
    prompt: Union[str, List[Any]] = ""
    stream: bool = False
    stream_options: Optional[Dict] = None
    max_tokens: Optional[int] = None
    temperature: float = 0.7
    top_p: float = 1.0
    seed: int = 42
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0
    extra_body: Dict = {}

    # Tracking fields
    user_id: str = ""
    opt_out: bool = False
    app_title: str = ""
    tags: List[str] = []

    def to_payload(self) -> Dict:
        """Convert to payload for the LLM API"""
        data = self.model_dump(
            exclude={"user_id", "opt_out", "app_title", "tags", "extra_body"}
        )
        data.update(self.extra_body)
        return data


class Function(BaseModel):
    arguments: str
    name: Optional[str] = None

    def __init__(
        self,
        arguments: Optional[Union[Dict, str]] = None,
        name: Optional[str] = None,
        **params,
    ):
        if arguments is None:
            arguments = ""
        elif isinstance(arguments, Dict):
            arguments = json.dumps(arguments)

        super().__init__(arguments=arguments, name=name, **params)

    def __contains__(self, key):
        return hasattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        setattr(self, key, value)


class FunctionCall(BaseModel):
    arguments: str
    name: Optional[str] = None


class ChatCompletionMessageToolCall(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    function: Function
    type: str = "function"

    def __contains__(self, key):
        return hasattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        setattr(self, key, value)


class ChatCompletionDeltaToolCall(BaseModel):
    id: Optional[str] = None
    function: Function
    type: Optional[str] = None
    index: int


class Message(BaseModel):
    content: Optional[str] = None
    role: Literal["assistant"] = "assistant"
    tool_calls: Optional[List[ChatCompletionMessageToolCall]] = None
    function_call: Optional[FunctionCall] = None
    raw_prompt: Optional[str] = None
    raw_output: Optional[str] = None

    def get(self, key, default=None):
        return getattr(self, key, default)

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def json(self, **kwargs):
        return self.model_dump_json()


class TopLogprob(BaseModel):
    token: str
    bytes: Optional[List[int]] = None
    logprob: float


class ChatCompletionTokenLogprob(BaseModel):
    token: str
    bytes: Optional[List[int]] = None
    logprob: float
    top_logprobs: List[TopLogprob]


class ChoiceLogprobs(BaseModel):
    content: Optional[List[ChatCompletionTokenLogprob]] = None


class Delta(BaseModel):
    content: Optional[str] = None
    role: Optional[str] = None
    function_call: Optional[FunctionCall] = None
    tool_calls: Optional[List[ChatCompletionDeltaToolCall]] = None

    def __contains__(self, key):
        return hasattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        setattr(self, key, value)


class Choices(BaseModel):
    finish_reason: Optional[str] = "stop"
    index: int = 0
    message: Message = Field(default_factory=Message)
    logprobs: Optional[ChoiceLogprobs] = None
    enhancements: Optional[Any] = None

    def __contains__(self, key):
        return hasattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        setattr(self, key, value)


class Usage(BaseModel):
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None

    def __contains__(self, key):
        return hasattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        setattr(self, key, value)


class StreamingChoices(BaseModel):
    finish_reason: Optional[str] = None
    index: int = 0
    delta: Delta = Field(default_factory=Delta)
    logprobs: Optional[ChoiceLogprobs] = None
    enhancements: Optional[Any] = None

    def __contains__(self, key):
        return hasattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        setattr(self, key, value)


class EmbeddingObject(BaseModel):
    object: Literal["embedding"] = "embedding"
    embedding: List[float]
    index: int = 0

    @field_validator("embedding", mode="before")
    @classmethod
    def decode_base64_embedding(cls, v):
        if isinstance(v, str):
            # Base64 string to list of floats
            # Assuming little-endian float32 as is common with OpenAI compatible APIs
            byte_data = base64.b64decode(v)
            count = len(byte_data) // 4
            return list(struct.unpack(f"<{count}f", byte_data))
        return v


class ModelResponse(BaseModel):
    id: str = Field(default_factory=_generate_id)
    choices: List[Union[Choices, StreamingChoices]] = Field(
        default_factory=lambda: [Choices()]
    )
    created: int = Field(default_factory=lambda: int(time.time()))
    model: Optional[str] = None
    object: str = "chat.completion"
    system_fingerprint: Optional[str] = None
    raw_prompt: Optional[str] = None
    raw_output: Optional[str] = None
    usage: Optional[Usage] = None
    data: Optional[List[EmbeddingObject]] = None

    _hidden_params: dict = {}

    # Validation headers from upstream
    headers: Optional[Dict[str, str]] = None

    def __init__(self, **data):
        super().__init__(**data)

        # Determine object type helper
        if not self.object:
            # Basic heuristic if not provided
            self.object = "chat.completion"

    def __contains__(self, key):
        return hasattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def __getitem__(self, key):
        return getattr(self, key)

    def json(self, **kwargs):
        return self.model_dump_json()


class RetryConstantError(Exception):
    pass


class RetryExpoError(Exception):
    pass


class UnknownLLMError(Exception):
    pass


class ProviderKeySubmission(BaseModel):
    provider: str
    api_key: str
