import timeit

# 生成一个长度为 10,000 的字符串
test_string = "这是一个测试文本。" * 1250  # 1250 * 8 = 10,000


# 测试 UTF-8 编码速度
def test_utf8_encode():
    encoded = test_string.encode("utf-8")


# 测试 UTF-8 解码速度
def test_utf8_decode():
    encoded = test_string.encode("utf-8")
    decoded = encoded.decode("utf-8")


# 运行测试
TIMES = 100000
encode_time = timeit.timeit(test_utf8_encode, number=TIMES)
decode_time = timeit.timeit(test_utf8_decode, number=TIMES)

# 打印结果
print(f"UTF-8 编码时间（TIMES 次，字符串长度 10,000）: {encode_time:.6f} 秒")
print(f"UTF-8 解码时间（TIMES 次，字符串长度 10,000）: {decode_time:.6f} 秒")
print(f"平均每次编码时间: {encode_time / (TIMES/1e6):.6f} 微秒")
print(f"平均每次解码时间: {decode_time / (TIMES/1e6):.6f} 微秒")
