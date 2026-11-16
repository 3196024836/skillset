"""
Claude Code 插件管理：一律通过官方 `claude plugin ...` 命令操作，不直接改插件文件。

插件只能整包启用/禁用，包里的 skill 只读展示。
"""

import json
import shutil
import subprocess
from pathlib import Path

from skillfs import list_skills, read_meta

# 插件命令超时（秒），安装可能要下载
ClaudeTimeout = 600


class PluginError(Exception):
    """插件命令失败时抛出。"""


def claude_exe() -> str:
    """找到 claude 命令的完整路径。

    :returns: 可执行文件路径
    """
    exe = shutil.which("claude")
    if not exe:
        raise PluginError("找不到 claude 命令，请确认 Claude Code 已安装并在 PATH 里")
    # npm 装的是 .cmd 包装脚本，参数要经过 cmd.exe 转义，JSON 参数会被弄乱；有原生 exe 就直接用
    native = Path(exe).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    if exe.lower().endswith(".cmd") and native.is_file():
        return str(native)
    return exe


def run_claude(*args: str) -> str:
    """执行一条 claude 命令。

    :param args: 参数，例如 ("plugin", "list", "--json")
    :returns: 标准输出
    """
    result = subprocess.run([claude_exe(), *args], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=ClaudeTimeout)
    if result.returncode != 0:
        raise PluginError((result.stderr or result.stdout).strip() or f"claude {' '.join(args)} 失败")
    return result.stdout


def run_json(*args: str):
    """执行 claude 命令并解析 JSON 输出。

    :param args: 参数
    :returns: 解析后的对象
    """
    output = run_claude(*args).strip()
    # 部分命令输出一行结果 JSON，取最后一行非空内容
    if not output.startswith(("[", "{")):
        output = output.splitlines()[-1] if output else "null"
    return json.loads(output)


def list_installed() -> list:
    """列出已安装插件，并附上每个插件包含的 skill。

    :returns: [{"id", "version", "scope", "enabled", "installPath", "skills": [{"id", "description"}]}]
    """
    plugins = run_json("plugin", "list", "--json")
    for plugin in plugins:
        # 插件 skill 在安装目录的 skills/ 下
        skillsRoot = Path(plugin.get("installPath", "")) / "skills"
        plugin["skills"] = [{"id": skillId, "description": read_meta(path)["description"]}
                            for skillId, path in list_skills(skillsRoot).items()]
    return plugins


def list_available() -> list:
    """列出已添加市场里可安装的插件。

    :returns: [{"pluginId", "name", "description", "marketplaceName"}]
    """
    return run_json("plugin", "list", "--json", "--available").get("available", [])


def list_marketplaces() -> list:
    """列出已添加的插件市场。

    :returns: [{"name", "source", "repo"/"url"/"path", ...}]
    """
    return run_json("plugin", "marketplace", "list", "--json")


def marketplace_source(market: dict) -> str:
    """从市场信息里取出可以重新 add 的来源字符串。

    :param market: list_marketplaces 返回的一项
    :returns: owner/repo、git 地址或本地路径
    """
    return market.get("repo") or market.get("url") or market.get("path") or ""


def action(verb: str, pluginId: str) -> dict:
    """对插件执行 install / uninstall / enable / disable / update。

    :param verb: 动作名
    :param pluginId: 插件 id，形如 name@marketplace
    :returns: 命令的 JSON 结果
    """
    return run_json("plugin", verb, pluginId, "-s", "user", "--json")


def update_marketplaces() -> None:
    """更新所有插件市场的目录，之后再更新插件才能拿到新版本。"""
    run_claude("plugin", "marketplace", "update")


def add_marketplace(source: str) -> dict:
    """添加插件市场。

    :param source: owner/repo、git 地址或本地路径
    :returns: 命令的 JSON 结果
    """
    return run_json("plugin", "marketplace", "add", source, "--json")
