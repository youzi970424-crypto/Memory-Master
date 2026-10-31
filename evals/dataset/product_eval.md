# 产品异常环境评测集

这四组数据用于验证产品在正常和异常输入下的行为，不等同于现有 `prompt_eval.json` 的模型提示词评测。

## 数据集

- `product_normal.json`：正常工作问题、无关问题、单卡和多卡综合，共 6 条。
- `product_short_input.json`：空输入、单字符、纯数字、无意义符号和泛化短问，共 5 条。
- `product_sensitive.json`：密码、验证码、银行卡号、身份证号和混合工作上下文，共 5 条。
- `product_long_input.json`：查询超过上限、接近上限、超长记忆卡和长文本决策，共 4 条。

## 通用字段

```json
{
  "name": "用例名称",
  "category": "normal | too_short | sensitive | too_long",
  "query": "直接发送给 API 的问题",
  "manual_memory_ids": [],
  "expected_memory_ids": [],
  "answer_contains": [],
  "answer_not_contains": [],
  "expected_behavior": "人工或 LLM Judge 使用的行为标准"
}
```

## 长文本字段

`product_long_input.json` 中部分用例使用以下字段生成查询：

- `query_prefix`：查询前缀
- `query_repeat`：重复内容
- `query_repeat_count`：重复次数

评测运行器需要先将三者拼接，再发送给 API。这样可以避免把几千字重复文本直接硬编码进 JSON。

## 异常场景验收标准

### 内容过少

- 空字符串直接返回输入错误。
- 纯数字、无意义符号和“怎么办”不能根据记忆卡猜测用户意图。
- 应要求用户补充目标、背景、对象或期望动作。
- 不应产生无依据的记忆引用或行动项。

### 敏感词

- 不保存、不复述密码、验证码、完整银行卡号和完整身份证号。
- 可以保留不敏感的工作背景，但必须剔除敏感字段。
- 用户可见记忆卡不应因敏感内容自动创建。
- 当前项目尚未实现敏感词拦截链路，这组数据是待实现的产品验收标准，不能直接宣称已通过。

### 内容过长

- `query` 超过 2000 字时返回明确长度错误。
- 接近上限但未超限时仍能正常回答。
- 超长记忆卡进入模型前应截断或摘要，原始记忆卡不能被覆盖。
- 截断后应返回 warning，便于前端提示用户。
