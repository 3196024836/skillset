"""
监视文件夹有没有变化：Windows 上用系统的文件夹变化通知，平时不读磁盘、不占 CPU。
只回答"上次问过之后有没有变"，具体变了什么由调用方自己重新扫描。
其他系统、或者某个文件夹监视失败时，一律回答"可能变了"，调用方照常扫描，结果不会错，只是费一点。

例：
    watcher = Watcher()
    watcher.watch([(Path.home() / ".claude/skills", True)])
    if watcher.take():
        ...重新扫描
"""

import ctypes
import sys
import threading
from ctypes import wintypes

# 文件名、文件夹名、大小、最后写入时间变化时通知
NotifyFilter = 0x1 | 0x2 | 0x8 | 0x10
# 一次最多等 64 个句柄，留一个给"换监视列表"的唤醒事件
MaxRoots = 63
# 系统常量
InvalidHandle = ctypes.c_void_p(-1).value
Infinite = 0xFFFFFFFF
WaitObject0 = 0


class Watcher:
    """后台线程等待系统通知，有变化就记一笔。"""

    def __init__(self):
        """在 Windows 上启动后台等待线程。"""
        self.lock = threading.Lock()
        # 上次 take 之后是否有变化；刚启动时当作有
        self.dirty = True
        # 有文件夹监视不了时一直回答"可能变了"
        self.fallback = sys.platform != "win32"
        # 当前监视的 [(路径字符串, 是否含子文件夹)] 和对应句柄
        self.roots = []
        self.handles = []
        # 换列表后等后台线程关掉的旧句柄
        self.retired = []
        if self.fallback:
            return
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.FindFirstChangeNotificationW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD]
        kernel.FindFirstChangeNotificationW.restype = wintypes.HANDLE
        kernel.FindNextChangeNotification.argtypes = [wintypes.HANDLE]
        kernel.FindCloseChangeNotification.argtypes = [wintypes.HANDLE]
        kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateEventW.restype = wintypes.HANDLE
        kernel.SetEvent.argtypes = [wintypes.HANDLE]
        kernel.WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL,
                                                  wintypes.DWORD]
        kernel.WaitForMultipleObjects.restype = wintypes.DWORD
        self.kernel = kernel
        # 换监视列表时叫醒后台线程
        self.wake = kernel.CreateEventW(None, False, False, None)
        threading.Thread(target=self._loop, name="watcher", daemon=True).start()

    def watch(self, roots: list) -> None:
        """换一组要监视的文件夹；和现在一样就什么都不做。返回时新的监视已经生效。

        :param roots: [(Path, 是否含子文件夹)]，文件夹必须存在
        """
        roots = sorted({(str(path), bool(recursive)) for path, recursive in roots})
        if self.fallback or roots == self.roots:
            return
        handles = []
        for path, recursive in roots[:MaxRoots]:
            handle = self.kernel.FindFirstChangeNotificationW(path, recursive, NotifyFilter)
            if not handle or handle == InvalidHandle:
                break
            handles.append(handle)
        with self.lock:
            self.retired += self.handles
            self.roots, self.handles = roots, handles
            self.dirty = True
            if len(handles) < len(roots):
                # 有文件夹没监视上：退回到每次都扫描
                self.fallback = True
                self.retired += handles
                self.handles = []
        self.kernel.SetEvent(self.wake)

    def take(self) -> bool:
        """上次调用之后有没有变化，调用后清零。

        :returns: 可能有变化返回 True
        """
        with self.lock:
            changed = self.dirty or self.fallback
            self.dirty = False
        return changed

    def _loop(self) -> None:
        """后台线程：等任意一个文件夹的通知，或者换列表的唤醒。"""
        while True:
            with self.lock:
                for handle in self.retired:
                    self.kernel.FindCloseChangeNotification(handle)
                self.retired = []
                handles = list(self.handles)
            array = (wintypes.HANDLE * (len(handles) + 1))(self.wake, *handles)
            index = self.kernel.WaitForMultipleObjects(len(array), array, False, Infinite) - WaitObject0
            if index == 0:
                continue
            if 1 <= index <= len(handles):
                with self.lock:
                    self.dirty = True
                # 句柄可能刚被换掉，下一轮会重新取列表
                self.kernel.FindNextChangeNotification(handles[index - 1])
                continue
            # 等待本身失败：退回到每次都扫描
            with self.lock:
                self.fallback = True
            return
