"""阈值/决策逻辑 —— 只消费 Jev 返回的结构化概率，输出保守决策。

各层 action 词汇约定：
- route : auto_execute / human_review
- gate  : auto_execute / human_review / block
- verify: pass / continue / human_review
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).with_name("config.yaml")


def _apply_env(cfg: dict) -> None:
    """把 config.yaml 的 env 段注入进程环境（setdefault：真实环境变量优先，YAML 兜底）。"""
    for key, val in (cfg.get("env") or {}).items():
        if val is None or val == "":
            continue
        os.environ.setdefault(str(key), str(val))


def load_config(path: Path | None = None) -> dict:
    with open(path or CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    _apply_env(cfg)
    return cfg


@dataclass
class Decision:
    action: str
    confidence: float
    reasons: list[str] = field(default_factory=list)
    degraded: bool = False


def _pf(fields: dict, name: str) -> float | None:
    """取字段概率；字段缺失/非法返回 None，由各 evaluator 决定保守方向。"""
    item = fields.get(name)
    if not isinstance(item, dict):
        return None
    try:
        return max(0.0, min(1.0, float(item.get("p", 0.0))))
    except (TypeError, ValueError):
        return None


def degraded_decision(cfg: dict, reason: str) -> Decision:
    """Jev 超时/不可用：默认保守，绝不自动放行。"""
    action = cfg.get("safety", {}).get("on_jev_unavailable", "human_review")
    return Decision(action=action, confidence=0.0, reasons=[f"jev_unavailable:{reason}"], degraded=True)


# ① 路由决策
def evaluate_route(fields: dict, cfg: dict) -> Decision:
    c = cfg["route"]
    p = _pf(fields, "task_type")
    difficulty = _pf(fields, "difficulty")
    if p is None or difficulty is None:
        return Decision("human_review", 0.0, ["字段缺失: task_type/difficulty"])
    task_type = (fields.get("task_type") or {}).get("value")
    urgent = _pf(fields, "is_urgent") or 0.0

    if p < c["review"]:
        return Decision("human_review", p, [f"路由置信度 {p:.2f} < review {c['review']}"])
    if difficulty >= c["difficulty_review"]:
        return Decision("human_review", p, [f"难度 {difficulty:.2f} >= difficulty_review {c['difficulty_review']}"])
    if p >= c["auto_execute"] and urgent < c["review"]:
        return Decision("auto_execute", p,
                        [f"task_type={task_type}", f"置信度 {p:.2f} >= auto_execute {c['auto_execute']}"])
    return Decision("human_review", p, [f"task_type={task_type}", "置信度处于 review 区间或任务紧急，转人工确认"])


# ② 风险门控
def evaluate_tool_gate(fields: dict, cfg: dict) -> Decision:
    c = cfg["tool_gate"]
    destructive = _pf(fields, "destructive")
    exfiltration = _pf(fields, "exfiltration")
    scope_breach = _pf(fields, "scope_breach")
    reversibility = _pf(fields, "reversibility")
    missing = [n for n, v in [
        ("destructive", destructive), ("exfiltration", exfiltration),
        ("scope_breach", scope_breach), ("reversibility", reversibility),
    ] if v is None]
    if missing:
        return Decision("human_review", 0.0, [f"字段缺失: {','.join(missing)}"])

    if exfiltration > c["exfiltration_threshold"]:
        return Decision("block", exfiltration,
                        [f"外泄概率 {exfiltration:.2f} > exfiltration_threshold {c['exfiltration_threshold']}"])
    if scope_breach > c["scope_breach_threshold"]:
        return Decision("block", scope_breach,
                        [f"越界概率 {scope_breach:.2f} > scope_breach_threshold {c['scope_breach_threshold']}"])
    if destructive > c["strict_destructive_threshold"]:
        return Decision("human_review", destructive,
                        [f"破坏性 {destructive:.2f} > strict_destructive_threshold {c['strict_destructive_threshold']}，"
                         f"需硬规则+人工审批"])
    if destructive > c["auto_execute_destructive_threshold"] or reversibility < c["reversibility_min"]:
        return Decision("human_review", destructive,
                        [f"破坏性/不可逆程度中等（destructive={destructive:.2f}, reversibility={reversibility:.2f}）"])
    return Decision("auto_execute", reversibility, ["低破坏性且可逆，允许自动执行"])


# ③ 完成验证
def evaluate_verify(fields: dict, loop_count: int, cfg: dict) -> Decision:
    c = cfg["verify"]
    covers = _pf(fields, "covers_user_request")
    if covers is None:
        return Decision("continue", 0.0, ["字段缺失: covers_user_request，保守继续循环"])
    unverified = _pf(fields, "has_unverified_claim") or 0.0
    repeated = _pf(fields, "repeated_action") or 0.0

    if covers < c["covers_min_noul"]:
        return Decision("continue", covers,
                        [f"覆盖率 {covers:.2f} < covers_min_noul {c['covers_min_noul']}，继续循环"])
    if unverified > c["has_unverified_claim_max"]:
        return Decision("human_review", unverified,
                        [f"未验证声明 {unverified:.2f} > has_unverified_claim_max {c['has_unverified_claim_max']}"])
    if repeated > c["repeated_action_max"]:
        return Decision("human_review", repeated,
                        [f"疑似重复动作 {repeated:.2f} > repeated_action_max {c['repeated_action_max']}"])
    if loop_count >= c["max_loops"]:
        return Decision("human_review", covers, [f"已达 max_loops {c['max_loops']}，升级人工"])
    return Decision("pass", covers, ["覆盖充分且无可疑声明，判定完成"])
