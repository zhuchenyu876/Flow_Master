"""
重试创建失败的知识库

从 kb_per_intent_results_{lang}.json 中读取失败的记录，
为每个失败的 intent 重新创建知识库。

使用方法：
1. 修改下方的 API 凭证
2. 可选：修改 RETRY_SUFFIX 添加后缀（避免重名）
3. 运行：python retry_failed_intents.py
"""

import json
import os
import time
import pandas as pd
from openpyxl.styles import Font, PatternFill
from create_knowledge_base import KnowledgeBaseAPI

# ========================================
# 配置信息（从 create_kb_per_intent.py 复制）
# ========================================
# ROBOT_KEY = "klv8XlgViTqT%2F%2BEwcHdwsToe4l8%3D"
# ROBOT_TOKEN = "MTc2MjM0MzIxNjcxMQptOXpqMS9OclZBb2ZYWW5ybzJ2WVdwY0ZFaDQ9"
# USERNAME = "edison.chu@dyna.ai"
ROBOT_KEY= "lXvKMXVQ%2BebdDtLO0TuYl93oMTk%3D"
ROBOT_TOKEN= "MTc2NDIyMTUyMTQwNgp6bEs0dnB0WkQrdWZ3d1ZYN2RFQjQwMkgxMlE9"
USERNAME= "hsbc_migration@dyna.ai"


# 配置
LANGUAGE_CODE = "en"  # 要处理的语言
RESULTS_FILE = f"output/qa_knowledge_bases/kb_per_intent_results_{LANGUAGE_CODE}.json"
INTENTS_FILE = f"output/step1_processed/intents_{LANGUAGE_CODE}.json"

# 是否添加后缀避免重名（如果原名称已存在）
RETRY_WITH_SUFFIX = False  # 已修复embedding参数，不需要后缀
RETRY_SUFFIX = "_retry"  # 可以改为 "_v2" 或其他

# 是否自动确认（跳过交互式输入）
AUTO_CONFIRM = True  # 设为 True 自动开始重试，False 需要用户确认


def create_kb_for_intent(api, intent, lang_code, name_suffix=""):
    """为单个意图创建知识库并上传 Q&A"""
    
    intent_id = intent.get('id', '')
    display_name = intent.get('displayName', '')
    training_phrases = intent.get('trainingPhrases', [])
    
    if not display_name or not training_phrases:
        print(f"   ⚠️  跳过：意图无训练短语")
        return None, None
    
    # 知识库名称和描述的长度限制
    MAX_KB_NAME_LENGTH = 255  # 名称可以很长
    MAX_KB_DESC_LENGTH = 128  # API 实际限制是 128 字符！
    
    kb_name = f"{display_name}{name_suffix}"
    
    if len(kb_name) > MAX_KB_NAME_LENGTH:
        available_length = MAX_KB_NAME_LENGTH - len(name_suffix)
        if available_length > 0:
            kb_name = f"{display_name[:available_length]}{name_suffix}"
            print(f"   ⚠️  名称过长，已截断为 {MAX_KB_NAME_LENGTH} 字符")
            print(f"      原始: {display_name}")
            print(f"      截断: {kb_name}")
        else:
            kb_name = kb_name[:MAX_KB_NAME_LENGTH]
    
    # 知识库描述（关键：必须限制在128字符以内！）
    kb_description = f"Intent: {display_name} | ID: {intent_id} | Language: {lang_code}"
    if len(kb_description) > MAX_KB_DESC_LENGTH:
        kb_description = kb_description[:MAX_KB_DESC_LENGTH]
        print(f"   ⚠️  描述过长，已截断为 {MAX_KB_DESC_LENGTH} 字符")
    
    print(f"\n{'='*80}")
    print(f"📍 意图: {display_name}")
    print(f"   KB名称: {kb_name}")
    print(f"   训练短语数量: {len(training_phrases)}")
    print(f"{'='*80}")
    
    # 根据语言代码选择合适的 embedding 参数
    # 英文和中文都使用 bge-large-zh 模型（多语言模型）
    # 但 emb_language 要与内容语言匹配
    if lang_code == "en":
        emb_language = "en"
        emb_model = "bge-large-zh"  # 模型名称用中文的，但支持英文
    elif lang_code in ["zh-hant"]:
        # zh-hant (粤语/繁体中文) 使用独立的 'zh-hant'
        emb_language = "zh-hant"
        emb_model = "bge-large-zh"
    elif lang_code == "zh":
        emb_language = "zh"
        emb_model = "bge-large-zh"
    else:
        emb_language = "en"
        emb_model = "bge-large-zh"
    
    print(f"   Embedding: {emb_language} / {emb_model}")
    
    # 创建知识库
    result = api.create_knowledge_base(
        name=kb_name,
        description=kb_description,
        emb_language=emb_language,
        emb_model=emb_model
    )
    
    if result.get("code") != "000000":
        print(f"❌ 创建失败: {result.get('message')}")
        return None, None
    
    kb_id = str(result.get("data", {}).get("id"))
    print(f"✅ 知识库创建成功！ID: {kb_id}")
    
    # 准备 Q&A 数据并生成 Excel
    qa_pairs = []
    for phrase in training_phrases:
        if phrase and phrase.strip():
            qa_pairs.append({
                "question": phrase.strip(),
                "answer": display_name
            })
    
    if not qa_pairs:
        print(f"   ⚠️  无有效的训练短语，跳过上传")
        return kb_id, kb_name
    
    # 生成临时 Excel 文件
    temp_excel_dir = "output/qa_knowledge_bases/temp"
    os.makedirs(temp_excel_dir, exist_ok=True)
    safe_display_name = display_name.replace('/', '_').replace('\\', '_').replace(':', '_')
    temp_excel_file = os.path.join(temp_excel_dir, f"qa_{safe_display_name}_{lang_code}.xlsx")
    
    df = pd.DataFrame(qa_pairs)
    
    try:
        with pd.ExcelWriter(temp_excel_file, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Sheet1')
            workbook = writer.book
            worksheet = writer.sheets['Sheet1']
            
            # 设置标题行样式
            header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
            header_font = Font(color='FFFFFF', bold=True)
            
            for cell in worksheet[1]:
                cell.fill = header_fill
                cell.font = header_font
            
            # 调整列宽
            worksheet.column_dimensions['A'].width = 50
            worksheet.column_dimensions['B'].width = 50
        
        print(f"   📄 Excel 生成成功: {temp_excel_file}")
        
        # 上传 Q&A
        print(f"   📤 正在上传 Q&A 到知识库...")
        upload_result = api.import_qa_from_file(
            username=USERNAME,
            knowledge_base_id=kb_id,
            folder_id="root",
            file_path=temp_excel_file
        )
        
        if upload_result.get("code") == "000000":
            print(f"   ✅ Q&A 上传成功！")
        else:
            print(f"   ⚠️  Q&A 上传失败: {upload_result.get('message')}")
        
    except Exception as e:
        print(f"   ❌ Excel 生成或上传失败: {str(e)}")
    
    return kb_id, kb_name


def load_failed_intents():
    """读取失败的 intent 记录"""
    
    if not os.path.exists(RESULTS_FILE):
        print(f"❌ 结果文件不存在: {RESULTS_FILE}")
        return []
    
    with open(RESULTS_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    results = data.get("results", {})
    
    # 找出所有失败的记录
    failed_intents = []
    for intent_name, record in results.items():
        if record.get("status") in ["failed", "error"] or not record.get("kb_id"):
            failed_intents.append(record)
    
    return failed_intents


def load_intent_data(intent_id):
    """根据 intent_id 从原始文件中加载完整的 intent 数据"""
    
    if not os.path.exists(INTENTS_FILE):
        print(f"❌ Intent 文件不存在: {INTENTS_FILE}")
        return None
    
    with open(INTENTS_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 支持两种格式
    if isinstance(data, dict) and 'intents' in data:
        intents = data['intents']
    elif isinstance(data, list):
        intents = data
    else:
        return None
    
    # 查找匹配的 intent
    for intent in intents:
        if intent.get('id') == intent_id:
            return intent
    
    return None


def retry_failed_intents():
    """重试创建失败的知识库"""
    
    print("="*80)
    print("🔄 重试创建失败的知识库")
    print("="*80)
    print(f"语言: {LANGUAGE_CODE}")
    print(f"结果文件: {RESULTS_FILE}")
    print(f"Intent 文件: {INTENTS_FILE}")
    
    if RETRY_WITH_SUFFIX:
        print(f"⚠️  将添加后缀避免重名: {RETRY_SUFFIX}")
    
    print("="*80 + "\n")
    
    # 加载失败的记录
    failed_records = load_failed_intents()
    
    if not failed_records:
        print("✅ 没有失败的记录需要处理！")
        return
    
    print(f"📊 找到 {len(failed_records)} 个失败的记录\n")
    
    # 显示失败的 intent 名称
    print("失败的 Intent 列表：")
    for i, record in enumerate(failed_records, 1):
        display_name = record.get('display_name', 'Unknown')
        training_count = record.get('training_phrases_count', 0)
        print(f"  {i}. {display_name} ({training_count} 个训练短语)")
    
    print("\n" + "="*80)
    if AUTO_CONFIRM:
        print("⚙️  自动确认模式：开始重试...")
        proceed = 'y'
    else:
        proceed = input("是否继续重试？(y/n): ").strip().lower()
    
    if proceed != 'y':
        print("已取消。")
        return
    print("="*80 + "\n")
    
    # 创建 API 客户端
    api = KnowledgeBaseAPI(ROBOT_KEY, ROBOT_TOKEN, USERNAME)
    
    # 读取现有结果
    with open(RESULTS_FILE, 'r', encoding='utf-8') as f:
        results_data = json.load(f)
    
    results = results_data.get("results", {})
    
    # 重试每个失败的 intent
    success_count = 0
    failed_count = 0
    
    # 决定是否添加后缀
    name_suffix = RETRY_SUFFIX if RETRY_WITH_SUFFIX else ""
    
    for idx, failed_record in enumerate(failed_records, 1):
        intent_id = failed_record.get('intent_id')
        display_name = failed_record.get('display_name', 'Unknown')
        
        print(f"\n[{idx}/{len(failed_records)}] 重试: {display_name}")
        print(f"   Intent ID: {intent_id}")
        
        # 加载完整的 intent 数据
        intent = load_intent_data(intent_id)
        
        if not intent:
            print(f"   ❌ 未找到对应的 intent 数据")
            failed_count += 1
            continue
        
        try:
            # 重新创建知识库
            kb_id, kb_name_used = create_kb_for_intent(api, intent, LANGUAGE_CODE, name_suffix)
            
            if kb_id:
                # 更新结果
                results[display_name] = {
                    "intent_id": intent_id,
                    "display_name": display_name,
                    "kb_id": kb_id,
                    "kb_name": kb_name_used,
                    "name_truncated": len(display_name) > 255,
                    "status": "success",
                    "training_phrases_count": len(intent.get('trainingPhrases', [])),
                    "retry": True  # 标记为重试成功的
                }
                success_count += 1
                print(f"   ✅ 重试成功！KB ID: {kb_id}")
            else:
                print(f"   ❌ 重试仍然失败")
                failed_count += 1
            
            # 避免请求过快
            if kb_id and idx < len(failed_records):
                time.sleep(1)
            
        except Exception as e:
            print(f"   ❌ 重试出错: {str(e)}")
            failed_count += 1
    
    # 保存更新后的结果
    results_data["results"] = results
    results_data["success_count"] = sum(1 for r in results.values() if r.get("status") == "success")
    results_data["failed_count"] = sum(1 for r in results.values() if r.get("status") in ["failed", "error"])
    
    with open(RESULTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(results_data, f, indent=2, ensure_ascii=False)
    
    # 显示结果
    print("\n" + "="*80)
    print("📊 重试结果汇总")
    print("="*80)
    print(f"总共重试: {len(failed_records)} 个")
    print(f"✅ 成功: {success_count} 个")
    print(f"❌ 失败: {failed_count} 个")
    print(f"\n💾 结果已更新到: {RESULTS_FILE}")
    print("="*80)


if __name__ == "__main__":
    retry_failed_intents()

