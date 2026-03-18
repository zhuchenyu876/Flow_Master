"""
改进的知识库名称构建函数
======================
解决截断导致的命名冲突问题，添加 hash 保证唯一性

使用方法：
1. 复制 build_kb_name_with_hash() 函数到 step3_kb_creator.py
2. 将原来的 build_kb_name() 替换为 build_kb_name_with_hash()
3. 或者保留两个版本，通过环境变量控制使用哪个
"""

import hashlib
import os

# 配置
MAX_KB_NAME_LENGTH = int(os.getenv("STEP_3_MAX_KB_NAME_LENGTH", "128"))
KB_NAME_PREFIX = ""
KB_NAME_SUFFIX = ""


def _sanitize_suffix_component(value: str) -> str:
    """将名称片段转换为仅包含字母、数字、下划线和连字符的安全字符串"""
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value or "")


def build_kb_name_with_hash(display_name: str, lang_code: str, source_file_tag: str = ""):
    """
    构造知识库名称，包含：
    - 前缀/后缀
    - 语言后缀
    - 输入文件名后缀（防重名）
    - 如果需要截断，添加 hash 保证唯一性
    
    同时保证总长度不超过 MAX_KB_NAME_LENGTH。
    
    Args:
        display_name: 意图的 displayName
        lang_code: 语言代码（如 "en", "zh"）
        source_file_tag: 源文件标签（用于避免重名）
    
    Returns:
        (kb_name, name_truncated)
        
    示例：
        输入：display_name="VeryLongIntentName...", lang_code="en", source_file_tag="FileA"
        输出：("VeryLongIntentName_en_FileA_a1b2c3d4", True)
                                               ^^^^^^^^ 8位hash保证唯一性
    """
    lang_suffix = f"_{lang_code}" if lang_code else ""
    safe_file_tag = _sanitize_suffix_component(source_file_tag)
    file_suffix = f"_{safe_file_tag}" if safe_file_tag else ""
    
    # 构造完整的名称（未截断）
    full_name = f"{KB_NAME_PREFIX}{display_name}{KB_NAME_SUFFIX}{lang_suffix}{file_suffix}"
    
    # 如果不需要截断，直接返回
    if len(full_name) <= MAX_KB_NAME_LENGTH:
        return full_name, False
    
    # 需要截断，添加 hash 保证唯一性
    # Hash 基于完整名称（包括所有组成部分）
    full_name_for_hash = f"{display_name}|{lang_code}|{source_file_tag}"
    name_hash = hashlib.md5(full_name_for_hash.encode('utf-8')).hexdigest()[:8]
    
    # Hash 部分：_xxxxxxxx (9个字符)
    HASH_SUFFIX = f"_{name_hash}"
    HASH_LENGTH = len(HASH_SUFFIX)
    
    # 固定部分长度
    fixed_length = len(KB_NAME_PREFIX) + len(KB_NAME_SUFFIX) + len(lang_suffix) + HASH_LENGTH
    
    # 剩余可用长度
    remaining = MAX_KB_NAME_LENGTH - fixed_length
    
    if remaining < 20:
        # 空间不够，优先保证 display_name 和 hash
        # 牺牲 file_suffix
        remaining = MAX_KB_NAME_LENGTH - len(KB_NAME_PREFIX) - len(KB_NAME_SUFFIX) - len(lang_suffix) - HASH_LENGTH
        file_suffix = ""
        
        if remaining < 10:
            # 还不够，说明前缀后缀太长了
            # 只保留 display_name 的前几个字符 + hash
            remaining = MAX_KB_NAME_LENGTH - HASH_LENGTH - len(lang_suffix)
            trimmed_display = display_name[:max(10, remaining)]
            kb_name = f"{trimmed_display}{lang_suffix}{HASH_SUFFIX}"
            return kb_name[:MAX_KB_NAME_LENGTH], True
    
    # 为 display_name 和 file_suffix 分配空间
    # 优先保证 display_name，file_suffix 可以被截断或移除
    if len(file_suffix) > remaining // 3:
        # file_suffix 太长，截断或移除
        max_file_suffix_len = remaining // 4
        if max_file_suffix_len < 10:
            file_suffix = ""  # 移除
        else:
            file_suffix = file_suffix[:max_file_suffix_len]
    
    # 重新计算剩余空间
    remaining = MAX_KB_NAME_LENGTH - len(KB_NAME_PREFIX) - len(KB_NAME_SUFFIX) - len(lang_suffix) - len(file_suffix) - HASH_LENGTH
    
    # 截断 display_name
    trimmed_display = display_name[:remaining]
    
    # 构造最终名称
    kb_name = f"{KB_NAME_PREFIX}{trimmed_display}{KB_NAME_SUFFIX}{lang_suffix}{file_suffix}{HASH_SUFFIX}"
    
    # 最终保险：确保不超出上限
    if len(kb_name) > MAX_KB_NAME_LENGTH:
        kb_name = kb_name[:MAX_KB_NAME_LENGTH]
    
    return kb_name, True


def build_kb_name_original(display_name: str, lang_code: str, source_file_tag: str = ""):
    """
    原始的知识库名称构建函数（保留用于对比）
    """
    lang_suffix = f"_{lang_code}" if lang_code else ""
    safe_file_tag = _sanitize_suffix_component(source_file_tag)
    file_suffix = f"_{safe_file_tag}" if safe_file_tag else ""

    fixed_length = len(KB_NAME_PREFIX) + len(KB_NAME_SUFFIX) + len(lang_suffix)
    name_truncated = False

    remaining_for_dynamic = MAX_KB_NAME_LENGTH - fixed_length
    if remaining_for_dynamic < 0:
        name_truncated = True
        kb_name = (KB_NAME_PREFIX + KB_NAME_SUFFIX + lang_suffix)[:MAX_KB_NAME_LENGTH]
        return kb_name, name_truncated

    if len(file_suffix) > remaining_for_dynamic:
        file_suffix = file_suffix[:remaining_for_dynamic]
        name_truncated = True

    remaining_for_display = MAX_KB_NAME_LENGTH - fixed_length - len(file_suffix)
    if remaining_for_display < 0:
        remaining_for_display = 0
        name_truncated = True

    trimmed_display = display_name[:remaining_for_display]
    if len(trimmed_display) < len(display_name):
        name_truncated = True

    kb_name = f"{KB_NAME_PREFIX}{trimmed_display}{KB_NAME_SUFFIX}{lang_suffix}{file_suffix}"

    if len(kb_name) > MAX_KB_NAME_LENGTH:
        kb_name = kb_name[:MAX_KB_NAME_LENGTH]
        name_truncated = True

    return kb_name, name_truncated


# ============================================================
# 测试和对比
# ============================================================

def test_kb_name_builders():
    """测试和对比两个版本的命名函数"""
    
    print("=" * 80)
    print("知识库名称构建函数测试")
    print("=" * 80)
    
    test_cases = [
        {
            "display_name": "ShortName",
            "lang_code": "en",
            "source_file_tag": "TestFile",
            "description": "正常长度名称"
        },
        {
            "display_name": "CardServicing_CheckCardDeliveryStatus_AfterReplacingCard_WithPhysicalCard_ATMCard",
            "lang_code": "en",
            "source_file_tag": "CardServicing_Fulfillment",
            "description": "需要截断的长名称"
        },
        {
            "display_name": "CardServicing_CheckCardDeliveryStatus_AfterReplacingCard_WithPhysicalCard_ATMCard_CheckMailingAddress",
            "lang_code": "en",
            "source_file_tag": "CardServicing_Fulfillment",
            "description": "极长名称 - 场景1"
        },
        {
            "display_name": "CardServicing_CheckCardDeliveryStatus_AfterReplacingCard_WithPhysicalCard_DebitCard_CheckMailingAddress",
            "lang_code": "en",
            "source_file_tag": "CardServicing_Fulfillment",
            "description": "极长名称 - 场景2（与场景1相似，测试hash唯一性）"
        },
        {
            "display_name": "TransactionServicing_CheckStatusOfDispute",
            "lang_code": "zh",
            "source_file_tag": "AccountServicing_FAQ",
            "description": "中文语言代码"
        }
    ]
    
    for i, case in enumerate(test_cases, 1):
        print(f"\n{'='*80}")
        print(f"测试 {i}: {case['description']}")
        print(f"{'='*80}")
        print(f"输入:")
        print(f"  display_name: {case['display_name']}")
        print(f"  lang_code: {case['lang_code']}")
        print(f"  source_file_tag: {case['source_file_tag']}")
        
        # 原始版本
        kb_name_orig, truncated_orig = build_kb_name_original(
            case['display_name'], 
            case['lang_code'], 
            case['source_file_tag']
        )
        
        # 改进版本
        kb_name_new, truncated_new = build_kb_name_with_hash(
            case['display_name'], 
            case['lang_code'], 
            case['source_file_tag']
        )
        
        print(f"\n原始版本:")
        print(f"  结果: {kb_name_orig}")
        print(f"  长度: {len(kb_name_orig)}")
        print(f"  截断: {truncated_orig}")
        
        print(f"\n改进版本 (带Hash):")
        print(f"  结果: {kb_name_new}")
        print(f"  长度: {len(kb_name_new)}")
        print(f"  截断: {truncated_new}")
        
        if truncated_new:
            # 显示 hash 部分
            hash_part = kb_name_new[-8:]
            print(f"  Hash: {hash_part} (保证唯一性)")
    
    print(f"\n{'='*80}")
    print("测试完成")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    test_kb_name_builders()

