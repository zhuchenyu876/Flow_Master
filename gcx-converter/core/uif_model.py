# -*- coding: utf-8 -*-
"""
Universal Intermediate Format (UIF) - 通用中间格式数据模型
============================================================
平台无关的工作流抽象层，作为各平台间互转的枢纽。

支持节点类型：
  start           - 开始节点
  message         - 发送消息（textReply）
  input           - 等待用户输入（captureUserReply）
  code            - 执行代码
  condition       - 条件判断（if/else）
  llm_var         - LLM 调用并将结果存入变量（llmVariableAssignment）
  llm_reply       - LLM 直接回复用户（llMReply）
  knowledge_query - 知识库检索（knowledgeAssignment）
  intent_router   - 意图路由（semanticJudgment / question-classifier）
  jump            - 跳转到其他 flow/workflow
  variable_assign - 纯变量赋值（简单 code 节点的语义化别名）
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


# ──────────────────────────────────────────────
# UIF 节点类型常量
# ──────────────────────────────────────────────
class NodeType:
    START           = "start"
    MESSAGE         = "message"
    INPUT           = "input"
    CODE            = "code"
    CONDITION       = "condition"
    LLM_VAR         = "llm_var"
    LLM_REPLY       = "llm_reply"
    KNOWLEDGE_QUERY = "knowledge_query"
    INTENT_ROUTER   = "intent_router"
    JUMP            = "jump"
    VARIABLE_ASSIGN = "variable_assign"


# ──────────────────────────────────────────────
# AgentStudio → UIF 节点类型映射
# ──────────────────────────────────────────────
AGENTSUDIO_TO_UIF = {
    "start":                NodeType.START,
    "textReply":            NodeType.MESSAGE,
    "captureUserReply":     NodeType.INPUT,
    "code":                 NodeType.CODE,
    "condition":            NodeType.CONDITION,
    "llmVariableAssignment": NodeType.LLM_VAR,
    "llMReply":             NodeType.LLM_REPLY,
    "knowledgeAssignment":  NodeType.KNOWLEDGE_QUERY,
    "semanticJudgment":     NodeType.INTENT_ROUTER,
    "jump":                 NodeType.JUMP,
}


# ──────────────────────────────────────────────
# 数据模型
# ──────────────────────────────────────────────

@dataclass
class UIFNode:
    """
    通用节点，对应工作流中的一个操作单元。

    type  : NodeType 中定义的类型字符串
    name  : 节点的唯一内部名称（来自 step2 的 name 字段，用于连边查找）
    title : 在画布上显示的节点标题
    data  : 节点类型特定的配置字典（详见各类型说明）

    各 type 的 data 结构：
    ─────────────────────
    start:
        {}  (无额外字段)

    message:
        text    : str           原始文本（从 payload 提取）
        payload : str           完整 JSON payload（含 buttons/type 等）

    input:
        variable_assign : str   捕获到的用户输入存入的变量名（通常是 last_user_response）

    code:
        code    : str           Python 代码字符串
        args    : List[str]     输入变量名列表
        outputs : List[str]     输出变量名列表

    condition:
        branches : List[UIFConditionBranch]

    llm_var:
        prompt_template : str       Prompt 模板（含 {{var}} 占位）
        variable_assign : str       结果存入的变量名
        llm_name        : str       模型名
        args            : List[str] Prompt 中引用的变量

    llm_reply:
        prompt_template : str
        llm_name        : str
        args            : List[str]

    knowledge_query:
        variable_assign  : str      检索结果存入的变量名
        knowledge_base_ids: List    知识库 ID 列表
        query_variable   : str      查询变量名（默认 last_user_response）

    intent_router:
        intents         : List[UIFIntent]   意图列表（含 training phrases）
        default_id      : str               fallback 分支 ID
        query_variable  : str               用于分类的输入变量（默认 last_user_response）

    jump:
        target_name : str   目标 flow/workflow 名称

    variable_assign:
        assignments : List[{variable: str, value: str}]
    """
    id:       str
    type:     str
    name:     str
    title:    str
    data:     Dict[str, Any]  = field(default_factory=dict)
    position: Dict[str, float] = field(default_factory=lambda: {"x": 0.0, "y": 0.0})


@dataclass
class UIFConditionBranch:
    """条件分支（对应 condition 节点的一个 if/elif/else 分支）"""
    branch_id:        str
    label:            str
    logical_operator: str                  = "and"   # "and" | "or" | "other"
    conditions:       List[Dict[str, Any]] = field(default_factory=list)
    # 每个 condition: {variable, operator, value}
    # operator: = ≠ > < ≥ ≤ contains


@dataclass
class UIFIntent:
    """意图定义（用于 intent_router）"""
    intent_id:        str
    name:             str
    training_phrases: List[str] = field(default_factory=list)


@dataclass
class UIFEdge:
    """
    节点之间的连边。

    source        : 源节点的 name
    target        : 目标节点的 name
    source_handle : 分支标识（默认为 "source"，
                    condition 节点用 condition_id，
                    intent_router 用 intent_id）
    label         : 可选的连线标签（调试用）
    """
    source:        str
    target:        str
    source_handle: str           = "source"
    label:         Optional[str] = None


@dataclass
class UIFFlow:
    """
    一个完整的对话流（对应 Google CX 的一个 Flow，或一个 Workflow）。
    """
    id:       str
    name:     str
    source_platform: str = "google_cx"
    language:        str = "en"
    nodes:    List[UIFNode]          = field(default_factory=list)
    edges:    List[UIFEdge]          = field(default_factory=list)
    # 会话级变量（在 Dify 中映射为 conversation_variables）
    conversation_variables: List[Dict[str, Any]] = field(default_factory=list)

    def get_node(self, name: str) -> Optional[UIFNode]:
        for n in self.nodes:
            if n.name == name:
                return n
        return None

    def get_outgoing_edges(self, name: str) -> List[UIFEdge]:
        return [e for e in self.edges if e.source == name]

    def get_incoming_edges(self, name: str) -> List[UIFEdge]:
        return [e for e in self.edges if e.target == name]


@dataclass
class UIFDocument:
    """
    完整的迁移文档，包含多个 Flow。
    """
    version:         str = "1.0"
    name:            str = ""
    description:     str = ""
    language:        str = "en"
    source_platform: str = "google_cx"
    target_platform: str = ""
    flows: List[UIFFlow] = field(default_factory=list)
