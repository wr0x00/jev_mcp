"""领域 questions 模板 —— Jev 只做有限类型判断（Choice / Score / Noul），不生成文本。

类型约定：
- Choice: 在有限枚举中选一项，返回 (value, p)
- Score : 打分 0~1，返回 p
- Noul  : 是否判断（为"是"的概率），返回 p
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

VALID_TYPES = {"choice", "score", "noul"}

# 疑似 prompt injection 的常见模式（只告警，不阻断；阻断策略在 policies 层）
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|above|prior|earlier)\s+(instructions|prompts?|messages?)",
    r"disregard\s+(all\s+)?(previous|above|prior)",
    r"you\s+are\s+now\s+(a|an|no\s+longer)",
    r"(system|developer)\s+(prompt|message)\s*[:：]",
    r"</?(system|assistant|tool)>",
]


@dataclass
class Question:
    name: str
    type: str  # choice | score | noul
    prompt: str
    options: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {"name": self.name, "type": self.type, "prompt": self.prompt}
        if self.options:
            d["options"] = list(self.options)
        return d


# ① jev_route_task：任务路由 / 难度 / 紧急度
ROUTE_QUESTIONS = [
    Question(
        name="task_type",
        type="choice",
        prompt="该用户请求最适合归入哪类执行路径？",
        options=[
            "direct_query",   # 直接查询/检索即可回答
            "code_change",    # 需要改动代码
            "research",       # 开放性调研
            "ops_action",     # 运维/发布/环境操作
            "communication",  # 对外沟通（邮件/消息）
            "other",
        ],
    ),
    Question(
        name="difficulty",
        type="score",
        prompt="该任务对 Agent 的执行难度（0=机械简单，1=高度不确定、多步且易错）",
    ),
    Question(
        name="is_urgent",
        type="noul",
        prompt="该请求是否具有紧急/生产事故性质？",
    ),
]

# ② jev_tool_gate：工具执行前风险门控
TOOL_GATE_QUESTIONS = [
    Question(
        name="destructive",
        type="noul",
        prompt="该计划动作是否具有破坏性（删除、覆盖、不可逆写入、生产发布等）？",
    ),
    Question(
        name="exfiltration",
        type="noul",
        prompt="该动作是否会把数据发送到任务边界之外（公网、外部邮箱、第三方服务）？",
    ),
    Question(
        name="scope_breach",
        type="noul",
        prompt="该动作是否超出用户请求所隐含的授权/范围？",
    ),
    Question(
        name="reversibility",
        type="score",
        prompt="该动作失败或撤销的难易程度（0=不可逆，1=完全可逆）",
    ),
]

# ③ jev_verify_done：执行后语义验证
VERIFY_QUESTIONS = [
    Question(
        name="covers_user_request",
        type="noul",
        prompt="执行结果是否完整覆盖了用户请求的核心诉求？",
    ),
    Question(
        name="has_unverified_claim",
        type="noul",
        prompt="结果陈述中是否包含未经验证的事实断言（未实际检查就声称已完成/已通过）？",
    ),
    Question(
        name="repeated_action",
        type="noul",
        prompt="最近一步是否与之前的步骤高度重复（疑似空转循环）？",
    ),
]


def sanitize_text(text: str, max_chars: int = 8000) -> tuple[str, list[str]]:
    """外部文本进入 state 前脱敏/隔离：去控制字符、截断、标记疑似注入。

    返回 (清洗后的文本, warnings 列表)。
    """
    warnings: list[str] = []
    if not text:
        return "", warnings
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars]
        warnings.append(f"text_truncated:{max_chars}")
    for pat in _INJECTION_PATTERNS:
        if re.search(pat, cleaned, flags=re.IGNORECASE):
            warnings.append(f"possible_prompt_injection:{pat}")
            break
    return cleaned, warnings


def validate_questions(questions: list) -> list[str]:
    """jev_ask 入参校验，返回错误列表（空列表 = 合法）。"""
    errors: list[str] = []
    if not isinstance(questions, list) or not questions:
        return ["questions 必须是非空数组"]
    for i, q in enumerate(questions):
        tag = f"questions[{i}]"
        if not isinstance(q, dict):
            errors.append(f"{tag}: 必须是对象")
            continue
        if q.get("type") not in VALID_TYPES:
            errors.append(f"{tag}.type 必须是 {'/'.join(sorted(VALID_TYPES))}")
        if not q.get("name"):
            errors.append(f"{tag}.name 不能为空")
        if not q.get("prompt"):
            errors.append(f"{tag}.prompt 不能为空")
        if q.get("type") == "choice" and not q.get("options"):
            errors.append(f"{tag}: choice 类型必须提供 options")
    return errors
