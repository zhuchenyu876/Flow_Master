"""
验证和修复 step7 输出的 JSON 文件
"""
import json
import os
import sys
from pathlib import Path

def validate_and_fix_json_files(directory):
    """验证并尝试修复 JSON 文件"""
    if not os.path.exists(directory):
        print(f"❌ 目录不存在: {directory}")
        return False
    
    files = [f for f in os.listdir(directory) if f.endswith('.json')]
    print(f"📁 检查目录: {directory}")
    print(f"📊 找到 {len(files)} 个 JSON 文件\n")
    
    invalid_files = []
    fixed_files = []
    
    for filename in sorted(files):
        filepath = os.path.join(directory, filename)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                json.load(f)
            print(f"✅ {filename}")
        except json.JSONDecodeError as e:
            print(f"❌ {filename}")
            print(f"   错误: {e.msg}")
            print(f"   位置: 第 {e.lineno} 行, 第 {e.colno} 列")
            
            # 尝试修复
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # 尝试常见修复方法
                # 1. 移除尾部多余逗号
                fixed_content = content
                
                # 2. 尝试重新格式化
                try:
                    # 尝试使用宽松模式解析
                    import re
                    # 移除注释
                    fixed_content = re.sub(r'//.*?\n', '\n', fixed_content)
                    # 移除尾部逗号
                    fixed_content = re.sub(r',(\s*[}\]])', r'\1', fixed_content)
                    
                    data = json.loads(fixed_content)
                    
                    # 重新写入
                    backup_path = filepath + '.backup'
                    os.rename(filepath, backup_path)
                    
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    
                    print(f"   ✅ 已修复并保存，备份到: {os.path.basename(backup_path)}")
                    fixed_files.append(filename)
                except:
                    print(f"   ⚠️  无法自动修复，需要手动检查")
                    invalid_files.append((filename, str(e)))
            except Exception as ex:
                print(f"   ⚠️  修复失败: {ex}")
                invalid_files.append((filename, str(e)))
        except Exception as e:
            print(f"⚠️  {filename}")
            print(f"   错误: {e}")
            invalid_files.append((filename, str(e)))
    
    print(f"\n" + "="*70)
    print(f"📊 检查完成:")
    print(f"   ✅ 正常文件: {len(files) - len(invalid_files)} 个")
    if fixed_files:
        print(f"   🔧 已修复: {len(fixed_files)} 个")
    if invalid_files:
        print(f"   ❌ 仍有错误: {len(invalid_files)} 个")
        for filename, error in invalid_files:
            print(f"      - {filename}")
        return False
    else:
        print(f"\n✅ 所有文件检查通过!")
        return True

if __name__ == "__main__":
    if len(sys.argv) > 1:
        directory = sys.argv[1]
    else:
        print("用法: python validate_and_fix_json.py <目录路径>")
        print("例如: python validate_and_fix_json.py output/xxx/step7_final/zh-hant")
        sys.exit(1)
    
    success = validate_and_fix_json_files(directory)
    sys.exit(0 if success else 1)

