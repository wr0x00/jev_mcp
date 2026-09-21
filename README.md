# jev-mcp

English | [中文](README_CN.md)

Wraps Jev (TypeSafe System One) into MCP tools that serve as a semantic decision-control layer for Agents:
use Jev for finite typed judgments (classification / scoring / yes-no), let code keep final execution authority, and let the LLM focus on generation and planning.
Works with Claude Code, Codex, Cursor, and custom Harnesses.

## Core Idea

- What can be computed by rules → give it to code
- What can be judged over a finite set → give it to Jev
- Only what needs open-ended thinking → give it to the LLM

Jev never generates text; it only returns structured probability results. The LLM never makes high-risk decisions directly; it only generates content.

## Architecture

```
User request
   │
   ▼
┌─────────────────┐
│ ① jev_route_task │ Task routing / difficulty / urgency
└────────┬────────┘
         ▼
    LLM generates plan / action
         │
         ▼
┌─────────────────┐
│ ② jev_tool_gate  │ Pre-execution risk gate
└────────┬────────┘
         ▼
    Execute tool (sandbox)
         │
         ▼
┌─────────────────┐
│ ③ jev_verify_done│ Post-execution semantic verification
└────────┬────────┘
         ▼
     Pass → done; otherwise loop / escalate to human
```

## MCP Tools

| Tool              | Purpose                        | Judgment fields                                                                  |
| ----------------- | ------------------------------ | -------------------------------------------------------------------------------- |
| `jev_route_task`  | Route selection                | task\_type(Choice), difficulty(Score), is\_urgent(Noul)                          |
| `jev_tool_gate`   | Pre-execution risk gate        | destructive(Noul), exfiltration(Noul), scope\_breach(Noul), reversibility(Score) |
| `jev_verify_done` | Completion verification        | covers\_user\_request(Noul), has\_unverified\_claim(Noul), repeated\_action(Noul) |
| `jev_ask`         | Generic Jev call (advanced)    | Custom questions; returns raw probabilities only, no policy applied              |

Response structure of every tool:

```json
{
  "tool": "jev_route_task",
  "decision": {"action": "auto_execute", "confidence": 0.93, "reasons": ["..."]},
  "fields": {"task_type": {"type": "choice", "value": "direct_query", "p": 0.93}},
  "degraded": false,
  "warnings": []
}
```

Action vocabulary:

- route: `auto_execute` / `human_review`
- gate: `auto_execute` / `human_review` / `block`
- verify: `pass` / `continue` / `human_review`

## One-Click Deployment

### Option 1: Local script (recommended, zero manual steps)

```text
Windows       double-click start.bat (or run start.bat in a terminal)
Linux/macOS   bash start.sh
```

The script automatically: checks Python → creates a virtualenv → installs dependencies → starts the service over Streamable HTTP.

### Option 2: Docker (recommended for cloud servers)

```bash
docker compose up -d --build
```

Provide credentials in either of two ways:

- Create a `.env` file in the project root (compose reads it automatically):

  ```bash
  TYPESAFE_API_KEY=xxx
  JEV_BASE_URL=https://your-jev-endpoint
  ```

- Or write them into the `env` section of [config.yaml](config.yaml) (real environment variables take precedence).

### Option 3: Manual deployment

```bash
pip install -r requirements.txt
python server.py
```

### Configuration (required, YAML form)

Edit the `env` section of [config.yaml](config.yaml) — no need to export anything:

```yaml
env:
  TYPESAFE_API_KEY: ""      # Jev cloud API Key (required)
  JEV_BASE_URL: "https://api.typesafe.ai/v1/systemone"          # Jev cloud endpoint (required)
```

Precedence: **real environment variables > the `env` section of config.yaml** (if a key already exists in the process environment, the YAML value is ignored).
For production: deliver the API Key via environment variables / a secrets manager; do not commit real keys to config.yaml.

### Post-start verification

```bash
curl http://127.0.0.1:8800/health
# {"status":"ok","service":"jev_mcp","version":"0.1.0"}
```

- `http://<host>:8800/mcp` —— MCP Streamable HTTP endpoint
- `http://<host>:8800/health` —— health probe

### Client integration (URL mode)

**Trae**: MCP panel → Add MCP Server → paste the JSON:

```json
{
  "mcpServers": {
    "jev_mcp": {
      "url": "http://127.0.0.1:8800/mcp"
    }
  }
}
```

**Claude Code / Cursor** —— `.mcp.json`:

```json
{
  "mcpServers": {
    "jev_mcp": {
      "url": "http://your-jev-mcp-host:8800/mcp",
      "headers": {
        "Authorization": "Bearer <your-mcp-token>"
      }
    }
  }
}
```

**Codex**: register with a `url` entry in `mcp.json` the same way; ccwitch can roll this config out to all clients.

> Common pitfall: for local / bare-port deployments you must use `http://`. Using `https://` fails the TLS handshake and shows up as a connection timeout. For public deployment, terminate TLS with Nginx/Caddy in front, then clients can use `https://`.

## Jev Cloud API Contract (the single adapter point in client.py)

The default implementation uses plain HTTP. If the official `typesafe-sdk` has a different API, you only need to replace `_post_ask()` in [client.py](client.py):

```
POST {JEV_BASE_URL}/v1/ask
Authorization: Bearer $TYPESAFE_API_KEY

Request body:
{
  "context": "context text to be judged",
  "questions": [
    {"name": "task_type", "type": "choice", "prompt": "...", "options": ["a", "b"]},
    {"name": "difficulty", "type": "score", "prompt": "..."},
    {"name": "is_urgent", "type": "noul", "prompt": "..."}
  ]
}

Response body (a bare {...} also works):
{
  "fields": {
    "task_type": {"type": "choice", "value": "a", "p": 0.93},
    "difficulty": {"type": "score", "p": 0.35},
    "is_urgent": {"type": "noul", "p": 0.08}
  }
}
```

## Configuration & Thresholds

Managed in [config.yaml](config.yaml), never hard-coded:

```yaml
route:
  auto_execute: 0.90      # task_type confidence >= this and not urgent → auto execute
  review: 0.60            # confidence < this → human review

tool_gate:
  strict_destructive_threshold: 0.2
  exfiltration_threshold: 0.1
  auto_execute_destructive_threshold: 0.05

verify:
  covers_min_noul: 0.90
  has_unverified_claim_max: 0.30
  max_loops: 8
```

**Thresholds must be calibrated against your own business samples — do not ship the defaults.**

## Control-Loop Pseudocode

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

## Safety & Degradation

- Jev timeout / unavailable → stay conservative by default (`decision.degraded=true`, action follows `safety.on_jev_unavailable`, default `human_review`); never auto-approve.
- External text is sanitized/quarantined before entering state (control-character filtering, length truncation, suspected prompt-injection warnings).
- High-risk tools (dropping databases, sending external email, production releases) must additionally be guarded by hard rules and human approval outside Jev.

## Directory Structure

```
.
├── start.bat            # Windows one-click start (auto venv + deps)
├── start.sh             # Linux/macOS one-click start
├── Dockerfile           # Container image
├── docker-compose.yml   # Docker one-click orchestration (with healthcheck)
├── requirements.txt
├── .dockerignore
├── LICENSE              # MIT
├── server.py            # MCP entry (Streamable HTTP cloud deployment)
├── templates.py         # Domain question templates + text sanitization
├── policies.py          # Thresholds / decision logic (incl. env injection)
├── client.py            # Jev cloud client (single adapter point for typesafe-sdk)
├── config.yaml          # Environment variables + threshold config
├── README.md            # English docs
└── README_CN.md         # 中文文档
```

## License

MIT
