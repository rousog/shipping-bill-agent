# Role

上一次抽取结果未通过校验。请根据错误信息修复 JSON，不要新增文档中不存在的数据。

# 原始文档

```text
{text}
```

# 上一次输出

```json
{previous_output}
```

# 校验错误

```json
{errors}
```

# 修复要求

1. 只修改与错误相关的字段；
2. 无法修复的字段输出 `null`；
3. 不要删除或合并原始费用行；
4. Credit Note 金额保持负数；
5. 返回与 `ExtractionResult` 完全一致的 JSON。
