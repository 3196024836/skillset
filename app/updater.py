"""
软件自身的更新：Skillset 是用 git 装的（克隆的仓库），更新就是 git fetch / git pull。

为了常驻省资源：启动 1 分钟后查一次，之后每 6 小时查一次，每次只跑一条 git fetch。
不是 git 装的（下载的压缩包）没法自动更新，只给出仓库地址让用户自己下。
"""

import subprocess
import sys
import threading
import time
from pathlib import Path

# 启动后多久第一次检查、之后多久查一次（秒）
FirstDelay = 60
CheckInterval = 6 * 3600
# 公开仓库，非 git 安装时提示去这里下载
RepoUrl = "https://github.com/3196024836/skillset"


class UpdateError(Exception):
    """更新失败，消息直接显示给用户。"""


class Updater:
    """检查并安装 Skillset 自身的更新。

    例：
        updater = Updater(RootDir)
        updater.start()            # 后台定期检查
        updater.check()            # 手动检查
        updater.pull()             # 更新代码（之后要重启才生效）
    """

    def __init__(self, root: Path):
        """:param root: Skillset 安装目录"""
        self.root = Path(root)
        self.lock = threading.Lock()
        # 界面显示用：是否 git 安装、落后几个提交、上次检查时间和错误
        self.status = {"git": (self.root / ".git").exists(), "behind": 0, "checkedAt": "", "error": "",
                       "current": "", "latest": "", "repo": RepoUrl}

    def _git(self, *args: str, timeout: int = 60) -> str:
        """在安装目录里运行 git，不弹控制台窗口。"""
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            done = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise UpdateError(f"运行 git 失败：{error}")
        if done.returncode != 0:
            raise UpdateError((done.stderr or done.stdout).strip().splitlines()[-1] if (done.stderr or done.stdout).strip()
                              else f"git {args[0]} 失败")
        return done.stdout.strip()

    def start(self) -> None:
        """开后台线程定期检查（只有 git 安装才需要）。"""
        if self.status["git"]:
            threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        """后台检查循环，出错只记下来，不打断。"""
        time.sleep(FirstDelay)
        while True:
            try:
                self.check()
            except UpdateError:
                pass
            time.sleep(CheckInterval)

    def check(self) -> dict:
        """从 GitHub 取最新信息，看本机落后几个提交。

        :returns: 更新状态
        """
        if not self.status["git"]:
            raise UpdateError(f"这份 Skillset 不是用 git 装的，没法自动更新。请到 {RepoUrl} 下载新版本")
        with self.lock:
            try:
                self._git("fetch", "--quiet")
                behind = int(self._git("rev-list", "--count", "HEAD..@{u}") or 0)
                self.status.update(behind=behind, error="",
                                   current=self._git("log", "-1", "--format=%h %s", "HEAD"),
                                   latest=self._git("log", "-1", "--format=%h %s", "@{u}"))
            except (UpdateError, ValueError) as error:
                self.status["error"] = str(error)
                raise UpdateError(f"检查更新失败：{error}")
            finally:
                self.status["checkedAt"] = time.strftime("%Y-%m-%d %H:%M")
            return dict(self.status)

    def pull(self) -> str:
        """拉取更新（只快进，不合并、不覆盖本机改动）。

        :returns: 更新说明
        """
        if not self.status["git"]:
            raise UpdateError(f"这份 Skillset 不是用 git 装的，没法自动更新。请到 {RepoUrl} 下载新版本")
        with self.lock:
            if self._git("status", "--porcelain", "--untracked-files=no"):
                raise UpdateError("Skillset 的代码在本机有改动，为了不覆盖它们，没有自动更新。请先提交或撤销这些改动")
            before = self._git("rev-parse", "HEAD")
            try:
                self._git("pull", "--ff-only", "--quiet", timeout=120)
            except UpdateError as error:
                raise UpdateError(f"更新失败：{error}")
            log = self._git("log", "--format=· %s", f"{before}..HEAD")
            self.status.update(behind=0, error="", current=self._git("log", "-1", "--format=%h %s", "HEAD"))
            return log
