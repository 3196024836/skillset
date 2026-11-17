"""
本地图形界面的后端：一个只监听 127.0.0.1 的小型 HTTP 服务，提供静态页面和 JSON 接口。
只用 Python 标准库，不需要安装任何依赖。

启动：python app/server.py [--no-browser] [--port 8765]
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import RootDir, data_dir  # noqa: E402
from keystore import KeyStoreError  # noqa: E402
from library import GitError  # noqa: E402
from service import SkillService, UserError  # noqa: E402
from updater import Updater, UpdateError  # noqa: E402

# 静态页面目录
WebDir = Path(__file__).resolve().parent / "web"
# 应用窗口只显示本机页面：关掉扩展、同步、后台联网、崩溃上报和多余的渲染进程，
# 实测进程从 11 个降到 7 个、私有内存少约 40MB
LeanWindowFlags = [
    "--disable-features=Translate,OptimizationHints,MediaRouter,AutofillServerCommunication",
    "--disable-extensions", "--disable-component-extensions-with-background-pages",
    "--disable-background-networking", "--disable-sync", "--disable-default-apps", "--no-pings",
    "--disable-breakpad", "--disable-crash-reporter", "--renderer-process-limit=1",
]
# 静态文件类型
ContentTypes = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                ".ico": "image/x-icon"}

# 全局业务对象、自身更新、HTTP 服务、应用窗口进程
service = None
updater = Updater(RootDir)
httpServer = None
windowProcess = None
# 更新重启后接班的进程：跟着哪个窗口进程一起退出（再更新一次时还要传下去）
attachedPid = 0
# 更新完代码后要不要重启自己
restartRequested = False


def route(path: str, body: dict) -> dict:
    """把接口路径分发到业务方法。

    :param path: 接口路径，如 /api/skill/enable
    :param body: 请求 JSON
    :returns: 返回给前端的 JSON
    """
    force = bool(body.get("force"))
    itemId = body.get("id", "")
    routes = {
        "/api/state": lambda: {**service.state(), "update": updater.status},
        "/api/stamp": lambda: {"stamp": service.stamp(), "update": updater.status},
        "/api/reload": lambda: (service.reload(), service.state())[1],
        "/api/skill/enable": lambda: service.enable(itemId, force),
        "/api/skill/disable": lambda: service.disable(itemId, force),
        "/api/skill/upload": lambda: service.upload(itemId),
        "/api/skill/delete": lambda: service.delete(itemId, force),
        "/api/item/edit": lambda: service.edit(body.get("kind", "skill"), itemId, body.get("fields", {})),
        "/api/upload-all": lambda: service.upload_all(),
        "/api/sync-all": lambda: service.sync_all(),
        "/api/update": lambda: service.update(),
        "/api/clean-duplicates": lambda: service.clean_duplicates(),
        "/api/push": lambda: service.push(),
        "/api/group/add": lambda: service.group_add(body.get("name", "")),
        "/api/group/edit": lambda: service.group_edit(itemId, body.get("name", ""), body.get("note", "")),
        "/api/group/delete": lambda: service.group_delete(itemId),
        "/api/group/toggle": lambda: service.group_toggle(itemId, bool(body.get("enabled")), force),
        "/api/plugin/enable": lambda: service.plugin_enable(itemId),
        "/api/plugin/disable": lambda: service.plugin_disable(itemId, force),
        "/api/plugin/upload": lambda: service.plugin_upload(itemId),
        "/api/plugin/delete": lambda: service.plugin_delete(itemId, force),
        "/api/mcp/enable": lambda: service.mcp_enable(itemId, force),
        "/api/mcp/disable": lambda: service.mcp_disable(itemId, force),
        "/api/mcp/upload": lambda: service.mcp_upload(itemId),
        "/api/mcp/delete": lambda: service.mcp_delete(itemId, force),
        "/api/rules/save": lambda: service.rules_save(body.get("text", "")),
        "/api/rules/import": lambda: service.rules_import(body.get("path", "")),
        "/api/rules/apply": lambda: service.rules_apply(force),
        "/api/plugin/available": lambda: service.plugin_available(),
        "/api/plugin/add-market": lambda: service.plugin_add_market(body.get("source", "")),
        "/api/targets": lambda: service.set_targets(body.get("ids", [])),
        "/api/settings": lambda: service.set_settings(body.get("remoteUrl", ""), body.get("autoPush", True)),
        "/api/open": lambda: open_folder(body.get("path", "")),
        "/api/connect": lambda: service.connect(body.get("remoteUrl", "")),
        "/api/github/status": lambda: service.github_status(),
        "/api/github/login": lambda: service.github_login(),
        "/api/github/create": lambda: service.github_create(body.get("name", "")),
        "/api/onboarding/skip": lambda: service.skip_onboarding(),
        "/api/keys/list": lambda: service.keys_list(),
        "/api/keys/save": lambda: service.keys_save(body.get("fields", {})),
        "/api/keys/reveal": lambda: service.keys_reveal(itemId, body.get("keyId", "")),
        "/api/keys/mark": lambda: service.keys_mark(itemId, body.get("keyId", ""), bool(body.get("failed")),
                                                    body.get("reason", "")),
        "/api/keys/delete": lambda: service.keys_delete(itemId, force),
        "/api/keys/export": lambda: service.keys_export(body.get("password", "")),
        "/api/keys/import-backup": lambda: service.keys_import_backup(body.get("text", ""), body.get("password", "")),
        "/api/keys/import-csv": lambda: service.keys_import_csv(body.get("text", "")),
        "/api/app/check": lambda: app_check(),
        "/api/app/update": lambda: app_update(),
    }
    handler = routes.get(path)
    if handler is None:
        raise UserError(f"未知接口 {path}")
    return handler()


def app_check() -> dict:
    """手动检查 Skillset 自身有没有新版本。

    :returns: 结果
    """
    status = updater.check()
    message = f"有新版本：落后 {status['behind']} 个更新" if status["behind"] else "已经是最新版本"
    return {"message": message, "update": status}


def app_update() -> dict:
    """拉取新版本；有改动就重启服务，界面等服务回来后自动刷新。

    :returns: 结果，restarting 为真表示马上重启
    """
    global restartRequested
    log = updater.pull()
    if not log:
        return {"message": "已经是最新版本", "update": updater.status}
    restartRequested = True
    # 先把这次的回复发出去，再停服务
    threading.Timer(0.5, httpServer.shutdown).start()
    return {"message": f"已更新：\n{log}\n正在重启…", "restarting": True}


def restart_self(port: int) -> None:
    """用新代码重新启动服务：同一端口、不再开新窗口，原来的窗口关掉时照样停止。

    :param port: 现在用的端口
    """
    command = [sys.executable, str(Path(__file__).resolve()), "--port", str(port), "--no-browser"]
    pid = windowProcess.pid if windowProcess and windowProcess.poll() is None else attachedPid
    if pid:
        command += ["--attach-pid", str(pid)]
    subprocess.Popen(command, cwd=str(RootDir))


def wait_pid_then_stop(pid: int, server: ThreadingHTTPServer) -> None:
    """等指定进程（应用窗口）退出后停止服务。

    :param pid: 进程 id
    :param server: 要停的服务
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # 句柄是指针宽度，不声明的话 64 位下会被截成 int
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        # 0x00100000 = SYNCHRONIZE，只要求能等它结束；打不开说明进程已经不在了
        handle = kernel32.OpenProcess(0x00100000, False, pid)
        if handle:
            kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
            kernel32.CloseHandle(handle)
    else:
        while True:
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(5)
    server.shutdown()


def bind_server(port: int, exact: bool) -> ThreadingHTTPServer:
    """监听端口。exact 为真（重启接班）时必须用同一个端口，旧进程刚退出可能要等一下。

    :param port: 端口
    :param exact: 是否必须用这个端口
    :returns: 服务
    """
    if not exact:
        return ThreadingHTTPServer(("127.0.0.1", free_port(port)), Handler)
    for _attempt in range(50):
        try:
            return ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError:
            time.sleep(0.2)
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def open_folder(path: str) -> dict:
    """在资源管理器里打开文件夹；给的是文件时打开它所在的文件夹并选中它（用户主动点击才调用）。

    :param path: 文件夹或文件路径
    :returns: 结果
    """
    folder = Path(path)
    if folder.is_file():
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(folder)])
            return {"message": "已打开"}
        folder = folder.parent
    if not folder.is_dir():
        raise UserError(f"文件夹不存在：{path}")
    if sys.platform == "win32":
        os.startfile(folder)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])
    return {"message": "已打开"}


class Handler(BaseHTTPRequestHandler):
    """处理静态文件和 /api/* 接口。"""

    def log_message(self, format, *args):
        """关闭默认的逐条访问日志。"""

    def _send(self, code: int, data: bytes, contentType: str) -> None:
        """发送响应。

        :param code: HTTP 状态码
        :param data: 响应体
        :param contentType: 内容类型
        """
        self.send_response(code)
        self.send_header("Content-Type", contentType)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code: int, payload: dict) -> None:
        """发送 JSON 响应。

        :param code: HTTP 状态码
        :param payload: 内容
        """
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        """返回静态页面，或 GET /api/state。"""
        if self.path.startswith("/api/"):
            return self._handle_api({})
        name = self.path.split("?", 1)[0].lstrip("/") or "index.html"
        file = (WebDir / name).resolve()
        if WebDir not in file.parents or not file.is_file():
            return self._send(404, b"not found", "text/plain")
        self._send(200, file.read_bytes(), ContentTypes.get(file.suffix, "application/octet-stream"))

    def do_POST(self):
        """处理 JSON 接口。"""
        # 只接受本页面发来的请求，防止其他网页借浏览器调用本地接口
        if self.headers.get("X-Skill-Manager") != "1":
            return self._json(403, {"error": "forbidden"})
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        self._handle_api(body)

    def _handle_api(self, body: dict) -> None:
        """执行接口并把异常转换成错误响应。

        :param body: 请求 JSON
        """
        # 只认本机地址，防止 DNS 重绑定攻击借道访问
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return self._json(403, {"error": "forbidden"})
        try:
            self._json(200, route(self.path.split("?", 1)[0], body))
        except (UserError, GitError, KeyStoreError, UpdateError) as error:
            self._json(400, {"error": str(error)})
        except Exception as error:
            traceback.print_exc()
            self._json(500, {"error": f"程序出错：{error}"})


def free_port(preferred: int) -> int:
    """优先用指定端口，被占用时让系统分配一个。

    :param preferred: 首选端口
    :returns: 可用端口
    """
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]


def find_browser() -> str:
    """找一个支持应用窗口模式（--app）的 Chromium 内核浏览器。

    :returns: 可执行文件路径，找不到返回空字符串
    """
    candidates = [shutil.which("msedge"), shutil.which("chrome"), shutil.which("google-chrome"),
                  shutil.which("chromium"),
                  r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                  r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    return next((exe for exe in candidates if exe and Path(exe).is_file()), "")


def run_window(url: str, server: ThreadingHTTPServer) -> None:
    """用独立配置的应用窗口打开界面；窗口关闭后自动停止服务。

    独立配置目录保证窗口是单独的进程，关窗口就能被感知到。
    找不到浏览器时退回默认浏览器，此时服务一直运行，需关闭控制台退出。

    :param url: 界面地址
    :param server: 要在窗口关闭后停止的服务
    """
    exe = find_browser()
    if not exe:
        webbrowser.open(url)
        return
    profile = data_dir() / "window-profile"
    global windowProcess
    window = windowProcess = subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1320,880",
                               "--no-first-run", "--no-default-browser-check", *LeanWindowFlags])
    window.wait()
    server.shutdown()


def install_shortcut() -> None:
    """在桌面创建带图标的快捷方式（Windows），启动时不显示控制台窗口。"""
    if sys.platform != "win32":
        print("只有 Windows 需要创建快捷方式；macOS / Linux 请运行 start.sh")
        return
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    script = Path(__file__).resolve()
    icon = RootDir / "assets" / "icon.ico"
    command = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
        "(Join-Path ([Environment]::GetFolderPath('Desktop')) 'Skillset.lnk'));"
        f"$s.TargetPath='{pythonw}';$s.Arguments='\"{script}\"';$s.WorkingDirectory='{RootDir}';"
        f"$s.IconLocation='{icon}';$s.Description='Skillset';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", command], check=True)
    print("已在桌面创建快捷方式：Skillset")


def main() -> None:
    """解析参数，启动服务。"""
    global service, httpServer, attachedPid
    parser = argparse.ArgumentParser(description="Skillset")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="只启动服务，不打开窗口")
    parser.add_argument("--install-shortcut", action="store_true", help="在桌面创建快捷方式后退出")
    parser.add_argument("--attach-pid", type=int, default=0, help="内部用：更新重启后跟着这个窗口进程一起退出")
    args = parser.parse_args()
    if args.install_shortcut:
        return install_shortcut()

    # 无控制台启动（pythonw）时没有标准输出，把错误写进日志文件
    if sys.stderr is None:
        data_dir().mkdir(parents=True, exist_ok=True)
        sys.stderr = sys.stdout = open(data_dir() / "app.log", "a", encoding="utf-8", buffering=1)

    service = SkillService()
    server = httpServer = bind_server(args.port, exact=bool(args.attach_pid))
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print(f"Skillset: {url}", flush=True)
    if args.attach_pid:
        attachedPid = args.attach_pid
        threading.Thread(target=wait_pid_then_stop, args=(args.attach_pid, server), daemon=True).start()
    elif not args.no_browser:
        threading.Thread(target=run_window, args=(url, server), daemon=True).start()
    updater.start()
    server.serve_forever()
    server.server_close()
    if restartRequested:
        restart_self(port)


if __name__ == "__main__":
    main()
