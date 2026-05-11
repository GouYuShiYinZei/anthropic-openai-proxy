# Anthropic → OpenAI Protocol Proxy

将 Anthropic Messages API 请求翻译为 OpenAI Chat Completions API，让你用任何 OpenAI 兼容的 API 来驱动 Claude Code 等 Anthropic 客户端。

## 为什么需要这个工具

Claude Code 只支持 Anthropic Messages API。如果你有 OpenAI 兼容的 API key（比如第三方中转、自建网关），Claude Code 无法直接使用。这个代理在中间做实时协议翻译，让 Claude Code 可以和任何 OpenAI 兼容端点通信。

## 功能

| 功能 | 状态 |
|------|------|
| 文本对话（流式 SSE + 非流式） | ✅ |
| 工具调用（tools / tool_use / tool_result / tool_choice）| ✅ |
| 图片理解（base64 转发）| ✅ |
| 思维链文本提取（thinking / redacted_thinking）| ✅ |
| 双认证（x-api-key + Authorization: Bearer）| ✅ |
| count_tokens 端点 | ✅ |
| 模型列表 / 模型详情 | ✅ |
| 交互式输入上游地址 | ✅ |
| Prompt caching 透传 | ❌ |

## 依赖

**Python 3.7+**，零额外依赖（仅使用标准库）。无需 pip install。

## 快速开始

### 1. 启动代理

**Windows** — 双击 `start_proxy.bat`，按提示输入上游 API 地址。

**Linux / macOS**：
```bash
chmod +x start_proxy.sh
./start_proxy.sh
```

启动后会提示输入上游 OpenAI API 地址，例如：
```
  Enter the OpenAI-compatible API endpoint you want to proxy to.
  Examples:
    https://api.openai.com/v1/chat/completions
    https://your-custom-api.com/v1/chat/completions

  Upstream URL: https://your-api.com/v1/chat/completions
```

也可以直接通过命令行参数指定：
```bash
python anthropic_openai_proxy.py --upstream https://your-api.com/v1/chat/completions
```

### 2. 配置客户端

在 Claude Code 的 `~/.claude/settings.json` 中设置：

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8899",
    "ANTHROPIC_AUTH_TOKEN": "<your-upstream-api-key>",
    "ANTHROPIC_MODEL": "<model-name>",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "<model-name>",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "<model-name>",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "<model-name>"
  }
}
```

## 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--host` | `127.0.0.1` | 监听地址，外网访问用 `0.0.0.0` |
| `--port` | `8899` | 监听端口 |
| `--upstream` | 交互式输入 | 上游 OpenAI Chat Completions API 地址 |
| `--model` | 无 | 强制覆盖模型中转给上游的模型名 |

也可以使用环境变量：
- `PROXY_HOST` — 监听地址
- `PROXY_PORT` — 监听端口
- `PROXY_UPSTREAM` — 上游 API 地址
- `PROXY_MODEL` — 强制覆盖上游模型名

## 工作原理

```
Claude Code                    Proxy                   OpenAI API
(Anthropic Messages)    (Protocol Translator)    (Chat Completions)

POST /v1/messages ──────> translate ───────────> POST /v1/chat/completions
x-api-key: sk-xxx         Anp → OpenAI           Authorization: Bearer sk-xxx
Anthropic format          JSON                   OpenAI format JSON

                          <────── translate ──── <──── OpenAI response
                          OpenAI → Anp
                          SSE or JSON            SSE or JSON
```

代理在本地 `127.0.0.1:8899` 监听，扮演一个 Anthropic API 的角色。Claude Code 以为在跟 Anthropic 服务器通信，实际上代理把每个请求都翻译成了 OpenAI 格式发给你指定的上游。

## 代理地址

此代理会将来自 Claude Code 或其他 Anthropic 客户端的请求转发到上游 OpenAI API。**你需要自行提供上游地址和 API key。**

## 文件说明

```
├── anthropic_openai_proxy.py   # 核心代理脚本
├── start_proxy.bat              # Windows 启动器（自动检测 Python）
├── start_proxy.sh               # Linux/macOS 启动器
└── README.md                    # 本文件
```

## 许可

MIT License — 可自由使用、修改和分发。
