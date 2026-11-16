"""
库：云端 git 仓库在本机的副本，是所有 skill 的"正本"。

目录结构：
    library/
        manifest.json        清单：中文名、介绍、分组、启用状态、插件记录
        skills/<英文名>/      skill 正本

本类只管清单读写和 git，不碰各个 AI 的目录。
"""

import json
import subprocess
from pathlib import Path

# 清单格式版本
ManifestVersion = 1
# git 子进程超时（秒），网络操作可能较慢
GitTimeout = 120


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
        return data

    def save(self, manifest: dict) -> None:
        """写回清单，键排序保证 git diff 稳定。

        :param manifest: 清单字典
        """
        manifest = {"version": ManifestVersion, "groups": [], "skills": {}, "plugins": {},
                    "marketplaces": {}, **manifest}
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
        result = subprocess.run(["git", *args], cwd=self.path, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=GitTimeout)
        if check and result.returncode != 0:
            raise GitError((result.stderr or result.stdout).strip() or f"git {args[0]} 失败")
        return result.stdout.strip()

    def remote_url(self) -> str:
        """返回 origin 远程地址，没配置时返回空字符串。

        :returns: 远程地址
        """
        return self.git("remote", "get-url", "origin", check=False)

    def set_remote(self, url: str) -> None:
        """设置或清除 origin 远程地址。

        :param url: 远程仓库地址；空字符串表示删除远程
        """
        current = self.remote_url()
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
        self.git("add", "-A")
        if not self.git("status", "--porcelain"):
            return False
        self.git("commit", "-m", message)
        return True

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
                                text=True, encoding="utf-8", errors="replace", timeout=GitTimeout)
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
        info = {"remote": remote, "dirty": bool(self.git("status", "--porcelain", check=False)),
                "ahead": 0, "behind": 0, "hasCommits": self.has_commits()}
        if remote and info["hasCommits"]:
            counts = self.git("rev-list", "--left-right", "--count", "HEAD...origin/main", check=False)
            if counts:
                info["ahead"], info["behind"] = (int(part) for part in counts.split())
            elif info["hasCommits"]:
                # 云端还没有 main 分支：本地全部提交都算未推送
                info["ahead"] = int(self.git("rev-list", "--count", "HEAD", check=False) or 0)
        return info
