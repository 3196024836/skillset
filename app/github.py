"""
连接云端仓库的辅助功能：检查 git / GitHub CLI 是否可用、登录状态、一键建私有仓库。

没有 GitHub CLI 也能用：用户直接填仓库地址，推送时由 Git Credential Manager 弹出登录。
"""

import json
import shutil
import subprocess
import sys

# 子进程超时（秒）
CommandTimeout = 60


def _run(*args: str) -> subprocess.CompletedProcess:
    """执行命令并收集输出，不抛异常。

    :param args: 命令和参数
    :returns: 执行结果
    """
    return subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=CommandTimeout)


def status() -> dict:
    """汇总连接云端需要的环境状态。

    :returns: {"git", "gitUser", "gh", "ghUser"}
    """
    info = {"git": bool(shutil.which("git")), "gitUser": "", "gh": bool(shutil.which("gh")), "ghUser": ""}
    if info["git"]:
        info["gitUser"] = _run("git", "config", "--global", "user.name").stdout.strip()
    if info["gh"]:
        result = _run("gh", "api", "user", "--jq", ".login")
        if result.returncode == 0:
            info["ghUser"] = result.stdout.strip()
    return info


def open_login() -> None:
    """打开一个终端窗口运行 GitHub CLI 网页登录（用户主动点击才调用）。"""
    command = ["gh", "auth", "login", "--web", "--git-protocol", "https", "--hostname", "github.com"]
    if sys.platform == "win32":
        subprocess.Popen(["cmd", "/c", "start", "GitHub 登录", "cmd", "/k", *command])
    elif sys.platform == "darwin":
        script = " ".join(command)
        subprocess.Popen(["osascript", "-e", f'tell app "Terminal" to do script "{script}"'])
    else:
        subprocess.Popen(["x-terminal-emulator", "-e", *command])


def repo_exists(name: str) -> str:
    """查询当前登录账号下是否已有同名仓库。

    :param name: 仓库名
    :returns: 存在时返回 https 克隆地址，否则返回空字符串
    """
    result = _run("gh", "repo", "view", name, "--json", "url")
    if result.returncode != 0:
        return ""
    return json.loads(result.stdout)["url"] + ".git"


def create_private_repo(name: str) -> str:
    """在当前登录账号下创建私有仓库；已存在则直接返回它的地址。

    :param name: 仓库名
    :returns: https 克隆地址
    """
    existing = repo_exists(name)
    if existing:
        return existing
    result = _run("gh", "repo", "create", name, "--private", "--description", "My AI agent skills (managed by Skillset)")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout.strip().splitlines()[-1] + ".git"
