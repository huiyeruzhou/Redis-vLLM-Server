from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar, Iterable, Optional

DESCRIPTOR: _descriptor.FileDescriptor

class VLLMProtocol(_message.Message):
    __slots__ = ["input_tokens", "output_tokens", "texts"]
    INPUT_TOKENS_FIELD_NUMBER: ClassVar[int]
    OUTPUT_TOKENS_FIELD_NUMBER: ClassVar[int]
    TEXTS_FIELD_NUMBER: ClassVar[int]
    input_tokens: int
    output_tokens: int
    texts: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, output_tokens: Optional[int] = ..., input_tokens: Optional[int] = ..., texts: Optional[Iterable[str]] = ...) -> None: ...
