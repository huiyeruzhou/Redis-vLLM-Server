import uuid
import timeit
import json
from google.protobuf.json_format import MessageToJson, Parse
from message_pb2 import ResponseMessage
import random


# 生成测试数据
def generate_data(size, dim, data_type):
    if data_type == 'str':
        string = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()_+{}|:\"<>?[]\\;',./"
        return [random.choice(string) for _ in range(size)] if dim == 0 else [random.choice(string) for _ in range(100)]
    else:
        return [random.randbytes(100) for _ in range(size)] if dim == 0 else [random.randbytes(size) for _ in range(100)]


def test_protobuf_two_fields(data):
    message = ResponseMessage(task_id=str(uuid.uuid4()), result=data)
    # 序列化
    serialized = message.SerializeToString()
    # 反序列化
    deserialized = ResponseMessage()
    deserialized.ParseFromString(serialized)
    return serialized, deserialized


def test_json_two_fields(data):
    message = {"task_id": str(uuid.uuid4()), "result": data}
    # 序列化
    serialized = json.dumps(message)
    # 反序列化
    deserialized = json.loads(serialized)
    return serialized, deserialized


# 性能测试
def run_tests(data_size, repeat=10, dim=0, data_type='bytes'):
    data = generate_data(data_size, dim, data_type)
    byte_data = [item.encode('latin-1') for item in data] if data_type == 'str' else data
    print(f"Testing with data size: {data_size} bytes")

    # Protobuf Three Fields
    # Protobuf Two Fields
    protobuf_two_time = timeit.timeit(lambda: test_protobuf_two_fields(byte_data), number=repeat)
    print(f"Protobuf (Two Fields) - Time: {protobuf_two_time / repeat:.6f} sec per op")

    # JSON Two Fields

    str_data = [item.decode('latin-1') for item in data] if data_type == 'bytes' else data
    json_two_time = timeit.timeit(lambda: test_json_two_fields(str_data), number=repeat)
    print(f"JSON (Two Fields) - Time: {json_two_time / repeat:.6f} sec per op")


# 运行测试
if __name__ == "__main__":
    data_sizes = [100, 1000, 10000]
    for data_type in ['bytes', 'str']:
        for dim in [0, 1]:
            print(f"{dim=} {data_type=}RESULT:")
            print("-" * 50)
            for size in data_sizes:
                run_tests(size, repeat=1000000 // size, dim=dim, data_type=data_type)
                print("-" * 50)
# dim = 0 RESULT:
# --------------------------------------------------
# Testing with data size: 100 bytes
# Protobuf (Two Fields) - Time: 0.000007 sec per op
# JSON (Two Fields) - Time: 0.000099 sec per op
# --------------------------------------------------
# Testing with data size: 1000 bytes
# Protobuf (Two Fields) - Time: 0.000038 sec per op
# JSON (Two Fields) - Time: 0.001461 sec per op
# --------------------------------------------------
# Testing with data size: 10000 bytes
# Protobuf (Two Fields) - Time: 0.000457 sec per op
# JSON (Two Fields) - Time: 0.016506 sec per op
# --------------------------------------------------
# dim = 1 RESULT:
# --------------------------------------------------
# Testing with data size: 100 bytes
# Protobuf (Two Fields) - Time: 0.000007 sec per op
# JSON (Two Fields) - Time: 0.000102 sec per op
# --------------------------------------------------
# Testing with data size: 1000 bytes
# Protobuf (Two Fields) - Time: 0.000026 sec per op
# JSON (Two Fields) - Time: 0.001270 sec per op
# --------------------------------------------------
# Testing with data size: 10000 bytes
# Protobuf (Two Fields) - Time: 0.000219 sec per op
# JSON (Two Fields) - Time: 0.013256 sec per op
# --------------------------------------------------
