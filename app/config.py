"""
工具的本机配置：库（云端仓库的本地副本）放哪、远程地址、给哪些 AI 安装。
配置只存在本机 data/config.json，不进 git。

测试时可用环境变量隔离真实环境：
    SKILLMGR_HOME    代替用户主目录（AI 的 skill 目录都基于它）
    SKILLMGR_DATA    代替 data 目录
"""

import json
import os
from pathlib import Path

# 工具根目录（app 的上一级）
RootDir = Path(__file__).resolve().parent.parent
# AI 目标定义文件
AgentsPath = Path(__file__).resolve().parent / "agents.json"


def home_dir() -> Path:
    """返回用户主目录，可被 SKILLMGR_HOME 覆盖。

    :returns: 主目录路径
    """
    override = os.environ.get("SKILLMGR_HOME")
    return Path(override) if override else Path.home()


def data_dir() -> Path:
    """返回本机数据目录，可被 SKILLMGR_DATA 覆盖。

    :returns: 数据目录路径
    """
    override = os.environ.get("SKILLMGR_DATA")
    return Path(override) if override else RootDir / "data"


def expand_path(raw: str) -> Path:
    """把 agents.json 里的路径（支持 ~ 和 %VAR%）展开成绝对路径。

    :param raw: 原始路径字符串
    :returns: 展开后的路径
    """
    if raw.startswith("~"):
        return home_dir() / raw[1:].lstrip("/\\")
    return Path(os.path.expandvars(raw))


def load_config() -> dict:
    """读取本机配置，缺失的键用默认值补齐。

    :returns: 配置字典
    """
    config = {
        # 库目录：云端 git 仓库在本机的副本
        "libraryPath": str(data_dir() / "library" if "SKILLMGR_DATA" in os.environ else RootDir / "library"),
        # 远程仓库地址，空表示只在本机
        "remoteUrl": "",
        # 每次改动后是否自动推送到云端
        "autoPush": True,
        # 启用的 AI 目标 id 列表；None 表示按"本机装了哪个 AI"自动选
        "targets": None,
    }
    path = data_dir() / "config.json"
    if path.is_file():
        config.update(json.loads(path.read_text(encoding="utf-8")))
    return config


def save_config(config: dict) -> None:
    """保存本机配置。

    :param config: 配置字典
    """
    path = data_dir() / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def load_agents() -> list:
    """读取 AI 目标定义列表。

    :returns: [{"id", "name", "skillsDir", "detect", ...}]
    """
    return json.loads(AgentsPath.read_text(encoding="utf-8"))["agents"]
