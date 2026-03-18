# -*- coding: utf-8 -*-
"""
convert_to_dify.py — Google Dialogflow CX → Dify Chatflow 转换入口
====================================================================

完整流程：
  Google CX exported_flow.json
        ↓  [step0~step2]  已有流程
  nodes_config_*.json + edge_config_*.json
        ↓  [Reader]  readers/nodes_config_reader.py
  UIFFlow（通用中间格式）
        ↓  [Writer]  writers/dify/writer.py
  Dify chatflow .yml

用法（命令行）：
  python convert_to_dify.py \\
      --nodes output/step2_workflow_config/en/nodes_config_CardFlow.json \\
      --edges output/step2_workflow_config/en/edge_config_CardFlow.json  \\
      --output output/dify/CardFlow.yml                                  \\
      --language en

用法（Python API）：
  from convert_to_dify import convert_flow_to_dify
  yml_path = convert_flow_to_dify(
      nodes_file = "...",
      edges_file = "...",
      output_file = "output/dify/MyFlow.yml",
      language    = "en",
      model_name  = "gpt-4o-mini",
  )

批量转换（整个 step2 输出目录下所有 flow）：
  python convert_to_dify.py \\
      --batch output/step2_workflow_config/en \\
      --output-dir output/dify/en
"""

import os
import sys
import glob
import argparse
from typing import Optional

# 确保根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Windows 终端编码兼容
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from readers.nodes_config_reader import NodesConfigReader
from writers.dify.writer         import DifyWriter


def convert_flow_to_dify(
    nodes_file:  str,
    edges_file:  str,
    output_file: str,
    flow_name:   str  = "",
    language:    str  = "en",
    model_name:  str  = "gpt-4o-mini",
    model_provider: str = "openai",
    verbose:     bool = True,
) -> str:
    """
    单个 flow 转换：nodes_config + edge_config → Dify YAML。

    Returns:
        生成的 .yml 文件路径
    """
    if verbose:
        print(f"\n{'='*60}")
        print(f"  Google CX → Dify 转换")
        print(f"{'='*60}")
        print(f"  nodes_config : {nodes_file}")
        print(f"  edge_config  : {edges_file}")
        print(f"  language     : {language}")
        print(f"  模型         : {model_name} ({model_provider})")

    # ── 1. Reader: nodes_config → UIF ────────────────────────
    if verbose:
        print(f"\n[1/3] 读取 nodes_config / edge_config → UIF...")

    flow = NodesConfigReader.read(
        nodes_file = nodes_file,
        edges_file = edges_file,
        flow_name  = flow_name,
        language   = language,
    )

    if verbose:
        n_by_type = {}
        for n in flow.nodes:
            n_by_type[n.type] = n_by_type.get(n.type, 0) + 1
        print(f"  ✅ 读取完成：{len(flow.nodes)} 个节点，{len(flow.edges)} 条边")
        for t, cnt in sorted(n_by_type.items()):
            print(f"     {t:20s}: {cnt}")

    # ── 2. Writer: UIF → Dify YAML ────────────────────────────
    if verbose:
        print(f"\n[2/3] 转换 UIF → Dify chatflow...")

    model_config = {
        "completion_params": {"temperature": 0.7},
        "mode":              "chat",
        "name":              model_name,
        "provider":          model_provider,
    }

    writer     = DifyWriter()
    dify_dict  = writer.write(flow, model_config=model_config)

    # ── 3. 保存 YAML ─────────────────────────────────────────
    if verbose:
        print(f"\n[3/3] 保存 Dify YAML...")

    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    writer.save(dify_dict, output_file)

    if verbose:
        size_kb = os.path.getsize(output_file) / 1024
        print(f"  ✅ 已保存: {output_file}  ({size_kb:.1f} KB)")
        print(f"\n💡 导入方式：Dify → 工作室 → 从文件导入 → 选择此 .yml 文件")
        print(f"{'='*60}\n")

    return output_file


def batch_convert(
    step2_dir:    str,
    output_dir:   str,
    language:     str  = "en",
    model_name:   str  = "gpt-4o-mini",
    model_provider: str = "openai",
    verbose:      bool = True,
) -> list:
    """
    批量转换 step2 目录下的所有 flow。

    Args:
        step2_dir  : step2 输出目录（包含 nodes_config_*.json）
        output_dir : Dify YAML 输出目录
        language   : 语言代码
    Returns:
        生成的文件路径列表
    """
    nodes_files = glob.glob(os.path.join(step2_dir, "nodes_config_*.json"))
    if not nodes_files:
        print(f"⚠️  在 {step2_dir} 中未找到 nodes_config_*.json 文件")
        return []

    print(f"\n{'='*60}")
    print(f"  批量转换：{len(nodes_files)} 个 flow")
    print(f"  输入目录 : {step2_dir}")
    print(f"  输出目录 : {output_dir}")
    print(f"{'='*60}")

    generated = []
    for nodes_file in sorted(nodes_files):
        flow_name = os.path.basename(nodes_file) \
            .replace("nodes_config_", "") \
            .replace(".json", "")
        edges_file = os.path.join(step2_dir, f"edge_config_{flow_name}.json")

        if not os.path.exists(edges_file):
            print(f"  ⚠️  跳过 {flow_name}：找不到对应的 edge_config 文件")
            continue

        output_file = os.path.join(output_dir, f"{flow_name}.yml")
        try:
            result = convert_flow_to_dify(
                nodes_file  = nodes_file,
                edges_file  = edges_file,
                output_file = output_file,
                flow_name   = flow_name,
                language    = language,
                model_name  = model_name,
                model_provider = model_provider,
                verbose     = verbose,
            )
            generated.append(result)
        except Exception as e:
            print(f"  ❌ {flow_name} 转换失败: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n✅ 批量转换完成：成功 {len(generated)}/{len(nodes_files)} 个")
    return generated


# ──────────────────────────────────────────────
# 命令行入口
# ──────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Google Dialogflow CX → Dify Chatflow 转换工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    # 单文件模式
    parser.add_argument("--nodes", "-n", type=str, help="nodes_config_*.json 文件路径")
    parser.add_argument("--edges", "-e", type=str, help="edge_config_*.json 文件路径")
    parser.add_argument("--output", "-o", type=str, default="output/dify/converted.yml",
                        help="输出 .yml 文件路径")
    # 批量模式
    parser.add_argument("--batch", "-b", type=str, help="step2 输出目录（批量模式）")
    parser.add_argument("--output-dir", type=str, default="output/dify",
                        help="批量模式输出目录")
    # 公共参数
    parser.add_argument("--language", "-l", type=str, default="en",
                        help="语言代码（en / zh / zh-hant）")
    parser.add_argument("--model", "-m", type=str, default="gpt-4o-mini",
                        help="LLM 模型名（默认 gpt-4o-mini）")
    parser.add_argument("--provider", "-p", type=str, default="openai",
                        help="模型提供商（默认 openai）")
    parser.add_argument("--flow-name", type=str, default="",
                        help="flow 名称（可选，默认从文件名推断）")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="静默模式")

    args = parser.parse_args()

    if args.batch:
        # 批量模式
        batch_convert(
            step2_dir    = args.batch,
            output_dir   = args.output_dir,
            language     = args.language,
            model_name   = args.model,
            model_provider = args.provider,
            verbose      = not args.quiet,
        )
    elif args.nodes and args.edges:
        # 单文件模式
        convert_flow_to_dify(
            nodes_file  = args.nodes,
            edges_file  = args.edges,
            output_file = args.output,
            flow_name   = args.flow_name,
            language    = args.language,
            model_name  = args.model,
            model_provider = args.provider,
            verbose     = not args.quiet,
        )
    else:
        parser.print_help()
        print("\n❌ 请提供 --nodes + --edges（单文件）或 --batch（批量）参数")
        sys.exit(1)
