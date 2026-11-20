"""
库：云端 git 仓库在本机的副本，是所有 skill 的"正本"。

目录结构：
    library/
        manifest.json        清单：中文名、介绍、分组、启用状态、插件记录、MCP 定义
        skills/<英文名>/      skill 正本
        rules/global.md      全局 AI 规则正本（同步成 CLAUDE.md、AGENTS.md、GEMINI.md）

本类只管清单读写和 git，不碰各个 AI 的目录。
"""

import json
import os
import subprocess
from pathlib import Path

from netproxy import git_env
from skillfs import SkipNames

# 清单格式版本
ManifestVersion = 1
# git 子进程超时（秒），网络操作可能较慢
GitTimeout = 120
# 只读命令（如 status）不去抢 index.lock，避免和其他同时读这个仓库的程序冲突
GitEnv = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}


class GitError(Exception):
    """git 命令失败时抛出，消息里带 git 的原始输出。"""


class Library:
    """管理库目录：清单 + skills 正本 + git 操作。

    例：
        lib = Library(Path("D:/Projects/skill-manager/library"))
        lib.ensure()
        manifest = lib.load()
    """

    def __init__(self, path: Path):
        """绑定库目录。

        :param path: 库根目录
        """
        # 库根目录
        self.path = Path(path)
        # skill 正本所在目录
        self.skillsDir = self.path / "skills"
        # 清单文件
        self.manifestPath = self.path / "manifest.json"
        # 全局规则正本
        self.rulesPath = self.path / "rules" / "global.md"
        # 远程地址缓存（None 表示还没读过）；只有 set_remote 会改它
        self._remote = None

    def ensure(self) -> None:
        """确保库目录、清单、git 仓库都存在；第一次运行时初始化。"""
        self.skillsDir.mkdir(parents=True, exist_ok=True)
        if not self.manifestPath.is_file():
            self.save({})
        if not (self.path / ".git").exists():
            self.git("init", "-b", "main")
            # 保持文件字节原样，避免换行转换导致指纹对不上
            self.git("config", "core.autocrlf", "false")
            # 中文文件名按原样显示
            self.git("config", "core.quotepath", "false")

    def load(self) -> dict:
        """读取清单，缺失的部分补齐成空结构。

        :returns: 清单字典
        """
        data = {}
        if self.manifestPath.is_file():
            data = json.loads(self.manifestPath.read_text(encoding="utf-8"))
        data.setdefault("version", ManifestVersion)
        data.setdefault("groups", [])
        data.setdefault("skills", {})
        data.setdefault("plugins", {})
        data.setdefault("marketplaces", {})
        data.setdefault("mcps", {})
        return data

    def save(self, manifest: dict) -> None:
        """写回清单，键排序保证 git diff 稳定。

        :param manifest: 清单字典
        """
        manifest = {"version": ManifestVersion, "groups": [], "skills": {}, "plugins": {},
                    "marketplaces": {}, "mcps": {}, **manifest}
        self.path.mkdir(parents=True, exist_ok=True)
        text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self.manifestPath.write_text(text, encoding="utf-8", newline="\n")

    def skill_path(self, skillId: str) -> Path:
        """返回某个 skill 正本的路径。

        :param skillId: skill 英文名（文件夹名）
        :returns: 路径（不保证存在）
        """
        return self.skillsDir / skillId

    def git(self, *args: str, check: bool = True) -> str:
        """在库目录执行一条 git 命令。

        :param args: git 参数
        :param check: 失败时是否抛 GitError
        :returns: 标准输出（去掉首尾空白）
        """
        result = self._run(*args)
        if check and result.returncode != 0:
            raise GitError((result.stderr or result.stdout).strip() or f"git {' '.join(args[:2])} 失败（退出码 {result.returncode}）")
        return result.stdout.strip()

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        """执行 git 并返回完整结果，不检查退出码。

        :param args: git 参数
        :returns: CompletedProcess
        """
        return subprocess.run(["git", *args], cwd=self.path, capture_output=True, text=True, env=git_env(args, GitEnv),
                              encoding="utf-8", errors="replace", timeout=GitTimeout)

    def remote_url(self) -> str:
        """返回 origin 远程地址，没配置时返回空字符串。

        :returns: 远程地址
        """
        if self._remote is None:
            self._remote = self.git("remote", "get-url", "origin", check=False)
        return self._remote

    def set_remote(self, url: str) -> None:
        """设置或清除 origin 远程地址。

        :param url: 远程仓库地址；空字符串表示删除远程
        """
        self._remote = None
        current = self.remote_url()
        self._remote = None
        if not url:
            if current:
                self.git("remote", "remove", "origin")
        elif current:
            self.git("remote", "set-url", "origin", url)
        else:
            self.git("remote", "add", "origin", url)

    def commit(self, message: str) -> bool:
        """提交库里的所有改动。

        :param message: 提交说明
        :returns: 有改动并已提交返回 True，没有改动返回 False
        """
        self._exclude_runtime()
        self.git("add", "-A")
        # 暂存区和 HEAD 一样（退出码 0）说明没有改动；还没有提交过时 HEAD 不存在，直接提交
        if self.has_commits() and self._run("diff", "--cached", "--quiet").returncode == 0:
            return False
        self.git("commit", "-m", message)
        return True

    def _exclude_runtime(self) -> None:
        """把 skill 的运行环境、缓存写进本地排除列表，免得被提交到库里。"""
        exclude = self.path / ".git" / "info" / "exclude"
        if not exclude.parent.is_dir():
            return
        text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        missing = [name for name in sorted(SkipNames) if name != ".git" and name not in text.splitlines()]
        if missing:
            exclude.write_text(text.rstrip("\n") + "\n" + "\n".join(missing) + "\n", encoding="utf-8")

    def has_commits(self) -> bool:
        """判断仓库是否已有提交。

        :returns: 有提交返回 True
        """
        return bool(self.git("rev-parse", "--verify", "HEAD", check=False))

    def push(self) -> str:
        """推送到云端。

        :returns: git 输出
        """
        if not self.remote_url():
            raise GitError("还没有设置云端仓库地址")
        result = subprocess.run(["git", "push", "-u", "origin", "HEAD"], cwd=self.path, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=GitTimeout,
                                env=git_env(("push",), GitEnv))
        if result.returncode != 0:
            raise GitError(result.stderr.strip())
        return (result.stderr or result.stdout).strip()

    def pull(self) -> str:
        """从云端拉取最新内容（只允许快进，有分叉时报错让用户处理）。

        :returns: git 输出
        """
        if not self.remote_url():
            raise GitError("还没有设置云端仓库地址")
        self.git("fetch", "origin")
        # 远程分支还是空仓库时不需要合并
        remoteHead = self.git("rev-parse", "--verify", "origin/main", check=False)
        if not remoteHead:
            return "云端还是空的"
        if not self.has_commits():
            self.git("reset", "--hard", "origin/main")
            self.git("branch", "--set-upstream-to=origin/main")
            return "已从云端下载"
        return self.git("merge", "--ff-only", "origin/main") or "已是最新"

    def status(self) -> dict:
        """汇总库的 git 状态，给界面显示。

        :returns: {"remote", "dirty", "ahead", "behind", "hasCommits"}
        """
        remote = self.remote_url()
        # 一次 git status 拿到：有没有提交、有没有未提交改动、和上游相差几个提交
        lines = self.git("status", "--porcelain=v2", "--branch", check=False).splitlines()
        head = next((line for line in lines if line.startswith("# branch.oid ")), "")
        info = {"remote": remote, "dirty": any(not line.startswith("#") for line in lines),
                "ahead": 0, "behind": 0, "hasCommits": bool(head) and not head.endswith("(initial)")}
        if remote and info["hasCommits"]:
            upstream = next((line.split() for line in lines if line.startswith("# branch.ab ")), None)
            counts = upstream and f"{upstream[2][1:]} {upstream[3][1:]}"
            counts = counts or self.git("rev-list", "--left-right", "--count", "HEAD...origin/main", check=False)
            if counts:
                info["ahead"], info["behind"] = (int(part) for part in counts.split())
            else:
                # 云端还没有 main 分支：本地全部提交都算未推送
                info["ahead"] = int(self.git("rev-list", "--count", "HEAD", check=False) or 0)
        return info
