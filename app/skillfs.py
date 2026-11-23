"""
Skill 文件夹的底层文件操作：识别、读取元数据、计算内容指纹、复制、删除、密钥扫描。
只处理磁盘，不关心清单和 git。

例：
    metas = list_skills(Path("~/.claude/skills").expanduser())
    same = dir_hash(a) == dir_hash(b)
"""

import hashlib
import os
import re
import shutil
import stat
from pathlib import Path

# 复制和计算指纹时忽略的文件名。.runtime、.venv、venv、node_modules 是 skill 在本机装的运行环境，
# 能重建、体积大、路径常超过 Windows 长度上限，不跟着同步
SkipNames = {".git", "__pycache__", ".DS_Store", "Thumbs.db", ".runtime", ".venv", "venv", "node_modules"}

# 密钥特征：名称 -> 正则
SecretPatterns = {
    "OpenAI/Anthropic 风格 key": re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    "GitHub token": re.compile(r"\b(ghp|gho|ghs|ghu)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}"),
    "AWS Access Key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
    "私钥": re.compile(r"-----BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
}

# 扫描密钥时跳过的大文件阈值（字节）
SecretScanMaxBytes = 2 * 1024 * 1024


def is_skill_dir(path: Path) -> bool:
    """判断一个文件夹是不是 skill（含 SKILL.md，且不是隐藏文件夹）。

    :param path: 待判断的文件夹
    :returns: 是 skill 返回 True
    """
    return path.is_dir() and not path.name.startswith(".") and (path / "SKILL.md").is_file()


def list_skills(root: Path) -> dict:
    """列出某个目录下的所有 skill。

    :param root: skills 根目录
    :returns: {文件夹名: 路径}，目录不存在时返回空字典
    """
    if not root.is_dir():
        return {}
    return {child.name: child for child in sorted(root.iterdir()) if is_skill_dir(child)}


def read_meta(skillDir: Path) -> dict:
    """读取 SKILL.md 开头 YAML 里的 name 和 description。

    只解析顶层的 `key: value` 和 `>` / `|` 多行写法，够用即可，不引入 YAML 依赖。

    :param skillDir: skill 文件夹
    :returns: {"name": str, "description": str}，缺失的字段为空字符串
    """
    # 读取文本，坏编码不报错
    text = (skillDir / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    meta = {"name": "", "description": ""}
    if not lines or lines[0].strip() != "---":
        return meta

    # 截出 frontmatter 区块
    block = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        block.append(line)

    # 逐行解析顶层键，多行值收集后续缩进行
    index = 0
    while index < len(block):
        line = block[index]
        match = re.match(r"^([A-Za-z_][\w\-]*):\s*(.*)$", line)
        index += 1
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()

        # 收集后续缩进行：块写法（> |）的全部内容，或普通写法的换行续写
        parts = []
        while index < len(block) and (block[index].startswith((" ", "\t")) or not block[index].strip()):
            parts.append(block[index].strip())
            index += 1
        if value in ("", ">", "|", ">-", "|-", ">+", "|+"):
            value = " ".join(part for part in parts if part)
        elif parts:
            value = " ".join([value, *(part for part in parts if part)])
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key in meta:
            meta[key] = value
    return meta


def iter_files(root: Path):
    """按稳定顺序遍历文件夹内的文件，跳过忽略项（运行环境、缓存等）。

    :param root: 根文件夹
    :returns: 生成 (相对路径字符串, 绝对路径)
    """
    for dirPath, dirNames, fileNames in os.walk(root):
        # 原地剪掉忽略的子目录，并排序保证顺序稳定
        dirNames[:] = sorted(name for name in dirNames if name not in SkipNames)
        for fileName in sorted(fileNames):
            if fileName in SkipNames:
                continue
            fullPath = Path(dirPath) / fileName
            yield fullPath.relative_to(root).as_posix(), fullPath


def dir_hash(root: Path) -> str:
    """计算文件夹内容指纹，用来判断两份 skill 是否一样。

    换行符统一成 LF 再算，避免 git 自动转换换行造成假差异。

    :param root: skill 文件夹
    :returns: sha256 十六进制字符串；文件夹不存在时返回空字符串
    """
    if not root.is_dir():
        return ""
    digest = hashlib.sha256()
    for relPath, fullPath in iter_files(root):
        digest.update(relPath.encode("utf-8") + b"\0")
        digest.update(fullPath.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def dir_stamp(root: Path) -> str:
    """文件夹的轻量指纹：只看文件名、大小、修改时间，不读内容，用来快速发现改动。

    用 os.scandir 直接拿目录项里的大小和时间（Windows 上不用再逐个 stat），界面每几秒调一次也不费劲。

    :param root: skill 文件夹
    :returns: 每个文件一行的文本
    """
    lines = []
    pending = [str(root)]
    while pending:
        try:
            entries = sorted(os.scandir(pending.pop()), key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            if entry.name in SkipNames:
                continue
            try:
                if entry.is_dir():
                    pending.append(entry.path)
                    continue
                info = entry.stat()
            except OSError:
                continue
            lines.append(f"{entry.path}|{info.st_size}|{info.st_mtime_ns}")
    return "\n".join(lines)


def newest_mtime(root: Path) -> float:
    """返回文件夹里最新文件的修改时间，用来挑"最近改过的那份"。

    :param root: skill 文件夹
    :returns: 时间戳，空文件夹返回 0
    """
    return max((fullPath.stat().st_mtime for _, fullPath in iter_files(root)), default=0.0)


def remove_dir(path: Path) -> None:
    """删除一个 skill 文件夹；如果它是链接，只删链接本身，不碰链接指向的内容。

    :param path: 要删除的路径
    """
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        os.unlink(path) if path.is_symlink() else os.rmdir(path)
        return
    if not path.exists():
        return

    def onError(func, failedPath, _exc):
        # 只读文件（git 对象常见）先去掉只读属性再重试
        os.chmod(failedPath, stat.S_IWRITE)
        func(failedPath)

    shutil.rmtree(path, onexc=onError)


def copy_skill(source: Path, target: Path) -> None:
    """把 skill 复制到目标位置，替换目标原有内容；目标里本机的运行环境（SkipNames）保留不动。

    :param source: 源 skill 文件夹
    :param target: 目标 skill 文件夹
    """
    if source.resolve() == target.resolve():
        return
    if target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction()) or not target.is_dir():
        remove_dir(target)
    else:
        for child in target.iterdir():
            if child.name in SkipNames:
                continue
            remove_dir(child) if child.is_dir() and not child.is_symlink() else child.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(*SkipNames), dirs_exist_ok=True)


def scan_secrets(root: Path) -> list:
    """扫描 skill 里疑似密钥的内容，只报告位置和类型，不返回密钥原文。

    :param root: skill 文件夹
    :returns: [{"file": 相对路径, "line": 行号, "kind": 类型}]
    """
    findings = []
    for relPath, fullPath in iter_files(root):
        # .env 文件本身就不该上传
        if fullPath.name == ".env" or fullPath.name.startswith(".env."):
            if fullPath.name != ".env.example":
                findings.append({"file": relPath, "line": 0, "kind": ".env 文件"})
            continue
        if fullPath.stat().st_size > SecretScanMaxBytes:
            continue
        text = fullPath.read_bytes().decode("utf-8", errors="ignore")
        for lineNo, line in enumerate(text.splitlines(), 1):
            for kind, pattern in SecretPatterns.items():
                if pattern.search(line):
                    findings.append({"file": relPath, "line": lineNo, "kind": kind})
    return findings
