from message_pb2 import ResponseMessage as ResponseMessage
from dataclasses import dataclass
import pickle


@dataclass
class ResponseMessageData:
    task_id: str
    result: list[bytes]


def response_from_byte(byte: bytes) -> ResponseMessage:
    msg = ResponseMessage()
    msg.ParseFromString(byte)
    return msg


# def response_from_byte(byte: bytes) -> ResponseMessageData:
#     return pickle.loads(byte)


def response_to_byte(task_id: str, result: list[bytes]) -> bytes:
    return ResponseMessage(task_id=task_id, result=result).SerializeToString()

# def response_to_byte(task_id: str, result: list[bytes]) -> bytes:
#     return pickle.dumps(ResponseMessageData(task_id=task_id, result=result))
