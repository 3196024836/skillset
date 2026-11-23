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
# git push 因云端有本机没有的提交而被拒时，stderr 里会出现这个词
RejectedMark = "[rejected]"
# 三方合并里表示"这一项不存在"
Missing = object()


def merge3(base, ours, theirs):
    """三方合并两份改动：只有一边改了取改了的那边，两边都改且不同时取本机（ours）。

    字典按键递归合并；带 id 的对象列表（如分组）按 id 合并，顺序以本机为准、云端新加的排在后面。
    任一参数可以是 Missing，表示那一边没有这一项。

    :returns: 合并结果，可能是 Missing（被删掉）
    """
    if ours == theirs or theirs == base:
        return ours
    if ours == base:
        return theirs
    if _is_id_list(ours) and _is_id_list(theirs) and (base is Missing or _is_id_list(base)):
        merged = merge3({} if base is Missing else _by_id(base), _by_id(ours), _by_id(theirs))
        order = [item["id"] for item in ours] + [item["id"] for item in theirs if item["id"] not in _by_id(ours)]
        return [merged[key] for key in order if key in merged]
    if isinstance(ours, dict) and isinstance(theirs, dict) and (base is Missing or isinstance(base, dict)):
        base = {} if base is Missing else base
        merged = {}
        for key in {**base, **theirs, **ours}:
            value = merge3(base.get(key, Missing), ours.get(key, Missing), theirs.get(key, Missing))
            if value is not Missing:
                merged[key] = value
        return merged
    return ours


def _is_id_list(value) -> bool:
    """是不是每项都带 id 的对象列表。"""
    return isinstance(value, list) and all(isinstance(item, dict) and "id" in item for item in value)


def _by_id(items: list) -> dict:
    """对象列表转成 id → 对象。"""
    return {item["id"]: item for item in items}


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
        """推送到云端。云端有另一台电脑推上去的新提交时，先合并再推。

        :returns: git 输出
        """
        if not self.remote_url():
            raise GitError("还没有设置云端仓库地址")
        result = self._push()
        if result.returncode != 0 and RejectedMark in result.stderr:
            self.pull()
            result = self._push()
        if result.returncode != 0:
            raise GitError(result.stderr.strip())
        return (result.stderr or result.stdout).strip()

    def _push(self) -> subprocess.CompletedProcess:
        """执行一次 git push，不检查结果。"""
        return subprocess.run(["git", "push", "-u", "origin", "HEAD"], cwd=self.path, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=GitTimeout,
                              env=git_env(("push",), GitEnv))

    def pull(self) -> str:
        """从云端拉取最新内容。两台电脑各自改过（分叉）时自动合并，同一处都改了以本机为准。

        :returns: 给用户看的结果
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
        # 本机有没提交的改动时先提交，否则合并会拒绝
        self.commit("保存本机改动")
        ahead, behind = (int(part) for part in self.git("rev-list", "--left-right", "--count", "HEAD...origin/main").split())
        if not behind:
            return "已是最新"
        if not ahead:
            self.git("merge", "--ff-only", "origin/main")
            return "已从云端更新"
        self._merge_remote()
        return f"已合并云端的 {behind} 个新提交（和本机的改动冲突时以本机为准）"

    def _merge_remote(self) -> None:
        """合并分叉的 origin/main：文件冲突取本机版本，清单按条目三方合并，两边各自加的都保留。"""
        base = self.git("merge-base", "HEAD", "origin/main")
        result = self._run("merge", "--no-ff", "--no-commit", "-X", "ours", "origin/main")
        if not (self.path / ".git" / "MERGE_HEAD").exists():
            raise GitError((result.stderr or result.stdout).strip() or "合并云端失败")
        try:
            # -X ours 解决不了"一边删、一边改"的冲突：本机还有就留本机的，本机删了就删
            for path in self.git("diff", "--name-only", "--diff-filter=U").splitlines():
                if path == "manifest.json":
                    continue
                if self._run("cat-file", "-e", f"HEAD:{path}").returncode == 0:
                    self.git("checkout", "--ours", "--", path)
                    self.git("add", "--", path)
                else:
                    self.git("rm", "-q", "--", path)
            manifest = merge3(*(self._manifest_at(rev) for rev in (base, "HEAD", "origin/main")))
            self.save({} if manifest is Missing else manifest)
            self.git("add", "manifest.json")
            self.git("commit", "-m", "合并云端改动")
        except Exception:
            self._run("merge", "--abort")
            raise

    def _manifest_at(self, rev: str) -> dict:
        """读某个提交里的清单，没有时返回空字典。"""
        result = self._run("show", f"{rev}:manifest.json")
        return json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else {}

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
