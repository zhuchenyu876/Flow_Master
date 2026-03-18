"""
Step 7: 工作流布局优化
使用 dagre 库（通过 Node.js）来美化生成的 workflow JSON 文件的节点位置

支持两种布局方向：
- LR (Left to Right): 从左到右
- TB (Top to Bottom): 从上到下

使用 Node.js 调用 dagre 库，与原始 JavaScript 代码逻辑完全一致
"""

import json
import os
import sys
import subprocess
from typing import Dict, List, Any


def check_nodejs_available() -> bool:
    """检查 Node.js 是否可用"""
    try:
        result = subprocess.run(['node', '--version'], 
                              capture_output=True, 
                              text=True, 
                              encoding='utf-8',
                              errors='replace',
                              timeout=5)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# 模块级缓存，避免重复检查
_dagre_checked = False
_dagre_installed = None

def check_dagre_installed() -> bool:
    """检查 dagre 是否已安装（检查脚本目录下的 node_modules）"""
    global _dagre_checked, _dagre_installed
    
    # 如果已经检查过，直接返回缓存结果
    if _dagre_checked:
        return _dagre_installed
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    node_modules_dir = os.path.join(script_dir, 'node_modules')
    
    # 首先检查 node_modules 目录是否存在
    dagre_dir = os.path.join(node_modules_dir, 'dagre')
    dagrejs_dir = os.path.join(node_modules_dir, '@dagrejs', 'dagre')
    
    if os.path.exists(dagre_dir) or os.path.exists(dagrejs_dir):
        _dagre_checked = True
        _dagre_installed = True
        return True
    
    # 如果 node_modules 目录不存在，尝试通过 require 检查（可能是全局安装）
    try:
        # 尝试 @dagrejs/dagre
        result = subprocess.run(['node', '-e', "require('@dagrejs/dagre')"], 
                              capture_output=True, 
                              text=True, 
                              encoding='utf-8',
                              errors='replace',
                              timeout=5,
                              cwd=script_dir)
        if result.returncode == 0:
            _dagre_checked = True
            _dagre_installed = True
            return True
    except:
        pass
    
    try:
        # 尝试 dagre
        result = subprocess.run(['node', '-e', "require('dagre')"], 
                              capture_output=True, 
                              text=True, 
                              encoding='utf-8',
                              errors='replace',
                              timeout=5,
                              cwd=script_dir)
        _dagre_checked = True
        _dagre_installed = (result.returncode == 0)
        return _dagre_installed
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _dagre_checked = True
        _dagre_installed = False
        return False


def install_dagre():
    """安装 dagre 库"""
    global _dagre_checked, _dagre_installed
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    node_modules_dir = os.path.join(script_dir, 'node_modules')
    dagre_dir = os.path.join(node_modules_dir, 'dagre')
    
    # 再次检查是否已安装（可能在检查后、安装前被其他进程安装了）
    if os.path.exists(dagre_dir):
        _dagre_checked = True
        _dagre_installed = True
        print('  ✅ Dagre already installed')
        return True
    
    print('  📦 Installing dagre...')
    
    # 尝试使用 cmd 而不是 PowerShell
    import platform
    if platform.system() == 'Windows':
        # 在 Windows 上，尝试使用 cmd.exe 来运行 npm
        try:
            # 尝试直接使用 npm.cmd
            result = subprocess.run(['npm.cmd', 'install', 'dagre'], 
                                  capture_output=True, 
                                  text=True, 
                                  encoding='utf-8',
                                  errors='replace',
                                  timeout=60,
                                  cwd=script_dir,
                                  shell=True)
            if result.returncode == 0:
                # 验证安装是否成功
                if os.path.exists(dagre_dir):
                    _dagre_checked = True
                    _dagre_installed = True
                    print('  ✅ Dagre installed successfully')
                    return True
        except FileNotFoundError:
            pass
        
        # 如果 npm.cmd 失败，尝试使用 cmd /c npm
        try:
            result = subprocess.run(['cmd', '/c', 'npm', 'install', 'dagre'], 
                                  capture_output=True, 
                                  text=True, 
                                  encoding='utf-8',
                                  errors='replace',
                                  timeout=60,
                                  cwd=script_dir,
                                  shell=True)
            if result.returncode == 0:
                # 验证安装是否成功
                if os.path.exists(dagre_dir):
                    _dagre_checked = True
                    _dagre_installed = True
                    print('  ✅ Dagre installed successfully')
                    return True
        except FileNotFoundError:
            pass
    
    # 最后尝试直接使用 npm
    try:
        result = subprocess.run(['npm', 'install', 'dagre'], 
                              capture_output=True, 
                              text=True, 
                              encoding='utf-8',
                              errors='replace',
                              timeout=60,
                              cwd=script_dir,
                              shell=True)
        if result.returncode == 0:
            # 验证安装是否成功
            if os.path.exists(dagre_dir):
                _dagre_checked = True
                _dagre_installed = True
                print('  ✅ Dagre installed successfully')
                return True
            else:
                print('  ⚠️  npm install completed but dagre directory not found')
                return False
        else:
            print(f'  ❌ Failed to install dagre: {result.stderr}')
            print('  💡 Please run manually: npm install dagre')
            _dagre_checked = True
            _dagre_installed = False
            return False
    except FileNotFoundError:
        print('  ❌ Error: npm not found. Please install Node.js and npm first.')
        print('  💡 Please run manually: npm install dagre')
        _dagre_checked = True
        _dagre_installed = False
        return False
    except subprocess.TimeoutExpired:
        print('  ❌ Error: npm install timeout')
        _dagre_checked = True
        _dagre_installed = False
        return False


def optimize_workflow_layout(
    input_file: str,
    output_file: str = None,
    direction: str = 'LR'
) -> str:
    """
    优化工作流布局（使用 Node.js 调用 dagre）
    
    Args:
        input_file: 输入的 workflow JSON 文件路径
        output_file: 输出的 workflow JSON 文件路径（如果为 None，则覆盖原文件）
        direction: 布局方向，'LR' (从左到右) 或 'TB' (从上到下)
        
    Returns:
        输出文件路径
    """
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"Input file not found: {input_file}")
    
    # 检查 Node.js
    if not check_nodejs_available():
        raise RuntimeError(
            "Node.js is not available. Please install Node.js first.\n"
            "Download from: https://nodejs.org/"
        )
    
    # 检查并安装 dagre
    if not check_dagre_installed():
        if not install_dagre():
            raise RuntimeError("Failed to install @dagrejs/dagre")
    
    # 简化输出 - 由 JS 脚本输出单行结果
    
    # 获取脚本目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    js_script = os.path.join(script_dir, 'step7_layout_optimizer_dagre.js')
    
    if not os.path.exists(js_script):
        raise FileNotFoundError(f"JavaScript script not found: {js_script}")
    
    # 转换为绝对路径（解决相对路径问题）
    input_file = os.path.abspath(input_file)
    if output_file is None:
        output_file = input_file
    else:
        output_file = os.path.abspath(output_file)
    
    cmd = [
        'node',
        js_script,
        '--file', input_file,
        '--output', output_file,
        '--direction', direction
    ]
    
    # 执行 Node.js 脚本
    try:
        result = subprocess.run(cmd, 
                              capture_output=True, 
                              text=True, 
                              encoding='utf-8',
                              errors='replace',
                              timeout=60,
                              cwd=script_dir)
        
        # 打印输出
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        
        if result.returncode != 0:
            raise RuntimeError(f"Node.js script failed with exit code {result.returncode}")
        
    except subprocess.TimeoutExpired:
        raise RuntimeError("Node.js script execution timeout")
    except Exception as e:
        raise RuntimeError(f"Error executing Node.js script: {str(e)}")
    
    return output_file


def process_all_workflows(
    input_dir: str = 'output/step6_final',
    output_dir: str = None,
    direction: str = 'LR'
):
    """
    处理目录中的所有工作流文件
    
    Args:
        input_dir: 输入目录路径
        output_dir: 输出目录路径（如果为 None，则覆盖原文件）
        direction: 布局方向，'LR' (从左到右) 或 'TB' (从上到下)
    """
    # 检查 Node.js
    if not check_nodejs_available():
        raise RuntimeError(
            "Node.js is not available. Please install Node.js first.\n"
            "Download from: https://nodejs.org/"
        )
    
    # 检查并安装 dagre
    if not check_dagre_installed():
        if not install_dagre():
            raise RuntimeError("Failed to install @dagrejs/dagre")
    
    # 获取脚本目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    js_script = os.path.join(script_dir, 'step7_layout_optimizer_dagre.js')
    
    if not os.path.exists(js_script):
        raise FileNotFoundError(f"JavaScript script not found: {js_script}")
    
    # 转换为绝对路径（解决相对路径问题）
    input_dir = os.path.abspath(input_dir)
    if output_dir is None:
        output_dir = input_dir
    else:
        output_dir = os.path.abspath(output_dir)
    
    if not os.path.exists(input_dir):
        error_msg = f'Input directory not found: {input_dir}'
        print(f'⚠️  Warning: {error_msg}')
        raise FileNotFoundError(error_msg)
    
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 检查输入目录中是否有文件
    json_files = [f for f in os.listdir(input_dir) if f.endswith('.json') and f.startswith('generated_workflow_')]
    if not json_files:
        error_msg = f'No workflow files found in input directory: {input_dir}'
        print(f'⚠️  Warning: {error_msg}')
        raise FileNotFoundError(error_msg)
    
    # 简化输出 - 由 JS 脚本输出单行结果
    
    # 构建命令
    cmd = [
        'node',
        js_script,
        '--input', input_dir,
        '--direction', direction
    ]
    
    if output_dir != input_dir:
        cmd.extend(['--output', output_dir])
    
    # 执行 Node.js 脚本
    try:
        result = subprocess.run(cmd, 
                              capture_output=True, 
                              text=True, 
                              encoding='utf-8',
                              errors='replace',
                              timeout=300,
                              cwd=script_dir)
        
        # 打印输出
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        
        if result.returncode != 0:
            raise RuntimeError(f"Node.js script failed with exit code {result.returncode}")
        
    except subprocess.TimeoutExpired:
        raise RuntimeError("Node.js script execution timeout")
    except Exception as e:
        raise RuntimeError(f"Error executing Node.js script: {str(e)}")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='优化工作流布局（使用 Dagre）')
    parser.add_argument('--input', '-i', type=str, default='output/step6_final',
                        help='输入文件或目录路径')
    parser.add_argument('--output', '-o', type=str, default=None,
                        help='输出文件或目录路径（如果为 None，则覆盖原文件）')
    parser.add_argument('--direction', '-d', type=str, default='LR',
                        choices=['LR', 'TB'],
                        help='布局方向：LR (从左到右) 或 TB (从上到下)')
    parser.add_argument('--file', '-f', type=str, default=None,
                        help='处理单个文件（如果指定，则忽略 --input）')
    
    args = parser.parse_args()
    
    try:
        if args.file:
            # 处理单个文件
            optimize_workflow_layout(args.file, args.output, args.direction)
        else:
            # 处理目录中的所有文件
            process_all_workflows(args.input, args.output, args.direction)
    except Exception as e:
        print(f'❌ Error: {str(e)}', file=sys.stderr)
        sys.exit(1)
