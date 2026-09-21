# jev-mcp

[English](README.md) | 中文

把 Jev（TypeSafe System One）封装成 MCP 工具，作为 Agent 的语义决策控制层：
用 Jev 做有限类型判断（分类/打分/是否），让代码掌握最终执行权，让 LLM 专注于生成与规划。
适用于 Claude Code、Codex、Cursor 及自研 Harness。

## 核心思想

- 能用规则算的 → 给代码
- 能枚举判断的 → 给 Jev
- 需要开放思考的 → 才给 LLM

Jev 不生成文本，只返回结构化概率结果；LLM 不直接做高风险决策，只负责生成内容。

## 架构

```
用户请求
   │
   ▼
┌─────────────────┐
│ ① jev_route_task │ 任务路由/难度/紧急度
└────────┬────────┘
         ▼
    LLM 生成计划/动作
         │
         ▼
┌─────────────────┐
│ ② jev_tool_gate  │ 工具执行前风险门控
└────────┬────────┘
         ▼
    执行工具（沙箱）
         │
         ▼
┌─────────────────┐
│ ③ jev_verify_done│ 执行后语义验证
└────────┬────────┘
         ▼
     通过则结束，否则循环/升级人工
```

## MCP 工具

| 工具                | 用途            | 判断字段                                                                              |
| ----------------- | ------------- | --------------------------------------------------------------------------------- |
| `jev_route_task`  | 路由选择          | task\_type(Choice), difficulty(Score), is\_urgent(Noul)                           |
| `jev_tool_gate`   | 风险门控          | destructive(Noul), exfiltration(Noul), scope\_breach(Noul), reversibility(Score)  |
| `jev_verify_done` | 完成验证          | covers\_user\_request(Noul), has\_unverified\_claim(Noul), repeated\_action(Noul) |
| `jev_ask`         | 通用 Jev 调用（高级） | 自定义 questions，只返回原始概率，不套用策略                                                       |

每个工具的返回结构：

```json
{
  "tool": "jev_route_task",
  "decision": {"action": "auto_execute", "confidence": 0.93, "reasons": ["..."]},
  "fields": {"task_type": {"type": "choice", "value": "direct_query", "p": 0.93}},
  "degraded": false,
  "warnings": []
}
```

action 词汇约定：

- route: `auto_execute` / `human_review`
- gate: `auto_execute` / `human_review` / `block`
- verify: `pass` / `continue` / `human_review`

## 一键部署

### 方式一：本地脚本（推荐，零手工步骤）

```text
Windows       双击 start.bat（或终端运行 start.bat）
Linux/macOS   bash start.sh
```

脚本自动完成：检查 Python → 创建虚拟环境 → 安装依赖 → 以 Streamable HTTP 启动服务。

### 方式二：Docker（云服务器推荐）

```bash
docker compose up -d --build
```

密钥二选一：

- 在项目根目录创建 `.env` 文件（compose 自动读取）：

  ```bash
  TYPESAFE_API_KEY=xxx
  JEV_BASE_URL=https://your-jev-endpoint
  ```

- 或直接写进 [config.yaml](config.yaml) 的 `env` 段（真实环境变量优先）。

### 方式三：手动部署

```bash
pip install -r requirements.txt
python server.py
```

### 配置（必做，YAML 形式）

编辑 [config.yaml](config.yaml) 的 `env` 段即可，无需手动 export：

```yaml
env:
  TYPESAFE_API_KEY: ""      # Jev 云端 API Key（必填）
  JEV_BASE_URL: "https://api.typesafe.ai/v1/systemone"          # Jev 云端端点（必填）
```

优先级：**真实环境变量 > config.yaml 的 env 段**（同名键已存在于进程环境时，YAML 值不生效）。
生产部署建议：API Key 走环境变量/密钥管理下发，config.yaml 不要提交真实 Key。

### 启动后验证

```bash
curl http://127.0.0.1:8800/health
# {"status":"ok","service":"jev_mcp","version":"0.1.0"}
```

- `http://<host>:8800/mcp` —— MCP Streamable HTTP 入口
- `http://<host>:8800/health` —— 探活

### 客户端接入（URL 方式）

**Trae**：MCP 面板 → 添加 MCP Server → 粘贴 JSON：

```json
{
  "mcpServers": {
    "jev_mcp": {
      "url": "http://127.0.0.1:8800/mcp"
    }
  }
}
```

**Claude Code / Cursor** —— `.mcp.json`：

```json
{
  "mcpServers": {
    "jev_mcp": {
      "url": "http://localhost:8800/mcp",
      "headers": {
        "Authorization": "Bearer $TYPESAFE_API_KEY"
      }
    }
  }
}
```

**Codex**：在 `mcp.json` 中同样以 `url` 方式注册即可；ccwitch 可将该配置统一下发到各客户端。

> 常见坑：本地/裸端口部署必须用 `http://`；写成 `https://` 会因 TLS 握手失败表现为连接超时。公网部署请在前面挂 Nginx/Caddy 做 TLS 终结，客户端再用 `https://`。

## Jev 云端接口契约（client.py 的单点适配处）

默认实现走 HTTP，若官方 `typesafe-sdk` 的 API 不同，只需替换 [client.py](client.py) 中的 `_post_ask()`：

```
POST {JEV_BASE_URL}/v1/ask
Authorization: Bearer $TYPESAFE_API_KEY

请求体：
{
  "context": "待判断的上下文文本",
  "questions": [
    {"name": "task_type", "type": "choice", "prompt": "...", "options": ["a", "b"]},
    {"name": "difficulty", "type": "score", "prompt": "..."},
    {"name": "is_urgent", "type": "noul", "prompt": "..."}
  ]
}

响应体（裸 {...} 亦可）：
{
  "fields": {
    "task_type": {"type": "choice", "value": "a", "p": 0.93},
    "difficulty": {"type": "score", "p": 0.35},
    "is_urgent": {"type": "noul", "p": 0.08}
  }
}
```

## 配置与阈值

以 [config.yaml](config.yaml) 方式管理，不写死在代码中：

```yaml
route:
  auto_execute: 0.90      # task_type 置信度 >= 此值且不紧急 → 自动执行
  review: 0.60            # 置信度 < 此值 → 人工复核

tool_gate:
  strict_destructive_threshold: 0.2
  exfiltration_threshold: 0.1
  auto_execute_destructive_threshold: 0.05

verify:
  covers_min_noul: 0.90
  has_unverified_claim_max: 0.30
  max_loops: 8
```

**阈值必须根据自己业务样本校准，不要直接采用默认值。**

## 控制点伪代码

```python
def agent_loop(request):
    route = jev_route_task(request)
    if route.decision.action != "auto_execute":
        return human_review(request, route)

    for step in range(MAX_STEPS):
        proposal = llm.plan_next(request, state)
        risk = jev_tool_gate(proposal)
        if risk.decision.action == "block":
            return reject(proposal, risk)
        if risk.decision.action != "auto_execute":
            return human_review(proposal, risk)

        result = run_in_sandbox(proposal)

        done = jev_verify_done(request, summarize(result), loop_count=step)
        if done.decision.action == "pass":
            return final_answer(state)
        if done.decision.action == "human_review":
            return escalate(done)
    return escalate()
```

## 安全与降级

- Jev 超时/不可用 → 默认保守（`decision.degraded=true`，action 走 `safety.on_jev_unavailable`，默认 `human_review`），绝不自动放行。
- 外部文本进入 state 前先脱敏/隔离（控制字符过滤、长度截断、疑似 prompt injection 告警），防止干扰判断。
- 高风险工具（删库、发外邮、生产发布）必须在 Jev 之外叠加硬规则和人工审批。

## 目录结构

```
.
├── start.bat            # Windows 一键启动（自动建 venv + 装依赖）
├── start.sh             # Linux/macOS 一键启动
├── Dockerfile           # 容器镜像
├── docker-compose.yml   # Docker 一键编排（含健康检查）
├── requirements.txt
├── .dockerignore
├── LICENSE              # MIT
├── server.py            # MCP 入口（Streamable HTTP 云端部署）
├── templates.py         # 领域 questions 模板 + 文本脱敏
├── policies.py          # 阈值/决策逻辑（含 env 段注入）
├── client.py            # Jev 云端客户端（typesafe-sdk 单点适配处）
├── config.yaml          # 环境变量 + 阈值配置
├── README.md            # English docs
└── README_CN.md         # 中文文档
```

## License

MIT
