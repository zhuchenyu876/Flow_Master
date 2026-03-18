# -*- coding: utf-8 -*-
"""
轻量 YAML 序列化器（无需 PyYAML）
===================================
专为 Dify chatflow 格式设计，覆盖所有必要类型：
  - 字符串（含自动引号：纯数字串、含特殊字符、空串）
  - 整数 / 浮点
  - 布尔 (true/false)
  - None → null
  - 列表（块风格 - item）
  - 字典（块风格 key: value）

重要：Dify 节点 ID 是纯数字字符串（如 '1737212530035'），
必须加引号，否则 YAML 解析器会当成整数处理。
"""

import re


# 不需要引号的 "安全" 字符串模式（无空格、无特殊符号、不以数字开头）
_SAFE_STR = re.compile(r'^[a-zA-Z_\-/][a-zA-Z0-9_\-/.:]*$')
# YAML 保留布尔字面量，必须引用
_YAML_RESERVED = {"true", "false", "null", "yes", "no", "on", "off",
                  "True", "False", "Null", "YES", "NO", "ON", "OFF"}


def _needs_quotes(s: str) -> bool:
    """判断字符串是否需要加引号"""
    if s == "":
        return True
    if s in _YAML_RESERVED:
        return True
    # 全数字（含负号/小数/科学计数）→ 必须引用，否则被解析为数值
    try:
        float(s)
        return True
    except ValueError:
        pass
    # 含特殊字符
    if any(c in s for c in ':#{}[]|>&*!,?@`\'"\\%\n\r\t'):
        return True
    # 以空格开头/结尾
    if s != s.strip():
        return True
    return False


def _escape_str(s: str) -> str:
    """转义双引号内的特殊字符"""
    s = s.replace("\\", "\\\\")
    s = s.replace('"',  '\\"')
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "\\r")
    s = s.replace("\t", "\\t")
    return s


def _dump_value(value, indent: int, prefix: str = "") -> str:
    """
    将 Python 值序列化为 YAML 字符串块。

    indent : 当前缩进级别（单位：2 空格）
    prefix : 若非空，则在同一行输出（用于 dict value 或 list item 的内联头）
    """
    pad = "  " * indent

    if value is None:
        return "null"

    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        return str(value)

    if isinstance(value, str):
        if _needs_quotes(value):
            return '"' + _escape_str(value) + '"'
        return value

    if isinstance(value, list):
        if not value:
            return "[]"
        lines = []
        for item in value:
            serialized = _dump_value(item, indent + 1)
            if serialized.startswith("\n"):
                # 多行块值
                lines.append(pad + "- " + serialized.lstrip())
            elif "\n" in serialized:
                first, *rest = serialized.split("\n")
                lines.append(pad + "- " + first)
                for r in rest:
                    lines.append(pad + "  " + r)
            else:
                lines.append(pad + "- " + serialized)
        return "\n" + "\n".join(lines)

    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = []
        for k, v in value.items():
            k_str = str(k)
            if _needs_quotes(k_str):
                k_str = '"' + _escape_str(k_str) + '"'
            v_serialized = _dump_value(v, indent + 1)
            if v_serialized.startswith("\n"):
                lines.append(pad + k_str + ":" + v_serialized)
            else:
                lines.append(pad + k_str + ": " + v_serialized)
        return "\n" + "\n".join(lines)

    # fallback
    return str(value)


def dumps(data) -> str:
    """
    序列化顶层对象（必须是 dict 或 list）为 YAML 字符串。
    """
    result = _dump_value(data, indent=0)
    # 顶层以换行开头时去掉前导换行
    if result.startswith("\n"):
        result = result[1:]
    if not result.endswith("\n"):
        result += "\n"
    return result


def dump(data, file_obj):
    """序列化并写入文件对象"""
    file_obj.write(dumps(data))
