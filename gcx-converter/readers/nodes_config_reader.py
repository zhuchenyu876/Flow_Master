# -*- coding: utf-8 -*-
"""
Nodes Config Reader
===================
将 step2 生成的 nodes_config_*.json + edge_config_*.json 转换为 UIF（通用中间格式）。

输入文件：
  nodes_config_<flow_name>.json  - 节点列表（AgentStudio 内部格式）
  edge_config_<flow_name>.json   - 边列表

输出：
  UIFFlow 对象，可传入任意 Writer（Dify、n8n、Coze 等）

用法：
  from readers.nodes_config_reader import NodesConfigReader
  flow = NodesConfigReader.read(
      nodes_file="output/.../nodes_config_CardFlow.json",
      edges_file="output/.../edge_config_CardFlow.json",
      flow_name="CardFlow",
      language="en"
  )
"""

import json
import uuid
import os
from typing import Dict, List, Any, Optional

from core.uif_model import (
    UIFFlow, UIFNode, UIFEdge, UIFConditionBranch, UIFIntent,
    NodeType, AGENTSUDIO_TO_UIF
)


def _gen_id() -> str:
    return str(uuid.uuid4()).replace("-", "")[:16]


class NodesConfigReader:
    """
    读取 step2 生成的 nodes_config + edge_config，转换为 UIFFlow。

    节点类型映射（AgentStudio → UIF）：
      start                → start
      textReply            → message
      captureUserReply     → input
      code                 → code
      condition            → condition
      llmVariableAssignment→ llm_var
      llMReply             → llm_reply
      knowledgeAssignment  → knowledge_query
      semanticJudgment     → intent_router
      jump                 → jump
    """

    @classmethod
    def read(
        cls,
        nodes_file: str,
        edges_file: str,
        flow_name: str = "",
        language: str  = "en"
    ) -> UIFFlow:
        """
        读取 nodes_config + edge_config 文件，返回 UIFFlow。

        Args:
            nodes_file : nodes_config_*.json 路径
            edges_file : edge_config_*.json 路径
            flow_name  : flow 名称（若留空，从文件名推断）
            language   : 语言代码
        """
        with open(nodes_file, "r", encoding="utf-8") as f:
            nodes_raw: List[Dict] = json.load(f)
        with open(edges_file, "r", encoding="utf-8") as f:
            edges_raw: List[Dict] = json.load(f)

        if not flow_name:
            basename = os.path.basename(nodes_file)
            flow_name = basename.replace("nodes_config_", "").replace(".json", "")

        flow = UIFFlow(
            id       = _gen_id(),
            name     = flow_name,
            language = language,
            source_platform = "google_cx"
        )

        # ── 1. 转换节点 ──────────────────────────────────────
        for raw in nodes_raw:
            node = cls._convert_node(raw)
            if node:
                flow.nodes.append(node)

        # ── 2. 转换边 ────────────────────────────────────────
        for raw in edges_raw:
            edge = cls._convert_edge(raw)
            if edge:
                flow.edges.append(edge)

        return flow

    # ─────────────────────────────────────────────────────────
    # 节点转换
    # ─────────────────────────────────────────────────────────

    @classmethod
    def _convert_node(cls, raw: Dict) -> Optional[UIFNode]:
        as_type = raw.get("type", "")
        uif_type = AGENTSUDIO_TO_UIF.get(as_type)
        if not uif_type:
            # 未知类型，跳过
            return None

        name  = raw.get("name", _gen_id())
        title = raw.get("title", name)
        data  = {}

        if uif_type == NodeType.START:
            data = {}

        elif uif_type == NodeType.MESSAGE:
            # textReply：提取 payload 或 plain_text
            payload = raw.get("payload", "")
            plain   = raw.get("plain_text", [])
            text    = ""
            if plain:
                first = plain[0]
                text  = first.get("text", "") if isinstance(first, dict) else str(first)
            elif payload:
                # payload 可能是 JSON 字符串
                try:
                    p = json.loads(payload) if isinstance(payload, str) else payload
                    text = p.get("text", str(payload))
                except Exception:
                    text = str(payload)
            data = {
                "text":    text,
                "payload": payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
            }

        elif uif_type == NodeType.INPUT:
            data = {
                "variable_assign": raw.get("variable_assign", "last_user_response")
            }

        elif uif_type == NodeType.CODE:
            outputs = raw.get("outputs", [])
            # outputs 可能是 str 列表或 dict 列表
            out_names = []
            for o in outputs:
                if isinstance(o, str):
                    out_names.append(o)
                elif isinstance(o, dict):
                    out_names.append(o.get("variable_assign") or o.get("name", ""))
            data = {
                "code":    raw.get("code", ""),
                "args":    raw.get("args", []),
                "outputs": out_names,
                "title":   title
            }

        elif uif_type == NodeType.CONDITION:
            branches = cls._parse_condition_branches(raw)
            data = {"branches": branches}

        elif uif_type == NodeType.LLM_VAR:
            data = {
                "prompt_template": raw.get("prompt_template", ""),
                "variable_assign": raw.get("variable_assign", "llm_result"),
                "llm_name":        raw.get("llm_name", "gpt-4o-mini"),
                "args":            cls._extract_template_vars(raw.get("prompt_template", ""))
            }

        elif uif_type == NodeType.LLM_REPLY:
            data = {
                "prompt_template": raw.get("prompt_template", ""),
                "llm_name":        raw.get("llm_name", "gpt-4o-mini"),
                "args":            cls._extract_template_vars(raw.get("prompt_template", ""))
            }

        elif uif_type == NodeType.KNOWLEDGE_QUERY:
            data = {
                "variable_assign":   raw.get("variable_assign", "rag_result"),
                "knowledge_base_ids": raw.get("knowledge_base_ids", []),
                "query_variable":     "last_user_response"
            }

        elif uif_type == NodeType.INTENT_ROUTER:
            intents, default_id = cls._parse_intent_router(raw)
            query_var = raw.get("query_variable", "last_user_response")
            data = {
                "intents":       intents,
                "default_id":    default_id,
                "query_variable": query_var
            }

        elif uif_type == NodeType.JUMP:
            data = {
                "target_name": raw.get("jump_robot_name", raw.get("title", ""))
            }

        return UIFNode(
            id    = _gen_id(),
            type  = uif_type,
            name  = name,
            title = title,
            data  = data
        )

    # ─────────────────────────────────────────────────────────
    # 条件分支解析
    # ─────────────────────────────────────────────────────────

    @classmethod
    def _parse_condition_branches(cls, raw: Dict) -> List[UIFConditionBranch]:
        branches = []
        raw_conditions = raw.get("if_else_conditions", [])

        for rc in raw_conditions:
            logical = rc.get("logical_operator", "and")
            cond_id = rc.get("condition_id", _gen_id())
            label   = rc.get("condition_name", "")

            raw_conds = rc.get("conditions", [])
            parsed_conds = []
            for c in raw_conds:
                parsed_conds.append({
                    "variable": c.get("condition_variable", ""),
                    "operator": cls._normalize_operator(c.get("comparison_operator", "=")),
                    "value":    str(c.get("condition_value", ""))
                })

            branches.append(UIFConditionBranch(
                branch_id        = cond_id,
                label            = label,
                logical_operator = logical,
                conditions       = parsed_conds
            ))

        return branches

    # ─────────────────────────────────────────────────────────
    # 意图路由解析（semanticJudgment）
    # ─────────────────────────────────────────────────────────

    @classmethod
    def _parse_intent_router(cls, raw: Dict) -> tuple:
        """
        解析 semanticJudgment 节点，返回 (intents: List[UIFIntent], default_id: str)。
        """
        # 两种格式：直接的 config 字典，或嵌套在 config.config 中
        cfg = raw.get("config", raw)
        semantic_conditions = cfg.get("semantic_conditions", [])
        default_condition   = cfg.get("default_condition", {})

        intents = []
        for sc in semantic_conditions:
            cond_id  = sc.get("condition_id", _gen_id())
            name     = sc.get("condition_name", "")
            phrases  = sc.get("refer_questions", [])
            intents.append(UIFIntent(
                intent_id        = cond_id,
                name             = name,
                training_phrases = phrases
            ))

        default_id = default_condition.get("condition_id", _gen_id())
        return intents, default_id

    # ─────────────────────────────────────────────────────────
    # 边转换
    # ─────────────────────────────────────────────────────────

    @classmethod
    def _convert_edge(cls, raw: Dict) -> Optional[UIFEdge]:
        source = raw.get("source_node", "")
        target = raw.get("target_node", "")
        if not source or not target:
            return None

        conn_type = raw.get("connection_type", "default")
        # condition/semantic 分支边带 condition_id
        handle = raw.get("condition_id", "source")
        if conn_type == "default":
            handle = "source"

        return UIFEdge(
            source        = source,
            target        = target,
            source_handle = handle,
            label         = raw.get("label")
        )

    # ─────────────────────────────────────────────────────────
    # 工具函数
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_operator(op: str) -> str:
        """统一比较运算符格式"""
        return {
            "=":        "==",
            "≠":        "!=",
            "≥":        ">=",
            "≤":        "<=",
            "EQUALS":   "==",
            "NOT_EQUALS": "!=",
            "CONTAINS": "contains",
        }.get(op, op)

    @staticmethod
    def _extract_template_vars(template: str) -> List[str]:
        """从 {{var}} 模板中提取变量名列表"""
        import re
        return list(dict.fromkeys(re.findall(r"\{\{([^}]+)\}\}", template)))
