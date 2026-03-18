import re
import json
import datetime
import random
import uuid
import urllib.parse
from typing import Any, List, Tuple, Optional

# --------- 1. 工具函数：日期/列表/字符串/校验等 ---------
def __add_date(dt_obj, amount: str, unit: str) -> str:
    """把 Dialogflow 日期对象 ± amount 个 unit 后返回 ISO 字符串"""
    amount = int(amount)
    dt = datetime.datetime(**{k: v for k, v in dt_obj.items() if k in {
        'year', 'month', 'day', 'hours', 'minutes', 'seconds'}})
    if unit == 'YEARS':
        dt = dt.replace(year=dt.year + amount)
    elif unit == 'MONTHS':
        # 用 timedelta 会溢出，直接 replace
        new_month = dt.month + amount
        new_year = dt.year + (new_month - 1) // 12
        new_month = (new_month - 1) % 12 + 1
        dt = dt.replace(year=new_year, month=new_month)
    elif unit == 'DAYS':
        dt += datetime.timedelta(days=amount)
    elif unit == 'WEEKS':
        dt += datetime.timedelta(weeks=amount)
    elif unit == 'HOURS':
        dt += datetime.timedelta(hours=amount)
    elif unit == 'MINUTES':
        dt += datetime.timedelta(minutes=amount)
    elif unit == 'SECONDS':
        dt += datetime.timedelta(seconds=amount)
    return dt.isoformat()


def __fmt_date(dt_obj, fmt: str, lang: str = 'en') -> str:
    """按 Dialogflow 格式符号转日期字符串"""
    dt = datetime.datetime(**{k: v for k, v in dt_obj.items() if k in {
        'year', 'month', 'day', 'hours', 'minutes', 'seconds'}})
    # 简化映射，生产环境可再补全
    fmt = fmt.replace('yyyy', '%Y').replace('yy', '%y') \
             .replace('MMMM', '%B').replace('MMM', '%b').replace('MM', '%m') \
             .replace('dd', '%d').replace('EEEE', '%A').replace('E', '%a') \
             .replace('HH', '%H').replace('h', '%I').replace('mm', '%M') \
             .replace('ss', '%S').replace('a', '%p')
    return dt.strftime(fmt)


def __is_future_date(dt_obj) -> bool:
    dt = datetime.datetime(**{k: v for k, v in dt_obj.items() if k in {
        'year', 'month', 'day', 'hours', 'minutes', 'seconds'}})
    return dt > datetime.datetime.now()


def __is_past_date(dt_obj) -> bool:
    dt = datetime.datetime(**{k: v for k, v in dt_obj.items() if k in {
        'year', 'month', 'day', 'hours', 'minutes', 'seconds'}})
    return dt < datetime.datetime.now()


def __phone(number: str, region: str = 'US') -> dict:
    # 简化版：只拆国家码/区号/号码，不验证真实性
    number = number.replace(' ', '').replace('-', '')
    if number.startswith('+'):
        number = number[1:]
        if number.startswith('1'):
            # 美国号码：国家码1位，区号3位
            country = number[:1]
            area = number[1:4]
            num = number[4:]
        else:
            # 其他国家：假设国家码2位，区号3位
            country = number[:2]
            area = number[2:5]
            num = number[5:]
    else:
        # 无+号，假设是美国号码
        country = '1'
        area = number[:3]
        num = number[3:]
    return {'country-code': country, 'area-code': area, 'number': num}


def __is_phone_number(number: str, region: str = 'US') -> bool:
    """
    验证电话号码是否符合指定区域的格式
    
    注意：完整验证需要使用phonenumbers库，当前实现提供基本格式验证。
    如需支持所有ISO-3166国家代码的精确验证，建议使用phonenumbers库。
    
    参数:
        number: 电话号码字符串
        region: ISO-3166国家代码（如 "US", "CN", "GB"），可选，默认"US"
    
    返回:
        bool: 如果电话号码格式有效返回True，否则返回False
    """
    if not number or not isinstance(number, str):
        return False
    
    # 移除常见分隔符
    cleaned = number.replace(' ', '').replace('-', '').replace('(', '').replace(')', '').replace('.', '')
    
    # 检查是否只包含数字和+号
    if not all(c.isdigit() or c == '+' for c in cleaned):
        return False
    
    # 处理国际格式（带+号）
    if cleaned.startswith('+'):
        # 移除+号，只保留数字
        digits = cleaned[1:]
        if not digits.isdigit():
            return False
        
        # 国际格式验证（ITU-T E.164标准）
        # 最短：7位（某些小国家的短号码）
        # 最长：15位（包括国家码）
        # 常见格式：1位国家码（如美国+1）+ 10位号码 = 11位
        if len(digits) < 7 or len(digits) > 15:
            return False
        
        # 基本格式检查：国际格式应该以国家码开头
        # 如果以1开头（美国/加拿大），应该是11位
        if digits.startswith('1'):
            if len(digits) == 11:
                # 验证美国/加拿大格式：1 + 区号（不能以0或1开头）+ 交换码（不能以0或1开头）+ 号码
                if digits[1] in ['0', '1'] or digits[4] in ['0', '1']:
                    return False
                return True
            elif len(digits) == 10:
                # 可能是省略了国家码1，但格式正确
                if digits[0] in ['0', '1'] or digits[3] in ['0', '1']:
                    return False
                return True
        
        # 其他国家的国际格式：至少8位（2位国家码 + 至少6位号码）
        if len(digits) >= 8:
            return True
        
        return False
    
    # 处理本地格式（无+号）
    if not cleaned.isdigit():
        return False
    
    # 根据区域代码进行基本验证
    region = region.upper() if region else 'US'
    
    # 美国/加拿大格式验证
    if region == 'US' or region == 'CA':
        # 美国号码：10位数字（不含国家码）
        # 格式：NXX-NXX-XXXX（区号3位 + 交换码3位 + 号码4位）
        if len(cleaned) == 10:
            # 检查区号不能以0或1开头
            if cleaned[0] in ['0', '1']:
                return False
            # 检查交换码不能以0或1开头
            if cleaned[3] in ['0', '1']:
                return False
            return True
        # 11位数字（带国家码1）
        elif len(cleaned) == 11 and cleaned[0] == '1':
            if cleaned[1] in ['0', '1'] or cleaned[4] in ['0', '1']:
                return False
            return True
    
    # 其他国家的通用验证
    # 大多数国家的本地号码长度在7-15位之间
    if len(cleaned) < 7 or len(cleaned) > 15:
        return False
    
    # 基本格式检查：不能全为相同数字
    if len(set(cleaned)) == 1:
        return False
    
    return True


def __is_date(date_str: str, format_str: str, lang: str = 'en') -> bool:
    """
    验证日期字符串是否可以按照指定格式解析
    支持基本格式验证和日期有效性检查（包括闰年验证）
    
    注意：多语言支持（如中文"四月"、俄文"апр"）需要额外的语言库支持，
    当前实现主要支持英文格式。如需完整多语言支持，建议使用dateparser库。
    
    参数:
        date_str: 日期字符串
        format_str: 格式模式（如 "uuuu-MM-dd", "uu MMM dd"）
        lang: 语言代码（当前主要用于说明，完整支持需要额外库）
    
    返回:
        bool: 如果日期有效且符合格式返回True，否则返回False
    """
    if not date_str or not format_str:
        return False
    
    try:
        # 转换Dialogflow格式模式到Python格式
        # 注意："u" 表示年份（era-independent），"y" 表示年份（era-dependent）
        # 在大多数情况下可以互换使用，但严格来说"u"更准确
        python_format = format_str
        
        # 替换年份格式：uuuu -> %Y (4位年份), uu -> %y (2位年份)
        # yyyy -> %Y, yy -> %y (注意：y是year-of-era，u是year)
        python_format = python_format.replace('uuuu', '%Y').replace('uu', '%y')
        python_format = python_format.replace('yyyy', '%Y').replace('yy', '%y')
        
        # 替换月份格式：MMMM -> %B (完整月份名), MMM -> %b (缩写), MM -> %m (数字)
        # 注意：MMM和MMMM需要英文月份名，多语言需要额外处理
        python_format = python_format.replace('MMMM', '%B').replace('MMM', '%b').replace('MM', '%m')
        
        # 替换日期格式：dd -> %d
        python_format = python_format.replace('dd', '%d')
        
        # 替换星期格式：EEEE -> %A (完整星期名), E -> %a (缩写)
        python_format = python_format.replace('EEEE', '%A').replace('E', '%a')
        
        # 替换时间格式：HH -> %H (24小时制), hh -> %I (12小时制)
        # mm -> %M (分钟), ss -> %S (秒)
        python_format = python_format.replace('HH', '%H').replace('hh', '%I')
        python_format = python_format.replace('mm', '%M').replace('ss', '%S')
        
        # 替换AM/PM：a -> %p
        python_format = python_format.replace('a', '%p')
        
        # 尝试解析日期（会自动验证日期有效性，包括闰年）
        parsed_date = datetime.datetime.strptime(date_str, python_format)
        
        # 额外验证：检查日期是否在合理范围内
        if parsed_date.year < 1 or parsed_date.year > 9999:
            return False
        
        # datetime.strptime已经验证了日期有效性（包括闰年），
        # 如果解析成功，说明日期格式正确且有效
        # 例如：2月29日在非闰年会被strptime自动拒绝
        
        return True
            
    except ValueError:
        # 格式不匹配或日期无效（包括闰年检查失败）
        # 例如："21 Feb 29" 在非闰年会被拒绝
        # 例如："21 Apr 42" 会被拒绝（4月只有30天）
        return False
    except Exception:
        # 其他错误
        return False


def __is_credit_card_number(card_number: str) -> bool:
    """
    使用Luhn算法验证信用卡号码
    
    参数:
        card_number: 信用卡号码字符串
    
    返回:
        bool: 如果信用卡号码通过Luhn算法验证返回True，否则返回False
    """
    if not card_number or not isinstance(card_number, str):
        return False
    
    # 移除空格和连字符，只保留数字
    digits = ''.join(filter(str.isdigit, card_number))
    
    # 检查长度：信用卡号码通常在13-19位之间
    if len(digits) < 13 or len(digits) > 19:
        return False
    
    # 如果为空字符串，返回False
    if not digits:
        return False
    
    # Luhn算法验证
    # 1. 从右到左，对偶数位置的数字（从右数第2, 4, 6...位）乘以2
    # 2. 如果乘以2后的数字大于9，则减去9（等价于将两位数字相加）
    # 3. 将所有数字相加
    # 4. 如果总和能被10整除，则号码有效
    
    total = 0
    # 从右到左处理每一位
    for i, digit_char in enumerate(reversed(digits)):
        digit = int(digit_char)
        
        # 偶数位置（从右数第2, 4, 6...位，即索引1, 3, 5...）
        if i % 2 == 1:
            # 乘以2
            doubled = digit * 2
            # 如果大于9，减去9（等价于将两位数字相加）
            if doubled > 9:
                doubled -= 9
            total += doubled
        else:
            # 奇数位置直接相加
            total += digit
    
    # 如果总和能被10整除，则号码有效
    return total % 10 == 0


def __filter(data, jsonpath_expr: str):
    """
    使用JsonPath表达式过滤数据
    
    注意：需要安装jsonpath-ng库。如果未安装，函数会返回空列表。
    安装方法：pip install jsonpath-ng
    
    参数:
        data: 要过滤的数据对象（字典或列表）
        jsonpath_expr: JsonPath表达式字符串
    
    返回:
        list: 匹配的结果列表
    """
    try:
        # 尝试导入jsonpath-ng库（可选依赖）
        try:
            from jsonpath_ng import parse  # type: ignore[import-untyped]
        except ImportError:
            # 如果未安装库，返回空列表并打印警告
            print("Warning: jsonpath-ng library not installed. FILTER function requires: pip install jsonpath-ng")
            return []
        
        # 解析JsonPath表达式
        jsonpath_expr_parsed = parse(jsonpath_expr)
        # 查找匹配的值
        matches = jsonpath_expr_parsed.find(data)
        # 返回匹配值的列表
        return [match.value for match in matches]
    except Exception as e:
        # 如果解析或执行出错，返回空列表
        return []


def __nested_field(obj, *keys):
    """
    动态访问嵌套字段
    
    支持访问嵌套对象和数组索引（如 "c[0]"）
    最多支持10层嵌套
    
    参数:
        obj: 要访问的对象（字典或列表）
        *keys: 一个或多个键路径（字符串），支持数组索引语法如 "c[0]"
    
    返回:
        嵌套字段的值，如果路径不存在则返回None
    """
    try:
        result = obj
        for key in keys:
            if result is None:
                return None
            
            # 处理数组索引，如 "c[0]" -> 访问 result["c"][0]
            if '[' in key and key.endswith(']'):
                # 分离字段名和索引
                field_name = key[:key.index('[')]
                index_str = key[key.index('[')+1:-1]  # 去掉 [ 和 ]
                try:
                    index = int(index_str)
                except ValueError:
                    return None  # 索引不是有效数字
                
                # 先访问字段
                if isinstance(result, dict):
                    result = result.get(field_name)
                else:
                    return None
                
                # 再访问数组索引
                if isinstance(result, list) and 0 <= index < len(result):
                    result = result[index]
                else:
                    return None
            else:
                # 普通字段访问
                if isinstance(result, dict):
                    result = result.get(key)
                elif isinstance(result, list):
                    # 列表不支持字符串键访问
                    return None
                else:
                    return None
        
        return result
    except (KeyError, IndexError, TypeError, AttributeError):
        # 键不存在、索引越界、类型错误等
        return None


# --------- 2. 函数名 → Python 模板/工具映射 ---------
FUNC_TEMPLATE = {
    # 数学
    'ADD': '({} + {})',  # 多参数时会在代码中处理
    'MINUS': '({} - {})',
    'MULTIPLY': '({} * {})',  # 多参数时会在代码中处理
    'DIVIDE': 'round({} / {}, {})',  # 3参：被除数、除数、小数位（可选）
    'ROUND': 'round({}, {})',  # 默认两个参数，单参数时会在代码中处理

    # 日期时间
    'NOW': 'datetime.datetime.now().isoformat()',
    'ADD_DATE': "__add_date({}, {}, {})",  # 3 参：对象、数值、单位
    'FORMAT_DATE': "__fmt_date({}, {}, {})",
    'IS_FUTURE_DATE': '__is_future_date({})',
    'IS_PAST_DATE': '__is_past_date({})',

    # 字符串
    'CONCATENATE': 'str({}) + str({})',  # 多参数时会在代码中处理
    'LEN': 'len(str({}))',
    'LOWER': 'str({}).lower()',
    'UPPER': 'str({}).upper()',
    'MID': 'str({})[{}-1:{}-1+{}]',  # 注意 1→0 索引
    'SUBSTITUTE': 're.sub(r"{}", r"{}", str({}))',
    'SPLIT': 're.split(r"{}", str({}))',
    'JOIN': '({}).join(map(str, {}))',  # 3参数时会在代码中处理
    'URL_ENCODE': 'urllib.parse.quote_plus(str({}))',

    # 列表
    'APPEND': '({} + list({}))',  # 多参数时会在代码中处理
    'REMOVE': '[x for x in {} if x not in set({})]',  # 多参数时会在代码中处理
    'COUNT': 'len({})',
    'UNIQUE': 'sorted(set({}), key=lambda __k: {}.index(__k))',
    'CONTAIN': '({} in {})',
    'MATCH': '({}.index({}) if {} in {} else -1)',
    'GET': '({}[{}] if 0 <= {} < len({}) else None)',
    
    # 对象操作
    'GET_FIELD': '({}.get({}, None))',
    'IDENTITY': '{}',  # 直接返回，不做类型转换
    'FILTER': '__filter({}, {})',  # 2参：数据对象、JsonPath表达式（需要特殊处理）
    'NESTED_FIELD': '__nested_field({})',  # 可变参数：对象 + 一个或多个键（需要特殊处理）

    # 类型转换
    'TO_TEXT': 'str({})',
    'TO_NUMBER': 'float({})',
    'TO_OBJECT': 'json.loads({})',
    'TO_PHONE_NUMBER': '__phone({}, {})',  # 2参：号码、区域（可选）

    # 校验
    'IS_DATE': '__is_date({}, {}, {})',  # 3参：日期字符串、格式、语言（可选）
    'IS_PHONE_NUMBER': '__is_phone_number({}, {})',  # 2参：号码、区域（可选）
    'IS_CREDIT_CARD_NUMBER': '__is_credit_card_number({})',  # 简化：接 luhn 算法

    # 生成
    'RAND': 'random.random()',
    'UUID': 'str(uuid.uuid4())',

    # 逻辑
    'IF': '({} if {} else {})',  # 条件、真值、假值
}

# --------- 3. 主函数：与原函数完全一致 ---------
def parse_dialogflow_value(value: Any, input_variables: Optional[List[str]] = None) -> Tuple[str, List[str]]:
    """
    解析 Dialogflow 的值表达式，转换为 Python 代码
    
    参数:
        value: 要解析的值（可以是字符串、数字、布尔值、列表、字典等）
        input_variables: 可选的输入变量列表，用于收集需要的输入变量。默认为None，会创建新列表
    
    返回:
        Tuple[str, List[str]]: (生成的Python代码, 输入变量列表)
    """
    # 如果 input_variables 为 None，创建新列表
    if input_variables is None:
        input_variables = []
    
    if value is None:
        return 'None', input_variables
    if isinstance(value, bool):
        return str(value), input_variables
    if isinstance(value, (int, float)):
        return str(value), input_variables
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False), input_variables

    if isinstance(value, str):
        # 3.1 系统函数
        if value.startswith('$sys.func.'):
            # 匹配函数名和参数（参数可以为空，如 NOW()）
            func_match = re.match(r'\$sys\.func\.(\w+)\((.*)\)', value)
            if func_match:
                func_name = func_match.group(1)
                args_str = func_match.group(2)  # 可能为空字符串（如 NOW()）
                # 递归解析每个参数
                arg_codes = []
                if args_str.strip():  # 只有当参数不为空时才解析
                    for a in _split_function_args(args_str):
                        code, input_variables = parse_dialogflow_value(a.strip(), input_variables)
                        arg_codes.append(code)

                template = FUNC_TEMPLATE.get(func_name)
                if template:
                    # 处理无参数函数（如 NOW, RAND, UUID）
                    if func_name in ['NOW', 'RAND', 'UUID']:
                        # 这些函数没有占位符，直接返回模板
                        return template, input_variables
                    # 处理可变参数函数
                    if func_name == 'ROUND' and len(arg_codes) == 1:
                        template = 'round({})'
                    elif func_name == 'FORMAT_DATE' and len(arg_codes) == 2:
                        template = '__fmt_date({}, {})'
                    elif func_name == 'DIVIDE' and len(arg_codes) == 2:
                        template = 'round({} / {}, 3)'  # 默认scale=3
                    elif func_name == 'JOIN':
                        if len(arg_codes) == 2:
                            template = '({}).join(map(str, {}))'  # 无final delimiter
                        elif len(arg_codes) == 3:
                            # 有final delimiter：前n-1个用delimiter，最后一个前用final_delimiter
                            delimiter = arg_codes[0]
                            list_var = arg_codes[1]
                            final_delimiter = arg_codes[2]
                            # 实现逻辑：如果列表长度>1，前n-1个用delimiter连接，然后加上final_delimiter和最后一个
                            template = f'(({delimiter}).join(map(str, {list_var}[:-1])) + {final_delimiter} + str({list_var}[-1])) if len({list_var}) > 1 else str({list_var}[0]) if len({list_var}) == 1 else ""'
                            return template, input_variables
                    elif func_name == 'IS_PHONE_NUMBER' and len(arg_codes) == 1:
                        template = '__is_phone_number({})'  # 默认region
                    elif func_name == 'IS_DATE' and len(arg_codes) == 2:
                        template = '__is_date({}, {})'  # 默认language
                    elif func_name == 'TO_PHONE_NUMBER' and len(arg_codes) == 1:
                        template = '__phone({})'  # 默认region
                    # 处理FILTER函数（第一个参数可能是字符串形式的参数引用）
                    elif func_name == 'FILTER':
                        if len(arg_codes) != 2:
                            return f'None  # FILTER requires exactly 2 arguments', input_variables
                        # 第一个参数：如果是字符串形式的参数引用（如 "$session.params.shapes"），
                        # 需要解析为实际变量。如果已经是变量引用，直接使用。
                        param_ref = arg_codes[0]
                        jsonpath_expr = arg_codes[1]
                        
                        # 检查第一个参数是否是字符串形式的参数引用
                        # 如果是字符串（以引号开头和结尾），尝试解析为变量引用
                        if param_ref.startswith('"') and param_ref.endswith('"'):
                            # 去掉引号，解析变量引用
                            param_str = param_ref[1:-1]  # 去掉引号
                            if param_str.startswith('$'):
                                # 解析变量引用（如 "$session.params.shapes"）
                                var_ref = param_str[1:]  # 去掉$
                                if 'request.user-utterance' in var_ref:
                                    var_py = 'user_utterance'
                                else:
                                    # 将路径转换为Python变量名（如 "session.params.shapes" -> "session_params_shapes"）
                                    var_py = var_ref.replace('.', '_').replace('-', '_')
                                # 添加到输入变量列表
                                if var_py not in input_variables:
                                    input_variables.append(var_py)
                                param_ref = var_py
                        
                        template = f'__filter({param_ref}, {jsonpath_expr})'
                        return template, input_variables
                    # 处理NESTED_FIELD函数（第一个参数是字符串形式的参数引用，后续是可变数量的键）
                    elif func_name == 'NESTED_FIELD':
                        if len(arg_codes) < 2:
                            return f'None  # NESTED_FIELD requires at least 2 arguments', input_variables
                        
                        # 第一个参数：字符串形式的参数引用（如 "$session.params.your-param-1"）
                        param_ref = arg_codes[0]
                        keys = arg_codes[1:]  # 后续参数是键路径
                        
                        # 检查第一个参数是否是字符串形式的参数引用
                        if param_ref.startswith('"') and param_ref.endswith('"'):
                            # 去掉引号，解析变量引用
                            param_str = param_ref[1:-1]  # 去掉引号
                            if param_str.startswith('$'):
                                # 解析变量引用（如 "$session.params.your-param-1"）
                                var_ref = param_str[1:]  # 去掉$
                                if 'request.user-utterance' in var_ref:
                                    var_py = 'user_utterance'
                                else:
                                    # 将路径转换为Python变量名（如 "session.params.your-param-1" -> "session_params_your_param_1"）
                                    var_py = var_ref.replace('.', '_').replace('-', '_')
                                # 添加到输入变量列表
                                if var_py not in input_variables:
                                    input_variables.append(var_py)
                                param_ref = var_py
                        
                        # 构建键参数列表（确保字符串键有引号）
                        key_args = []
                        for key in keys:
                            # 如果键已经是字符串字面量（有引号），直接使用
                            # 否则添加引号
                            if (key.startswith('"') and key.endswith('"')) or \
                               (key.startswith("'") and key.endswith("'")):
                                key_args.append(key)
                            else:
                                # 添加引号
                                key_args.append(f'"{key}"')
                        
                        # 生成函数调用：__nested_field(obj, "key1", "key2", ...)
                        keys_str = ', '.join(key_args)
                        template = f'__nested_field({param_ref}, {keys_str})'
                        return template, input_variables
                    # 处理多参数函数
                    elif func_name in ['ADD', 'MULTIPLY', 'CONCATENATE']:
                        if len(arg_codes) < 2:
                            return f'None  # {func_name} requires at least 2 arguments', input_variables
                        if func_name == 'ADD':
                            template = ' + '.join([f'({code})' for code in arg_codes])
                        elif func_name == 'MULTIPLY':
                            template = ' * '.join([f'({code})' for code in arg_codes])
                        elif func_name == 'CONCATENATE':
                            template = ' + '.join([f'str({code})' for code in arg_codes])
                        return template, input_variables
                    # 处理APPEND和REMOVE（支持多个参数）
                    elif func_name == 'APPEND':
                        if len(arg_codes) < 2:
                            return f'None  # APPEND requires at least 2 arguments', input_variables
                        # 第一个是列表，其余是要追加的值（如果是列表则展开）
                        list_var = arg_codes[0]
                        append_parts = []
                        for code in arg_codes[1:]:
                            # 如果是列表则展开，否则作为单个元素
                            append_parts.append(f'({code} if isinstance({code}, list) else [{code}])')
                        template = f'({list_var} if {list_var} is not None else []) + ' + ' + '.join(append_parts)
                        return template, input_variables
                    elif func_name == 'REMOVE':
                        if len(arg_codes) < 2:
                            return f'None  # REMOVE requires at least 2 arguments', input_variables
                        # 第一个是列表，其余是要移除的值（如果是列表则展开）
                        list_var = arg_codes[0]
                        remove_items = []
                        for code in arg_codes[1:]:
                            # 如果是列表则展开，否则作为单个元素
                            remove_items.append(f'({code} if isinstance({code}, list) else [{code}])')
                        remove_list = ' + '.join(remove_items)
                        template = f'[x for x in ({list_var} if {list_var} is not None else []) if x not in ({remove_list})]'
                        return template, input_variables
                    # 处理MID函数（需要特殊处理索引计算）
                    elif func_name == 'MID':
                        if len(arg_codes) != 3:
                            return f'None  # MID requires exactly 3 arguments', input_variables
                        str_var = arg_codes[0]
                        start_pos = arg_codes[1]  # 1-based索引
                        length = arg_codes[2]
                        # MID: str({})[{}-1:{}-1+{}] 需要特殊处理索引计算
                        template = f'str({str_var})[{start_pos}-1:{start_pos}-1+{length}]'
                        return template, input_variables
                    # 处理需要重复参数的函数
                    elif func_name == 'MATCH':
                        if len(arg_codes) != 2:
                            return f'None  # MATCH requires exactly 2 arguments', input_variables
                        list_var = arg_codes[0]
                        item_var = arg_codes[1]
                        # MATCH: ({}.index({}) if {} in {} else -1) 需要重复使用参数
                        template = f'({list_var}.index({item_var}) if {item_var} in {list_var} else -1)'
                        return template, input_variables
                    elif func_name == 'GET':
                        if len(arg_codes) != 2:
                            return f'None  # GET requires exactly 2 arguments', input_variables
                        list_var = arg_codes[0]
                        index_var = arg_codes[1]
                        # GET: ({}[{}] if 0 <= {} < len({}) else None) 需要重复使用参数
                        template = f'({list_var}[{index_var}] if 0 <= {index_var} < len({list_var}) else None)'
                        return template, input_variables
                    elif func_name == 'UNIQUE':
                        if len(arg_codes) != 1:
                            return f'None  # UNIQUE requires exactly 1 argument', input_variables
                        list_var = arg_codes[0]
                        # UNIQUE: sorted(set({}), key=lambda __k: {}.index(__k)) 需要重复使用参数
                        template = f'sorted(set({list_var}), key=lambda __k: {list_var}.index(__k))'
                        return template, input_variables
                    # 处理GET_FIELD函数（支持嵌套路径）
                    elif func_name == 'GET_FIELD':
                        if len(arg_codes) != 2:
                            return f'None  # GET_FIELD requires exactly 2 arguments', input_variables
                        
                        # 获取原始第一个参数（在解析前）
                        original_first_arg = _split_function_args(args_str)[0].strip()
                        second_arg = arg_codes[1]
                        
                        # 检查第一个参数是否是嵌套路径（如 $session.params.PE_SegmentNumber.CardServicing.CustomerSegment.segmentNumberText）
                        if original_first_arg.startswith('$'):
                            var_ref = original_first_arg[1:]  # 去掉$
                            
                            # 去掉末尾的点
                            while var_ref.endswith('.'):
                                var_ref = var_ref[:-1]
                            
                            # 分割路径
                            parts = var_ref.split('.')
                            
                            # 检查是否是 xxx.params.XXX 格式（如 session.params.XXX, flow.params.XXX 等）
                            if len(parts) >= 3 and parts[1] == 'params':
                                # 获取 xxx.params 后面的部分
                                param_parts = parts[2:]  # 如 ['PE_SegmentNumber', 'CardServicing', 'CustomerSegment', 'segmentNumberText']
                                
                                if len(param_parts) >= 2:
                                    # 有嵌套路径，第一部分是变量名，其余是要链式 get 的键
                                    root_var = param_parts[0].replace('-', '_')
                                    nested_keys = param_parts[1:]  # 如 ['CardServicing', 'CustomerSegment', 'segmentNumberText']
                                    
                                    # 移除递归解析时错误添加的变量（arg_codes[0] 是递归解析的结果，如 segmentNumberText）
                                    wrong_var = arg_codes[0]
                                    if wrong_var in input_variables:
                                        input_variables.remove(wrong_var)
                                    
                                    # 添加正确的根变量到输入变量列表
                                    if root_var not in input_variables:
                                        input_variables.append(root_var)
                                    
                                    # 构建链式 get 调用
                                    # PE_SegmentNumber.get("CardServicing", {}).get("CustomerSegment", {}).get("segmentNumberText", {}).get(customerSegment, None)
                                    chain_parts = [root_var]
                                    for key in nested_keys:
                                        chain_parts.append(f'.get("{key}", {{}})')
                                    
                                    # 最后一个 get 用于获取 second_arg 对应的值
                                    chain_code = ''.join(chain_parts) + f'.get({second_arg}, None)'
                                    return chain_code, input_variables
                        
                        # 默认行为：简单的 obj.get(key, None)
                        obj_var = arg_codes[0]
                        template = f'({obj_var}.get({second_arg}, None))'
                        return template, input_variables
                    # 处理IF函数（需要调整参数顺序）
                    elif func_name == 'IF':
                        if len(arg_codes) != 3:
                            return f'None  # IF requires exactly 3 arguments', input_variables
                        # Dialogflow: IF(condition, true_value, false_value)
                        # Python三元运算符: true_value if condition else false_value
                        condition = arg_codes[0]
                        true_value = arg_codes[1]
                        false_value = arg_codes[2]
                        
                        # 如果condition是字符串字面量，需要解析为Python表达式
                        # 例如: "1 < 2" -> 1<2
                        # 例如: "\"$session.params.PAYMENTDUEDATE=null\"" -> PAYMENTDUEDATE=null（只替换变量引用，保持条件表达式原样）
                        # 检查condition是否是字符串字面量（Python代码中的字符串字面量格式）
                        # 可能是 '"..."' 或 "\"..."\" 格式
                        is_string_literal = False
                        condition_str = None
                        
                        # 检查是否是双引号字符串字面量: "..." 或 "\"...\""
                        if condition.startswith('"') and condition.endswith('"'):
                            is_string_literal = True
                            try:
                                # json.loads可以正确处理转义的引号
                                condition_str = json.loads(condition)
                                # 如果解析后的字符串仍然以引号开头和结尾，需要再次去掉引号
                                # 例如: json.loads('"\\"1 < 2\\""') 返回 '"1 < 2"'，需要去掉引号得到 '1 < 2'
                                if isinstance(condition_str, str) and len(condition_str) >= 2:
                                    if (condition_str.startswith('"') and condition_str.endswith('"')) or \
                                       (condition_str.startswith("'") and condition_str.endswith("'")):
                                        # 去掉外层引号
                                        condition_str = condition_str[1:-1]
                                        # 处理转义的引号
                                        condition_str = condition_str.replace('\\"', '"').replace("\\'", "'").replace('\\\\', '\\')
                            except (json.JSONDecodeError, ValueError):
                                # 如果json.loads失败，手动处理
                                # 去掉外层引号
                                condition_str = condition[1:-1]
                                # 处理转义的引号
                                condition_str = condition_str.replace('\\"', '"').replace('\\\\', '\\')
                        # 检查是否是单引号字符串字面量: '...' 或 '"..."'
                        elif condition.startswith("'") and condition.endswith("'"):
                            # 可能是 '"..."' 格式（单引号包裹的双引号字符串）
                            inner = condition[1:-1]  # 去掉外层单引号
                            if inner.startswith('"') and inner.endswith('"'):
                                is_string_literal = True
                                try:
                                    # 尝试解析内层的双引号字符串
                                    condition_str = json.loads(inner)
                                    # 如果解析后的字符串仍然以引号开头和结尾，需要再次去掉引号
                                    if isinstance(condition_str, str) and len(condition_str) >= 2:
                                        if (condition_str.startswith('"') and condition_str.endswith('"')) or \
                                           (condition_str.startswith("'") and condition_str.endswith("'")):
                                            condition_str = condition_str[1:-1].replace('\\"', '"').replace("\\'", "'").replace('\\\\', '\\')
                                except (json.JSONDecodeError, ValueError):
                                    # 如果失败，手动处理
                                    condition_str = inner[1:-1].replace('\\"', '"').replace('\\\\', '\\')
                        
                        if is_string_literal and condition_str is not None:
                            # 解析条件表达式字符串，将其中的变量引用转换为Python变量
                            # 匹配 $session.params.XXX 或 $XXX 格式的变量引用
                            def replace_var_ref(match):
                                var_ref = match.group(0)  # 完整的变量引用，如 $session.params.PAYMENTDUEDATE
                                var_path = var_ref[1:]  # 去掉开头的$
                                
                                # 处理特殊变量
                                if 'request.user-utterance' in var_path:
                                    var_py = 'user_utterance'
                                elif var_path.startswith('session.params.'):
                                    # 提取变量名部分（去掉 session.params. 前缀）
                                    var_name = var_path.replace('session.params.', '')
                                    # 转换为Python变量名（将点号和连字符替换为下划线）
                                    var_py = var_name.replace('.', '_').replace('-', '_')
                                else:
                                    # 简单变量引用，直接转换
                                    var_py = var_path.replace('.', '_').replace('-', '_')
                                
                                # 添加到输入变量列表
                                if var_py not in input_variables:
                                    input_variables.append(var_py)
                                
                                return var_py
                            
                            # 替换所有变量引用
                            # 匹配 $session.params.XXX 或 $XXX（支持点号和连字符）
                            condition_str = re.sub(r'\$session\.params\.[a-zA-Z0-9_.-]+|\$[a-zA-Z0-9_.-]+', replace_var_ref, condition_str)
                            
                            # 注意：不再处理 =null 等条件表达式的转换，保持原样
                            # 条件表达式是什么样就保持什么样，只替换变量引用
                            
                            condition = condition_str
                        
                        # 处理 true_value 和 false_value，如果它们是字符串字面量，去掉多余的转义引号
                        # 例如: "\"N/A\"" -> "N/A"
                        def clean_string_literal(value_str):
                            """清理字符串字面量，去掉多余的转义引号"""
                            if value_str.startswith('"') and value_str.endswith('"'):
                                try:
                                    # 使用 json.loads 正确解析字符串
                                    parsed = json.loads(value_str)
                                    if isinstance(parsed, str):
                                        # 如果解析后的字符串仍然以引号开头和结尾，需要再次去掉引号
                                        # 例如: json.loads('"\\"N/A\\""') 返回 '"N/A"'，需要去掉引号得到 'N/A'
                                        if len(parsed) >= 2:
                                            if (parsed.startswith('"') and parsed.endswith('"')) or \
                                               (parsed.startswith("'") and parsed.endswith("'")):
                                                parsed = parsed[1:-1]
                                                # 处理转义的引号
                                                parsed = parsed.replace('\\"', '"').replace("\\'", "'").replace('\\\\', '\\')
                                        # 重新格式化为 Python 字符串字面量
                                        # 转义特殊字符
                                        escaped = parsed.replace('\\', '\\\\').replace('"', '\\"')
                                        return f'"{escaped}"'
                                except (json.JSONDecodeError, ValueError):
                                    pass
                            elif value_str.startswith("'") and value_str.endswith("'"):
                                # 处理单引号字符串字面量
                                inner = value_str[1:-1]
                                if inner.startswith('"') and inner.endswith('"'):
                                    try:
                                        parsed = json.loads(inner)
                                        if isinstance(parsed, str):
                                            # 如果解析后的字符串仍然以引号开头和结尾，需要再次去掉引号
                                            if len(parsed) >= 2:
                                                if (parsed.startswith('"') and parsed.endswith('"')) or \
                                                   (parsed.startswith("'") and parsed.endswith("'")):
                                                    parsed = parsed[1:-1].replace('\\"', '"').replace("\\'", "'").replace('\\\\', '\\')
                                            # 重新格式化为 Python 字符串字面量
                                            escaped = parsed.replace('\\', '\\\\').replace('"', '\\"')
                                            return f'"{escaped}"'
                                    except (json.JSONDecodeError, ValueError):
                                        pass
                            return value_str
                        
                        true_value = clean_string_literal(true_value)
                        false_value = clean_string_literal(false_value)
                        
                        template = f'({true_value} if {condition} else {false_value})'
                        return template, input_variables
                    
                    return template.format(*arg_codes), input_variables
                else:
                    return f'None  # TODO: unsupported function {func_name}', input_variables

        # 3.2 变量引用
        if value.startswith('$'):
            var_ref = value[1:]
            # writed by senlin.deng 2026-01-16
            # 特殊处理：如果变量引用以点结尾（可能是误写），去掉末尾的点
            # 例如: "$session.params.CardType." -> "session.params.CardType"
            while var_ref.endswith('.'):
                var_ref = var_ref[:-1]
            if 'request.user-utterance' in var_ref:
                var_py = 'user_utterance'
            else:
                var_py = var_ref.split('.')[-1].replace('-', '_')
            if var_py not in input_variables:
                input_variables.append(var_py)
            return var_py, input_variables

        # 3.3 普通字符串
        # 检查是否是数字字符串（整数或浮点数）
        # 如果参数本身是数字（不带引号），应该保持为数字
        # 例如: "1" -> 1, "3.14" -> 3.14
        # 但如果是带引号的字符串（如 "N/A"），应该保持为字符串
        stripped = value.strip()
        if stripped:  # 非空字符串
            try:
                # 尝试解析为整数
                int(stripped)
                return stripped, input_variables
            except ValueError:
                try:
                    # 尝试解析为浮点数
                    float(stripped)
                    return stripped, input_variables
                except ValueError:
                    # 不是数字，作为普通字符串处理
                    escaped = value.replace('\\', '\\\\').replace('"', '\\"')
                    return f'"{escaped}"', input_variables
        else:
            # 空字符串
            return '""', input_variables

    # 其他类型直接 str
    return str(value), input_variables


# --------- 4. 参数拆分（与原代码相同，零改动） ---------
def _split_function_args(args_str: str) -> List[str]:
    """按逗号分割，但跳过嵌套括号内的逗号"""
    args, current, depth = [], '', 0
    for ch in args_str:
        if ch == '(':
            depth += 1
            current += ch
        elif ch == ')':
            depth -= 1
            current += ch
        elif ch == ',' and depth == 0:
            args.append(current.strip())
            current = ''
        else:
            current += ch
    if current.strip():
        args.append(current.strip())
    return args


# --------- 5. 测试示例 ---------
if __name__ == '__main__':
    print("=" * 60)
    print("Dialogflow 值解析器测试示例")
    print("=" * 60)
    
    # 测试用例列表 - 覆盖所有40个Dialogflow系统函数
    test_cases = [
        # ========== 基础类型 ==========
        ("普通字符串", "Hello World"),
        ("数字", 123),
        ("浮点数", 3.14),
        ("布尔值", True),
        ("None值", None),
        ("列表", [1, 2, 3]),
        ("字典", {"name": "test", "value": 100}),
        
        # ========== 变量引用 ==========
        ("简单变量", "$user_name"),
        ("嵌套变量", "$session.params.user_name"),
        ("用户输入", "$request.user-utterance"),
        
        # ========== 数学函数 (5个) ==========
        ("ADD-加法(2参)", "$sys.func.ADD(10, 20)"),
        ("ADD-加法(多参)", "$sys.func.ADD(1, 2, 3, 4)"),
        ("MINUS-减法", "$sys.func.MINUS(100, 30)"),
        ("MULTIPLY-乘法(2参)", "$sys.func.MULTIPLY(5, 6)"),
        ("MULTIPLY-乘法(多参)", "$sys.func.MULTIPLY(2, 3, 4)"),
        ("DIVIDE-除法(2参)", "$sys.func.DIVIDE(100, 3)"),
        ("DIVIDE-除法(3参)", "$sys.func.DIVIDE(10, 3, 2)"),
        ("ROUND-四舍五入(单参)", "$sys.func.ROUND(3.14159)"),
        ("ROUND-四舍五入(双参)", "$sys.func.ROUND(3.14159, 2)"),
        
        # ========== 日期时间函数 (5个) ==========
        ("NOW-当前时间", "$sys.func.NOW()"),
        ("ADD_DATE-日期加减", "$sys.func.ADD_DATE($date_obj, 5, 'DAYS')"),
        ("FORMAT_DATE-日期格式化(2参)", "$sys.func.FORMAT_DATE($date_obj, 'yyyy-MM-dd')"),
        ("FORMAT_DATE-日期格式化(3参)", "$sys.func.FORMAT_DATE($date_obj, 'yyyy-MM-dd', 'en')"),
        ("IS_FUTURE_DATE-未来日期", "$sys.func.IS_FUTURE_DATE($date_obj)"),
        ("IS_PAST_DATE-过去日期", "$sys.func.IS_PAST_DATE($date_obj)"),
        
        # ========== 字符串函数 (9个) ==========
        ("CONCATENATE-字符串连接(2参)", "$sys.func.CONCATENATE($first_name, $last_name)"),
        ("CONCATENATE-字符串连接(多参)", "$sys.func.CONCATENATE('a', 'b', 'c', 'd')"),
        ("LEN-字符串长度", "$sys.func.LEN($text)"),
        ("LOWER-转小写", "$sys.func.LOWER($text)"),
        ("UPPER-转大写", "$sys.func.UPPER($text)"),
        ("MID-提取子串", "$sys.func.MID('google', 4, 2)"),
        ("SUBSTITUTE-替换", "$sys.func.SUBSTITUTE('good', 'd', 'gle')"),
        ("SPLIT-分割", "$sys.func.SPLIT('a/b/c', '/')"),
        ("JOIN-连接(2参)", "$sys.func.JOIN(', ', $list)"),
        ("JOIN-连接(3参)", "$sys.func.JOIN(', ', $list, ', and ')"),
        ("URL_ENCODE-URL编码", "$sys.func.URL_ENCODE($search_query)"),
        
        # ========== 列表函数 (7个) ==========
        ("APPEND-追加(2参)", "$sys.func.APPEND($list1, $item)"),
        ("APPEND-追加(多参)", "$sys.func.APPEND($list1, 3, $list2)"),
        ("REMOVE-移除(2参)", "$sys.func.REMOVE($list, $item)"),
        ("REMOVE-移除(多参)", "$sys.func.REMOVE($list, 2, $items)"),
        ("COUNT-列表长度", "$sys.func.COUNT($items)"),
        ("UNIQUE-去重", "$sys.func.UNIQUE($list)"),
        ("CONTAIN-包含", "$sys.func.CONTAIN($item, $list)"),
        ("MATCH-查找索引", "$sys.func.MATCH($list, $item)"),
        ("GET-获取元素", "$sys.func.GET($list, 0)"),
        
        # ========== 对象操作函数 (3个) ==========
        ("GET_FIELD-获取字段", "$sys.func.GET_FIELD($obj, 'key')"),
        ("IDENTITY-保持类型", "$sys.func.IDENTITY($param)"),
        ("FILTER-过滤", "$sys.func.FILTER(\"$session.params.shapes\", \"$.result.shapes[:1]\")"),
        ("NESTED_FIELD-嵌套字段(2参)", "$sys.func.NESTED_FIELD(\"$session.params.obj\", 'a')"),
        ("NESTED_FIELD-嵌套字段(多参)", "$sys.func.NESTED_FIELD(\"$session.params.obj\", 'a', 'b', 'c')"),
        ("NESTED_FIELD-数组索引", "$sys.func.NESTED_FIELD(\"$session.params.obj\", 'a', 'c[0]')"),
        
        # ========== 类型转换函数 (4个) ==========
        ("TO_TEXT-转字符串", "$sys.func.TO_TEXT(123)"),
        ("TO_NUMBER-转数字", "$sys.func.TO_NUMBER('-3')"),
        ("TO_OBJECT-转对象", "$sys.func.TO_OBJECT('{\"name\": \"test\"}')"),
        ("TO_PHONE_NUMBER-转电话(1参)", "$sys.func.TO_PHONE_NUMBER('650-206-5555')"),
        ("TO_PHONE_NUMBER-转电话(2参)", "$sys.func.TO_PHONE_NUMBER('650-206-5555', 'US')"),
        
        # ========== 校验函数 (3个) ==========
        ("IS_DATE-日期验证(2参)", "$sys.func.IS_DATE('2021-04-29', 'uuuu-MM-dd')"),
        ("IS_DATE-日期验证(3参)", "$sys.func.IS_DATE('21 Apr 29', 'uu MMM dd', 'en')"),
        ("IS_PHONE_NUMBER-电话验证(1参)", "$sys.func.IS_PHONE_NUMBER('650-206-5555')"),
        ("IS_PHONE_NUMBER-电话验证(2参)", "$sys.func.IS_PHONE_NUMBER('650-206-5555', 'US')"),
        ("IS_CREDIT_CARD_NUMBER-卡号验证", "$sys.func.IS_CREDIT_CARD_NUMBER('4111111111111111')"),
        
        # ========== 生成函数 (2个) ==========
        ("RAND-随机数", "$sys.func.RAND()"),
        ("UUID-UUID生成", "$sys.func.UUID()"),
        
        # ========== 逻辑函数 (1个) ==========
        ("IF-条件判断", "$sys.func.IF($condition, $true_value, $false_value)"),
        # 使用单引号避免转义问题，实际JSON中的格式是: "$sys.func.IF(\"$session.params.PAYMENTDUEDATE=null\", \"N/A\", $session.params.PAYMENTDUEDATE)"
        ("IF-简单条件表达式", '$sys.func.IF("1 < 2", 1, 2)'),
        ("IF-null检查-PAYMENTDUEDATE", '$sys.func.IF("$session.params.PAYMENTDUEDATE=null", "N/A", $session.params.PAYMENTDUEDATE)'),
        ("IF-null检查-AVAILABLECREDITLIMIT", '$sys.func.IF("$session.params.AVAILABLECREDITLIMIT=null", "N/A", $session.params.AVAILABLECREDITLIMIT)'),
        ("IF-null检查-嵌套路径", '$sys.func.IF("$session.params.CB_CardInfo.PAYMENTDUEDATE=null", "N/A", $session.params.CB_CardInfo.PAYMENTDUEDATE)'),
        ("IF-null检查-空字符串", '$sys.func.IF(\"$session.params.PAYMENTDUEDATE=null\", \"N/A\", $session.params.PAYMENTDUEDATE)'),
        
        # ========== 嵌套函数 ==========
        ("嵌套函数-复杂", "$sys.func.ADD($sys.func.MULTIPLY(2, 3), $sys.func.MINUS(10, 4))"),
        ("嵌套函数-多层", "$sys.func.CONCATENATE($sys.func.UPPER($first), $sys.func.LOWER($last))"),
        ("GET_FIELD-获取字段", "$sys.func.GET_FIELD($session.params.PE_SegmentNumber.CardServicing.CustomerSegment.segmentNumberText, $session.params.customerSegment)")
    ]
    
    # 运行测试
    for test_name, test_value in test_cases:
        print(f"\n【测试】{test_name}")
        print(f"输入: {test_value}")
        
        input_vars = []
        try:
            code, input_vars = parse_dialogflow_value(test_value, input_vars)
            print(f"生成的Python代码: {code}")
            # if input_vars:
            #     print(f"需要的输入变量: {input_vars}")
        except Exception as e:
            print(f"❌ 错误: {e}")
    
    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)