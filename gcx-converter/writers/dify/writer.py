# -*- coding: utf-8 -*-
"""
Dify Chatflow Writer
====================
将 UIFFlow 转换为 Dify Advanced-Chat 格式的 YAML 文件。

关键设计决策：
  1. Dify chatflow 是「每轮触发型」：每条用户消息触发整个流程一次。
     因此 `captureUserReply`（input）节点被「跳过」，
     其 variable_assign 变量被重映射到 start 节点的 sys.query。

  2. `semanticJudgment`（intent_router）→ Dify `question-classifier` 节点，
     每个意图映射为一个 class，default 分支为 fallback。

  3. 变量引用系统：
     Dify 中变量以 [node_id, var_name] 二元组引用。
     转换过程中维护 var_registry: {var_name → [dify_node_id, output_key]}

  4. Prompt 中的 {{var_name}} 被替换为 Dify 格式 {{#node_id.var_name#}}。

节点类型映射：
  UIF start           → Dify start
  UIF message         → Dify answer
  UIF input           → 跳过（重映射 variable_assign → sys.query）
  UIF code            → Dify code
  UIF condition       → Dify if-else
  UIF llm_var         → Dify llm（output variable 用 code 节点提取）
  UIF llm_reply       → Dify llm
  UIF knowledge_query → Dify knowledge-retrieval
  UIF intent_router   → Dify question-classifier
  UIF jump            → Dify answer（提示跳转目标）
  UIF variable_assign → Dify variable-assigner
"""

import uuid
import time
import json
import re
from typing import Dict, List, Any, Optional, Tuple

from core.uif_model import UIFFlow, UIFNode, UIFEdge, UIFConditionBranch, UIFIntent, NodeType
from core import yaml_dumper


# ──────────────────────────────────────────────
# 辅助：ID 生成
# ──────────────────────────────────────────────

_id_counter = [int(time.time() * 1000)]

def _nid() -> str:
    """生成 Dify 风格的节点 ID（13 位时间戳字符串）"""
    _id_counter[0] += 1
    return str(_id_counter[0])

def _uid() -> str:
    """生成标准 UUID"""
    return str(uuid.uuid4())


# ──────────────────────────────────────────────
# 坐标布局（简单横向排列）
# ──────────────────────────────────────────────

class SimpleLayout:
    """
    从左到右的简单瀑布布局。
    按拓扑顺序给每个节点分配 (x, y)，同一层的节点垂直排列。
    """
    X_STEP = 350
    Y_STEP = 160

    def __init__(self):
        self._col: Dict[str, int] = {}   # node_name → 列编号
        self._row: Dict[int, int] = {}   # 列编号 → 当前行数

    def assign(self, nodes: List[UIFNode], edges: List[UIFEdge]) -> Dict[str, Tuple[float, float]]:
        """返回 {node_name → (x, y)} 字典"""
        # 拓扑排序
        order = self._topo_sort(nodes, edges)
        positions: Dict[str, Tuple[float, float]] = {}
        col_counter: Dict[str, int] = {}

        for name in order:
            # 找前驱最大列
            in_edges = [e for e in edges if e.target == name]
            max_pred_col = max(
                (col_counter.get(e.source, 0) for e in in_edges),
                default=-1
            )
            col = max_pred_col + 1
            col_counter[name] = col

            row = self._row.get(col, 0)
            self._row[col] = row + 1

            positions[name] = (col * self.X_STEP, row * self.Y_STEP)

        return positions

    @staticmethod
    def _topo_sort(nodes: List[UIFNode], edges: List[UIFEdge]) -> List[str]:
        names = [n.name for n in nodes]
        adj: Dict[str, List[str]] = {n: [] for n in names}
        in_deg: Dict[str, int]    = {n: 0  for n in names}

        for e in edges:
            if e.source in adj and e.target in adj:
                adj[e.source].append(e.target)
                in_deg[e.target] += 1

        queue = [n for n in names if in_deg[n] == 0]
        result = []
        while queue:
            cur = queue.pop(0)
            result.append(cur)
            for nxt in adj.get(cur, []):
                in_deg[nxt] -= 1
                if in_deg[nxt] == 0:
                    queue.append(nxt)
        # 剩余节点（有环或孤立）追加到末尾
        result += [n for n in names if n not in result]
        return result


# ──────────────────────────────────────────────
# 变量注册表
# ──────────────────────────────────────────────

class VarRegistry:
    """
    维护变量名 → Dify 变量选择器 [node_id, var_key] 的映射。

    特殊规则：
      - last_user_response / sys.query → [start_node_id, sys.query]
      - captureUserReply.variable_assign 统一重映射到 sys.query
    """
    SYS_QUERY_VAR = "last_user_response"

    def __init__(self, start_node_id: str):
        self._map: Dict[str, List[str]] = {}
        self._start_id = start_node_id
        # 注册 sys.query
        self._map["sys.query"]          = [start_node_id, "sys.query"]
        self._map["last_user_response"] = [start_node_id, "sys.query"]

    def register(self, var_name: str, dify_node_id: str, output_key: str):
        self._map[var_name.lower()] = [dify_node_id, output_key]

    def register_input_node(self, uif_node: UIFNode):
        """captureUserReply → 重映射 variable_assign 到 sys.query"""
        var = uif_node.data.get("variable_assign", "last_user_response")
        self._map[var.lower()] = [self._start_id, "sys.query"]

    def get(self, var_name: str) -> List[str]:
        key = var_name.strip().lower()
        return self._map.get(key, [self._start_id, "sys.query"])

    def replace_prompt_vars(self, template: str) -> str:
        """将 prompt 中的 {{var}} 替换为 Dify 格式 {{#node_id.var#}}"""
        def replacer(m):
            var = m.group(1).strip()
            sel = self.get(var)
            return "{{#" + sel[0] + "." + sel[1] + "#}}"
        return re.sub(r"\{\{([^}]+)\}\}", replacer, template)


# ──────────────────────────────────────────────
# Dify 节点模板
# ──────────────────────────────────────────────

def _make_dify_node(
    dify_id: str,
    node_type: str,
    title: str,
    data: Dict,
    x: float = 0.0,
    y: float = 0.0,
    height: int = 87,
    width:  int = 242,
) -> Dict:
    """生成 Dify 节点的标准外层结构"""
    base = {**data, "selected": False, "title": title, "type": node_type}
    return {
        "data":             base,
        "height":           height,
        "id":               dify_id,
        "position":         {"x": round(x, 2), "y": round(y, 2)},
        "positionAbsolute": {"x": round(x, 2), "y": round(y, 2)},
        "selected":         False,
        "sourcePosition":   "right",
        "targetPosition":   "left",
        "type":             "custom",
        "width":            width,
    }


def _make_edge(
    src_id: str, src_handle: str,
    tgt_id: str,
    src_type: str, tgt_type: str,
    z: int = 0
) -> Dict:
    return {
        "data":         {"isInLoop": False, "sourceType": src_type, "targetType": tgt_type},
        "id":           f"{src_id}-{src_handle}-{tgt_id}-target",
        "source":       src_id,
        "sourceHandle": src_handle,
        "target":       tgt_id,
        "targetHandle": "target",
        "type":         "custom",
        "zIndex":       z,
    }


# ──────────────────────────────────────────────
# 主 Writer 类
# ──────────────────────────────────────────────

class DifyWriter:
    """
    将 UIFFlow 转换为 Dify Advanced-Chat 格式的 dict（可序列化为 YAML）。

    调用方式：
        writer = DifyWriter()
        dify_dict = writer.write(uif_flow)
        yaml_str  = writer.to_yaml(dify_dict)
    """

    DEFAULT_MODEL = {
        "completion_params": {"temperature": 0.7},
        "mode":     "chat",
        "name":     "gpt-4o-mini",
        "provider": "openai",
    }

    def write(self, flow: UIFFlow, model_config: Optional[Dict] = None) -> Dict:
        """
        转换单个 UIFFlow → Dify chatflow dict。

        Args:
            flow         : UIFFlow 对象
            model_config : 可选，覆盖默认的模型配置

        Returns:
            可直接序列化为 YAML 的 dict
        """
        self._model = model_config or self.DEFAULT_MODEL
        self._name_to_dify_id: Dict[str, str] = {}   # uif_node.name → dify node id
        self._name_to_dtype:   Dict[str, str] = {}   # uif_node.name → dify type string

        # ── Step 1: 分配 Dify 节点 ID ──────────────────────
        start_dify_id = _nid()
        for node in flow.nodes:
            if node.type == NodeType.START:
                self._name_to_dify_id[node.name] = start_dify_id
            else:
                self._name_to_dify_id[node.name] = _nid()

        # ── Step 2: 建立变量注册表 ──────────────────────────
        self._vars = VarRegistry(start_dify_id)
        # 预扫描：注册所有 input 节点的变量
        for node in flow.nodes:
            if node.type == NodeType.INPUT:
                self._vars.register_input_node(node)

        # ── Step 3: 布局 ────────────────────────────────────
        layout = SimpleLayout()
        positions = layout.assign(flow.nodes, flow.edges)

        # ── Step 4: 转换节点 ────────────────────────────────
        dify_nodes: List[Dict] = []
        # 跳过 input 节点（但记录变量映射）
        skip_names = set()
        for node in flow.nodes:
            if node.type == NodeType.INPUT:
                skip_names.add(node.name)

        for node in flow.nodes:
            x, y = positions.get(node.name, (0.0, 0.0))
            dify_node = self._convert_node(node, x, y)
            if dify_node:
                dify_nodes.append(dify_node)

        # ── Step 5: 转换边 ────────────────────────────────────
        # 需要「绕过」被跳过的 input 节点：
        #   A → input_B → C  变成  A → C
        dify_edges = self._convert_edges(flow, skip_names)

        # ── Step 6: 组装完整文档 ─────────────────────────────
        return self._build_document(flow, dify_nodes, dify_edges)

    # ─────────────────────────────────────────────────────────
    # 节点转换
    # ─────────────────────────────────────────────────────────

    def _convert_node(self, node: UIFNode, x: float, y: float) -> Optional[Dict]:
        did   = self._name_to_dify_id[node.name]
        dtype = ""
        dnode = None

        if node.type == NodeType.START:
            dtype = "start"
            self._name_to_dtype[node.name] = dtype
            dnode = _make_dify_node(did, "start", node.title or "开始",
                                    {"desc": "", "variables": []}, x, y, height=72)

        elif node.type == NodeType.INPUT:
            # 跳过，不生成 Dify 节点
            self._name_to_dtype[node.name] = "input_skip"
            return None

        elif node.type == NodeType.MESSAGE:
            dtype = "answer"
            self._name_to_dtype[node.name] = dtype
            text = self._vars.replace_prompt_vars(node.data.get("text", ""))
            dnode = _make_dify_node(did, "answer", node.title or "回复",
                                    {"answer": text, "variables": []}, x, y, height=83)

        elif node.type == NodeType.CODE:
            dtype = "code"
            self._name_to_dtype[node.name] = dtype
            dnode, out_vars = self._build_code_node(node, did, x, y)
            for v in out_vars:
                self._vars.register(v, did, v)

        elif node.type == NodeType.CONDITION:
            dtype = "if-else"
            self._name_to_dtype[node.name] = dtype
            dnode = self._build_if_else_node(node, did, x, y)

        elif node.type == NodeType.LLM_VAR:
            dtype = "llm"
            self._name_to_dtype[node.name] = dtype
            dnode = self._build_llm_node(node, did, x, y, reply_mode=False)
            # LLM 输出默认为 "text" key
            var_assign = node.data.get("variable_assign", "llm_result")
            self._vars.register(var_assign, did, "text")

        elif node.type == NodeType.LLM_REPLY:
            dtype = "llm"
            self._name_to_dtype[node.name] = dtype
            dnode = self._build_llm_node(node, did, x, y, reply_mode=True)

        elif node.type == NodeType.KNOWLEDGE_QUERY:
            dtype = "knowledge-retrieval"
            self._name_to_dtype[node.name] = dtype
            dnode = self._build_knowledge_node(node, did, x, y)
            var_assign = node.data.get("variable_assign", "rag_result")
            self._vars.register(var_assign, did, "output")

        elif node.type == NodeType.INTENT_ROUTER:
            dtype = "question-classifier"
            self._name_to_dtype[node.name] = dtype
            dnode = self._build_question_classifier(node, did, x, y)

        elif node.type == NodeType.JUMP:
            dtype = "answer"
            self._name_to_dtype[node.name] = dtype
            target = node.data.get("target_name", "")
            dnode = _make_dify_node(did, "answer", node.title or f"跳转至 {target}",
                                    {"answer": f"[转接至 {target}]", "variables": []},
                                    x, y, height=83)

        elif node.type == NodeType.VARIABLE_ASSIGN:
            dtype = "variable-assigner"
            self._name_to_dtype[node.name] = dtype
            dnode = self._build_variable_assigner(node, did, x, y)

        if dnode:
            self._name_to_dtype.setdefault(node.name, dtype)
        return dnode

    # ─────────────────────────────────────────────────────────
    # 各节点类型的构建函数
    # ─────────────────────────────────────────────────────────

    def _build_code_node(self, node: UIFNode, did: str, x: float, y: float) -> Tuple[Dict, List[str]]:
        """构建 Dify code 节点，返回 (dify_node, output_var_names)"""
        args    = node.data.get("args", [])
        outputs = node.data.get("outputs", [])
        code    = node.data.get("code", "")

        # 构建 variables（输入变量，引用其他节点的输出）
        variables = []
        for arg in args:
            sel = self._vars.get(arg)
            variables.append({
                "variable":      arg,
                "value_selector": sel
            })

        # 构建 outputs dict
        outputs_dict = {v: {"type": "string"} for v in outputs}

        data = {
            "code":          code,
            "code_language": "python3",
            "outputs":       outputs_dict,
            "variables":     variables,
        }
        dnode = _make_dify_node(did, "code", node.title or "代码执行", data, x, y, height=52)
        return dnode, outputs

    def _build_if_else_node(self, node: UIFNode, did: str, x: float, y: float) -> Dict:
        """构建 Dify if-else 节点"""
        branches: List[UIFConditionBranch] = node.data.get("branches", [])

        # 运算符映射
        op_map = {
            "==":       "==",
            "!=":       "!=",
            ">":        ">",
            "<":        "<",
            ">=":       ">=",
            "<=":       "<=",
            "contains": "contains",
            "=":        "==",
            "≠":        "!=",
            "≥":        ">=",
            "≤":        "<=",
        }

        cases = []
        for b in branches:
            if b.logical_operator == "other":
                # else 分支不含 conditions
                cases.append({
                    "case_id":         b.branch_id,
                    "conditions":      [],
                    "logical_operator": "and",
                    "id":              b.branch_id,
                })
                continue

            dify_conds = []
            for c in b.conditions:
                var_name = c.get("variable", "")
                sel      = self._vars.get(var_name)
                dify_conds.append({
                    "comparison_operator": op_map.get(c.get("operator", "=="), "=="),
                    "id":                  _uid(),
                    "value":               c.get("value", ""),
                    "varType":             "string",
                    "variable_selector":   sel,
                })

            cases.append({
                "case_id":          b.branch_id,
                "conditions":       dify_conds,
                "id":               b.branch_id,
                "logical_operator": b.logical_operator,
            })

        # 保证最后有一个 else 分支（如果没有 other）
        if not any(b.logical_operator == "other" for b in branches):
            else_id = _uid()
            cases.append({
                "case_id":          else_id,
                "conditions":       [],
                "id":               else_id,
                "logical_operator": "and",
            })

        data  = {"cases": cases}
        h     = max(100, 60 + len(cases) * 44)
        dnode = _make_dify_node(did, "if-else", node.title or "条件分支", data, x, y, height=h)
        return dnode

    def _build_llm_node(self, node: UIFNode, did: str, x: float, y: float, reply_mode: bool) -> Dict:
        """构建 Dify llm 节点"""
        prompt_raw = node.data.get("prompt_template", "")
        prompt_str = self._vars.replace_prompt_vars(prompt_raw)

        data = {
            "context": {"enabled": False, "variable_selector": []},
            "model":   self._model,
            "prompt_template": [
                {"id": _uid(), "role": "system", "text": ""},
                {"id": _uid(), "role": "user",   "text": prompt_str},
            ],
            "vision": {"enabled": False},
        }
        title = node.title or ("LLM 回复" if reply_mode else "LLM 处理")
        return _make_dify_node(did, "llm", title, data, x, y, height=87)

    def _build_knowledge_node(self, node: UIFNode, did: str, x: float, y: float) -> Dict:
        """构建 Dify knowledge-retrieval 节点"""
        query_var = node.data.get("query_variable", "last_user_response")
        query_sel = self._vars.get(query_var)

        data = {
            "dataset_ids": node.data.get("knowledge_base_ids", []),
            "multiple_retrieval_config": {
                "reranking_enable": False,
                "top_k":            4,
            },
            "query_attachment_selector": [],
            "query_variable_selector":   query_sel,
            "retrieval_mode":            "multiple",
        }
        return _make_dify_node(did, "knowledge-retrieval", node.title or "知识检索", data, x, y, height=51)

    def _build_question_classifier(self, node: UIFNode, did: str, x: float, y: float) -> Dict:
        """构建 Dify question-classifier 节点（对应 semanticJudgment）"""
        intents: List[UIFIntent] = node.data.get("intents", [])
        query_var = node.data.get("query_variable", "last_user_response")
        query_sel = self._vars.get(query_var)

        classes = [
            {"id": intent.intent_id, "name": intent.name}
            for intent in intents
        ]

        data = {
            "classes":              classes,
            "instruction":          "",
            "model":                self._model,
            "query_variable_selector": query_sel,
            "vision":               {"enabled": False},
        }
        h     = max(100, 60 + len(classes) * 32)
        dnode = _make_dify_node(did, "question-classifier", node.title or "意图分类", data, x, y, height=h)
        return dnode

    def _build_variable_assigner(self, node: UIFNode, did: str, x: float, y: float) -> Dict:
        assignments = node.data.get("assignments", [])
        variables = []
        for a in assignments:
            var_name = a.get("variable", "")
            # value 可能是变量引用或字面量
            value = a.get("value", "")
            if value.startswith("{{") and value.endswith("}}"):
                ref = value[2:-2].strip()
                sel = self._vars.get(ref)
            else:
                sel = [self._name_to_dify_id.get("start", "start"), "sys.query"]
            variables.append({
                "variable":      var_name,
                "value_selector": sel,
            })
        data = {"variables": variables, "output_type": "string"}
        return _make_dify_node(did, "variable-assigner", node.title or "变量赋值", data, x, y, height=87)

    # ─────────────────────────────────────────────────────────
    # 边转换（绕过被跳过的 input 节点）
    # ─────────────────────────────────────────────────────────

    def _convert_edges(self, flow: UIFFlow, skip_names: set) -> List[Dict]:
        """
        将 UIF 边列表转换为 Dify 边列表。
        遇到被跳过的 input 节点，自动重连其前驱和后继。
        """
        # 建立 input 节点的前驱→后继穿透映射
        bypass: Dict[str, List[Tuple[str, str]]] = {}  # input_name → [(pred, handle), ...]
        for e in flow.edges:
            if e.target in skip_names:
                bypass.setdefault(e.target, []).append((e.source, e.source_handle))

        dify_edges: List[Dict] = []
        processed = set()

        for e in flow.edges:
            # 源节点是被跳过的 input？→ 找到真正的前驱
            real_sources: List[Tuple[str, str]] = []  # [(name, handle)]
            if e.source in skip_names:
                real_sources = bypass.get(e.source, [])
            else:
                real_sources = [(e.source, e.source_handle)]

            # 目标节点是被跳过的 input？→ 跳过这条边（会由后续边处理）
            if e.target in skip_names:
                continue

            for src_name, src_handle in real_sources:
                if src_name in skip_names:
                    continue

                src_did  = self._name_to_dify_id.get(src_name)
                tgt_did  = self._name_to_dify_id.get(e.target)
                src_type = self._name_to_dtype.get(src_name, "")
                tgt_type = self._name_to_dtype.get(e.target, "")

                if not src_did or not tgt_did:
                    continue

                edge_key = (src_did, src_handle, tgt_did)
                if edge_key in processed:
                    continue
                processed.add(edge_key)

                # 确定 sourceHandle
                if src_handle in ("source", "default"):
                    dify_handle = "source"
                else:
                    dify_handle = src_handle  # condition/intent 分支 ID

                dify_edges.append(_make_edge(
                    src_id     = src_did,
                    src_handle = dify_handle,
                    tgt_id     = tgt_did,
                    src_type   = src_type,
                    tgt_type   = tgt_type,
                ))

        return dify_edges

    # ─────────────────────────────────────────────────────────
    # 组装完整 Dify 文档结构
    # ─────────────────────────────────────────────────────────

    def _build_document(self, flow: UIFFlow, nodes: List[Dict], edges: List[Dict]) -> Dict:
        return {
            "app": {
                "description":       "",
                "icon":              "🤖",
                "icon_background":   "#D1E9FF",
                "mode":              "advanced-chat",
                "name":              flow.name,
                "use_icon_as_answer_icon": False,
            },
            "kind":    "app",
            "version": "0.6.0",
            "workflow": {
                "conversation_variables": flow.conversation_variables,
                "environment_variables":  [],
                "features": {
                    "file_upload": {
                        "allowed_file_extensions":   [".JPG", ".JPEG", ".PNG", ".GIF", ".WEBP", ".SVG"],
                        "allowed_file_types":        ["image"],
                        "allowed_file_upload_methods":["local_file", "remote_url"],
                        "enabled":                   False,
                        "image":                     {"enabled": False, "number_limits": 3, "transfer_methods": ["local_file", "remote_url"]},
                        "number_limits":             3,
                    },
                    "opening_statement":          "",
                    "retriever_resource":         {"enabled": True},
                    "sensitive_word_avoidance":   {"enabled": False},
                    "speech_to_text":             {"enabled": False},
                    "suggested_questions":        [],
                    "suggested_questions_after_answer": {"enabled": False},
                    "text_to_speech":             {"enabled": False, "language": "", "voice": ""},
                },
                "graph": {
                    "edges": edges,
                    "nodes": nodes,
                    "viewport": {"x": 0, "y": 0, "zoom": 0.8},
                },
                "rag_pipeline_variables": [],
            },
        }

    # ─────────────────────────────────────────────────────────
    # 序列化为 YAML
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def to_yaml(dify_dict: Dict) -> str:
        """将 Dify dict 序列化为 YAML 字符串（与 Dify 导出格式兼容）"""
        return yaml_dumper.dumps(dify_dict)

    @staticmethod
    def save(dify_dict: Dict, output_path: str):
        """保存为 .yml 文件"""
        yml_str = DifyWriter.to_yaml(dify_dict)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(yml_str)
        return output_path
