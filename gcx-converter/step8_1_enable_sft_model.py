# writed by senlin.deng 2026-01-28
# 启用SFT模型配置
import argparse
import json
from pathlib import Path
from typing import Dict, Any, List, Tuple


def load_sft_config(config_path: Path) -> Dict[str, str]:
    with config_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    # write by senlin.deng 2026-01-30
    # 将sft_model_config.json中的label和value映射到name_to_label中，保持大写，匹配使用小写
    # name_to_label的key为name的lower，value为{'label': label, 'value': name}
    name_to_label: Dict[str, Dict[str, str]] = {}
    for label, name in raw.items():
        if not isinstance(name, str):
            continue
        if name in name_to_label:
            # 保留第一个映射，避免覆盖
            continue
        name_to_label[name.lower()] = {'label': str(name), 'value': str(label)}
    # print(f"sft_model_config.json loaded: {name_to_label}")
    # exit()
    return name_to_label


def find_intention_list(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(data.get("intention_list"), list):
        return data["intention_list"]
    planning = data.get("planning", {})
    resource = planning.get("resource", {})
    if isinstance(resource.get("intention_list"), list):
        return resource["intention_list"]
    return []

# 更新不同意图跳转下面的配置
def update_intentions(
    intentions: List[Dict[str, Any]],
    name_to_label: Dict[str, str],
    model_name: str,
) -> Tuple[int, int]:
    updated = 0
    missing = 0
    for item in intentions:
        if not isinstance(item, dict):
            continue
        intent_name = item.get("intention_name")
        if not intent_name:
            continue
        intent_key = intent_name.lower() if isinstance(intent_name, str) else intent_name
        value = name_to_label.get(intent_key, {}).get('value')
        label = name_to_label.get(intent_key, {}).get('label')
        # 开关全局意图中各意图的vector开关
        item["embedding_enable"] = True
        if label is None or value is None:
            item["sft_model_enable"] = False
            item["sft_model_name"] = ""
            item["sft_model_reponse_structure"] = "{}"
            missing += 1
            continue
        response_structure = {
            "label": label,
            "value": value,
        }
        item["sft_model_enable"] = True
        item["sft_model_name"] = model_name
        item["sft_model_reponse_structure"] = json.dumps(
            response_structure, ensure_ascii=False
        )
        updated += 1
    return updated, missing


def process_file(
    file_path: Path, name_to_label: Dict[str, str], model_name: str
) -> Tuple[int, int, bool]:
    with file_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    intentions = find_intention_list(data)
    if not intentions:
        return 0, 0, False

    updated, missing = update_intentions(intentions, name_to_label, model_name)
    with file_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.write("\n")
    return updated, missing, True


def collect_target_files(input_path: Path) -> List[Path]:
    if input_path.is_file():
        return [input_path]
    if not input_path.exists():
        return []
    targets: List[Path] = []
    for step8_dir in input_path.rglob("step8_final"):
        if not step8_dir.is_dir():
            continue
        targets.extend(sorted(step8_dir.glob("*.json")))
    return targets
