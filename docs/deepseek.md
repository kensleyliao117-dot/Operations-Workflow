# DeepSeek 文本调用

`integrations/llm/deepseek.py` 使用同步 `httpx` 调用 Chat Completions API，
发送一条 system 消息和一条 user 消息，返回生成正文字符串，不读写数据库。
请求格式参考 [DeepSeek 官方接口文档](https://api-docs.deepseek.com/api/create-chat-completion/)。

## 配置

配置由 `config.py` 从项目根目录的 `.env` 加载，已有环境变量优先：

```dotenv
DEEPSEEK_API_KEY=your-key
DEEPSEEK_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
```

`DEEPSEEK_URL` 为 HTTPS 根地址，不包含 `/chat/completions`；
省略地址和模型时分别采用上述默认值，API Key 在创建客户端时检查。
DeepSeek 配置与 RAG 的 `EMBEDDING_*` 配置独立。

默认使用 `deepseek-flash`，与 `.env.example` 保持一致；客户端统一读取
`config.MODEL_DEEPSEEK`，不会在收到错误后自动切换模型。
模型名称参考 [DeepSeek 官方调用示例](https://api-docs.deepseek.com/)。

## 调用

```python
from integrations.llm.deepseek import generate_text

text = generate_text(
    system_prompt="你是小红书营销文案助手，依据提供的产品事实写作。",
    user_prompt="请根据以下资料写一个自然的标题：……",
)
```

连续调用时复用客户端，并在结束时关闭：

```python
from integrations.llm.deepseek import DeepSeekClient

with DeepSeekClient(timeout=60.0) as client:
    text = client.generate_text(system_prompt="你是文案助手。", user_prompt="请写一个标题。")
```

返回 `choices[0].message.content` 的正文，去除首尾空白，保留段内换行，
不返回 `reasoning_content`。请求使用 `stream=false`。
当前接口适合同步 Service；异步节点需要使用线程池或另行适配异步客户端。

## 错误处理

| 情况 | 异常 |
| --- | --- |
| Key、模型、根地址或超时配置无效 | `config.ConfigurationError` |
| system/user Prompt 为空或不是字符串 | `InvalidInputError` |
| 连接、读取、写入或连接池等待超时 | `DeepSeekTimeoutError` |
| 网络错误、非 2xx 状态、空响应、JSON 或消息格式错误 | `DeepSeekError` |
| 生成被截断、过滤或中断 | `DeepSeekError`，不将部分内容当成完整结果返回 |

`DeepSeekTimeoutError` 继承 `DeepSeekError`，后者继承 `ServiceUnavailableError`，
可由项目已有业务异常处理器转换为 503。上游 401 不表示当前用户的 JWT 失效。
错误消息不回显 Key、Prompt 或上游响应正文。

默认 HTTP I/O 超时为 60 秒，连接超时最多 10 秒；这是各阶段超时，不是整个生成的总时限。
请求不自动重试、不跟随重定向。客户端只关闭自己创建的 HTTP 连接，
测试或调用方注入的 `httpx.Client` 由调用方管理。

## 验证

```powershell
python -m unittest discover -s tests -p test_deepseek.py -v
```

测试使用 `httpx.MockTransport`，不会发送真实 Prompt、消耗 API 额度或访问数据库。
模拟测试不能替代真实账号和模型服务的连通性验证。

结合产品权限、RAG 和人工 Prompt 的业务调用见[内容生成服务](content_generation.md)。
