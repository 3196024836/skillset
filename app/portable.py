"""
路径通用化：云端库里存占位符，装到本机时换成这台电脑的真实路径。

不同电脑的盘符、用户名不一样（例如公司电脑没有 D 盘），skill、规则、MCP 里写死的
D:\\Projects、C:\\Users\\tj 拿到别的电脑上就用不了。所以：
    上传（存进库）时 collapse：本机路径 → {{PROJECTS}} / {{SOFTWARE}} / {{HOME}}
    安装（写给 AI）时 expand：占位符 → 本机路径
AI 读到的永远是本机真实路径，看不到占位符。

同一个路径常见四种写法，各用一种占位符，展开时还原成同样的写法：
    D:\\Projects      {{PROJECTS}}
    D:/Projects      {{PROJECTS:/}}
    /d/Projects      {{PROJECTS:sh}}      （Git Bash）
    D:\\\\Projects     {{PROJECTS:\\\\}}     （JSON 等转义过的反斜杠）
"""

import ctypes
import os
import re
import string

import config as cfg

# 可以通用化的路径：名字 → 说明
Names = {"PROJECTS": "项目目录", "SOFTWARE": "软件目录", "HOME": "用户主目录"}
# 写法标记：没有标记表示 Windows 反斜杠写法
Forms = ("", "/", "sh", "\\\\")
TokenPattern = re.compile(r"\{\{(PROJECTS|SOFTWARE|HOME)(?::(/|sh|\\\\))?\}\}")

_cache = {"values": None, "pattern": None, "lookup": None}


def _default_drive() -> str:
    """项目和软件默认放的盘：C 盘之外第一个本地硬盘，没有就 C 盘。

    :returns: 例如 "D:"
    """
    if os.name == "nt":
        for letter in string.ascii_uppercase[3:]:
            # 3 = DRIVE_FIXED（本地硬盘），排除 U 盘、光驱、网络盘
            if ctypes.windll.kernel32.GetDriveTypeW(f"{letter}:\\") == 3:
                return f"{letter}:"
    return "C:"


def defaults() -> dict:
    """本机路径的默认值。

    :returns: {名字: 路径}
    """
    drive = _default_drive()
    return {"PROJECTS": f"{drive}\\Projects", "SOFTWARE": f"{drive}\\SoftWare", "HOME": str(cfg.home_dir())}


def values() -> dict:
    """本机实际用的路径：设置里填过的优先，没填用默认值。

    :returns: {名字: 路径}
    """
    if _cache["values"] is None:
        saved = cfg.load_config().get("paths") or {}
        merged = defaults()
        merged.update({name: str(value).rstrip("\\/") for name, value in saved.items() if name in Names and value})
        _cache["values"] = merged
    return _cache["values"]


def reload() -> None:
    """设置改了以后清掉缓存。"""
    _cache.update(values=None, pattern=None, lookup=None)


def _render(path: str, form: str) -> str:
    """把 Windows 路径写成某种写法。

    :param path: 例如 D:\\Projects
    :param form: Forms 里的一种
    :returns: 对应写法的路径
    """
    if form == "/":
        return path.replace("\\", "/")
    if form == "sh":
        return "/" + path[0].lower() + path[2:].replace("\\", "/") if re.match(r"^[A-Za-z]:", path) else path.replace("\\", "/")
    if form == "\\\\":
        return path.replace("\\", "\\\\")
    return path


def _compile(table: dict) -> tuple:
    """按路径表生成一个大正则：长的路径排前面，避免 C:\\Users\\tj 抢走 C:\\Users\\tj\\Projects。

    :param table: {名字: 路径}
    :returns: (正则, {小写写法: 占位符})
    """
    lookup = {}
    for name, path in table.items():
        if not re.match(r"^[A-Za-z]:\\.+", path):
            continue
        for form in Forms:
            token = "{{" + name + (":" + form if form else "") + "}}"
            lookup.setdefault(_render(path, form).lower(), token)
    if not lookup:
        return None, lookup
    alternatives = "|".join(re.escape(text) for text in sorted(lookup, key=len, reverse=True))
    # 前后不能紧挨着字母数字，免得把 D:\Projects2 当成 D:\Projects
    return re.compile(rf"(?<![\w])(?:{alternatives})(?![\w-])", re.IGNORECASE), lookup


def collapse(text: str, table: dict = None) -> str:
    """本机路径换成占位符（存进库之前用）。

    :param text: 原文
    :param table: 用别的路径表（例如迁移另一台电脑写进来的内容）；默认本机
    :returns: 换好的文本
    """
    if table is None:
        if _cache["pattern"] is None:
            _cache["pattern"], _cache["lookup"] = _compile(values())
        pattern, lookup = _cache["pattern"], _cache["lookup"]
    else:
        pattern, lookup = _compile(table)
    if pattern is None or not text:
        return text
    return pattern.sub(lambda match: lookup[match.group(0).lower()], text)


def expand(text: str) -> str:
    """占位符换成本机路径（写给 AI 之前用）。

    :param text: 库里的文本
    :returns: 换好的文本
    """
    if not text or "{{" not in text:
        return text
    table = values()
    return TokenPattern.sub(lambda match: _render(table[match.group(1)], match.group(2) or ""), text)


def _walk(value, change):
    """对 dict / list 里的所有字符串做同一个转换（MCP 配置用）。"""
    if isinstance(value, str):
        return change(value)
    if isinstance(value, dict):
        return {key: _walk(item, change) for key, item in value.items()}
    if isinstance(value, list):
        return [_walk(item, change) for item in value]
    return value


def collapse_obj(value):
    """:returns: 所有字符串都 collapse 过的副本"""
    return _walk(value, collapse)


def expand_obj(value):
    """:returns: 所有字符串都 expand 过的副本"""
    return _walk(value, expand)


def convert_bytes(data: bytes, change) -> bytes:
    """文本文件按 UTF-8 转换，二进制文件（含 NUL 或不是 UTF-8）原样返回。

    :param data: 文件内容
    :param change: collapse 或 expand
    :returns: 转换后的内容
    """
    if b"\0" in data:
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    result = change(text)
    return data if result == text else result.encode("utf-8")
