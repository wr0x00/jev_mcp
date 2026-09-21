"""Jev MCP Server（云端部署）—— Agent 的语义决策控制层。

核心思想：
  能用规则算的     → 给代码
  能枚举判断的     → 给 Jev（Choice/Score/Noul 有限类型判断，云端 API）
  需要开放思考的   → 才给 LLM

Jev 不生成文本，只返回结构化概率结果；LLM 不直接做高风险决策，只负责生成内容。

云端启动（Streamable HTTP，非 stdio）：
  python server.py                  # 在本目录（仓库根）运行
  python -m jev_mcp.server          # 或从上级目录以包方式运行

配置（YAML 形式，见 config.yaml 的 env 段；真实环境变量优先、YAML 兜底）：
  TYPESAFE_API_KEY   Jev 云端 API Key（必填）
  JEV_BASE_URL       Jev 云端端点（必填）
  JEV_MCP_HOST       监听地址，默认 0.0.0.0
  JEV_MCP_PORT       监听端口，默认 8800
  JEV_MCP_PATH       MCP 路径，默认 /mcp
"""
from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from starlette.responses import JSONResponse

try:  # 包方式运行：python -m jev_mcp.server
    from . import __version__
    from .client import JevClient, JevError
    from .policies import (
        Decision,
        degraded_decision,
        evaluate_route,
        evaluate_tool_gate,
        evaluate_verify,
        load_config,
    )
    from .templates import (
        ROUTE_QUESTIONS,
        TOOL_GATE_QUESTIONS,
        VERIFY_QUESTIONS,
        Question,
        sanitize_text,
        validate_questions,
    )
except ImportError:  # 脚本方式运行：python jev_mcp/server.py
    __version__ = "0.1.0"
    from client import JevClient, JevError
    from policies import (
        Decision,
        degraded_decision,
        evaluate_route,
        evaluate_tool_gate,
        evaluate_verify,
        load_config,
    )
    from templates import (
        ROUTE_QUESTIONS,
        TOOL_GATE_QUESTIONS,
        VERIFY_QUESTIONS,
        Question,
        sanitize_text,
        validate_questions,
    )

mcp = FastMCP("jev_mcp")
_cfg = load_config()
_client = JevClient(
    timeout_ms=_cfg.get("client", {}).get("timeout_ms", 4000),
    max_retries=_cfg.get("client", {}).get("max_retries", 1),
)


# ---------- 内部流程（与 MCP 装饰器解耦，便于单测） ----------

def _sanitize(text: str | None) -> tuple[str, list[str]]:
    if _cfg.get("safety", {}).get("sanitize_external_text", True):
        return sanitize_text(text or "", _cfg.get("safety", {}).get("max_context_chars", 8000))
    return text or "", []


def _fields_for(questions: list[Question], fields: dict) -> dict:
    """按模板顺序整理字段输出；缺字段显式标注，避免 Agent 误读。"""
    out = {}
    for q in questions:
        item = fields.get(q.name)
        out[q.name] = item if isinstance(item, dict) else {"type": q.type, "value": None, "p": None, "missing": True}
    return out


def _package(tool: str, decision: Decision, fields: dict, warnings: list[str]) -> dict:
    out: dict[str, Any] = {
        "tool": tool,
        "decision": {
            "action": decision.action,
            "confidence": round(decision.confidence, 4),
            "reasons": decision.reasons,
        },
        "fields": fields,
        "degraded": decision.degraded,
    }
    if warnings:
        out["warnings"] = warnings
    return out


def _run(tool: str, questions: list[Question], raw_context: str, evaluate) -> dict:
    context, warnings = _sanitize(raw_context)
    if not _client.configured:
        d = degraded_decision(_cfg, "not_configured: 缺少 TYPESAFE_API_KEY 或 JEV_BASE_URL")
        return _package(tool, d, _fields_for(questions, {}), warnings)
    try:
        fields = _client.ask([q.to_dict() for q in questions], context)
    except JevError as exc:
        return _package(tool, degraded_decision(_cfg, str(exc)), _fields_for(questions, {}), warnings)
    return _package(tool, evaluate(fields), _fields_for(questions, fields), warnings)


# ========== ① jev_route_task ==========

@mcp.tool()
def jev_route_task(request: str) -> dict:
    """任务路由：判断 task_type(Choice) / difficulty(Score) / is_urgent(Noul)。

    返回 action=auto_execute（可自动执行）或 human_review（转人工复核），
    以及各字段的原始概率，供上层 Agent 循环使用。

    Args:
        request: 用户的原始请求文本
    """
    return _run("jev_route_task", ROUTE_QUESTIONS, request, lambda f: evaluate_route(f, _cfg))


# ========== ② jev_tool_gate ==========

@mcp.tool()
def jev_tool_gate(proposal: str, tool_name: str = "", target: str = "") -> dict:
    """工具执行前风险门控：destructive(Noul) / exfiltration(Noul) / scope_breach(Noul) / reversibility(Score)。

    返回 action=auto_execute / human_review / block。
    LLM 只负责生成 proposal，是否放行由本门控决定；
    高风险工具（删库、发外邮、生产发布）还须在本门控之外叠加硬规则与人工审批。

    Args:
        proposal: LLM 计划执行的动作描述
        tool_name: 将调用的工具名（可选）
        target: 动作作用对象，如路径/URL/表名（可选）
    """
    context = f"计划动作：{proposal}\n工具：{tool_name or '未指定'}\n目标：{target or '未指定'}"
    return _run("jev_tool_gate", TOOL_GATE_QUESTIONS, context, lambda f: evaluate_tool_gate(f, _cfg))


# ========== ③ jev_verify_done ==========

@mcp.tool()
def jev_verify_done(user_request: str, result_summary: str, loop_count: int = 0) -> dict:
    """执行后语义验证：covers_user_request(Noul) / has_unverified_claim(Noul) / repeated_action(Noul)。

    返回 action=pass（完成）/ continue（继续循环）/ human_review（升级人工）。

    Args:
        user_request: 用户的原始请求
        result_summary: 本轮执行结果摘要（外部文本，进入判断前会被脱敏）
        loop_count: 当前已是第几轮循环（从 0 起）
    """
    context = f"用户请求：{user_request}\n执行结果：{result_summary}"
    return _run("jev_verify_done", VERIFY_QUESTIONS, context, lambda f: evaluate_verify(f, loop_count, _cfg))


# ========== ④ jev_ask ==========

@mcp.tool()
def jev_ask(questions: list[dict[str, Any]], context: str = "") -> dict:
    """通用 Jev 调用（高级）：自定义 questions，只返回原始概率字段，不套用策略阈值。

    Args:
        questions: [{"name": "...", "type": "choice|score|noul", "prompt": "...", "options": ["a", "b"]}]
                   choice 类型必须给 options；score/noul 不需要。
        context: 供 Jev 判断的上下文文本（会先脱敏）
    """
    errors = validate_questions(questions)
    if errors:
        return {"tool": "jev_ask", "error": "invalid_questions", "details": errors}

    context, warnings = _sanitize(context)
    result: dict[str, Any] = {"tool": "jev_ask", "degraded": False}
    if warnings:
        result["warnings"] = warnings

    if not _client.configured:
        result["degraded"] = True
        result["decision"] = {
            "action": _cfg.get("safety", {}).get("on_jev_unavailable", "human_review"),
            "reasons": ["jev_unavailable:not_configured: 缺少 TYPESAFE_API_KEY 或 JEV_BASE_URL"],
        }
        return result
    try:
        fields = _client.ask(questions, context)
    except JevError as exc:
        result["degraded"] = True
        result["decision"] = {
            "action": _cfg.get("safety", {}).get("on_jev_unavailable", "human_review"),
            "reasons": [f"jev_unavailable:{exc}"],
        }
        return result

    result["fields"] = fields
    result["note"] = "jev_ask 仅返回原始概率字段，不套用策略阈值"
    return result


# ========== 健康检查（云部署供负载均衡/探活使用） ==========

@mcp.custom_route("/health", methods=["GET"])
async def health(_request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "jev_mcp", "version": __version__})


# ========== 启动（云端 Streamable HTTP，客户端通过 URL 接入） ==========
if __name__ == "__main__":
    import os

    mcp.run(
        transport="http",
        host=os.environ.get("JEV_MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("JEV_MCP_PORT", "8800")),
        path=os.environ.get("JEV_MCP_PATH", "/mcp"),
    )
