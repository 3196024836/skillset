"""
让 git、gh 走系统代理。

git 和 gh 不读 Windows 的系统代理设置，开着 Clash 之类的代理时它们仍然直连 GitHub，
在国内常被重置连接（Recv failure: Connection was reset）。这里在联网命令前读一次系统代理，
代理端口确实在监听才交给子进程；代理没开就照常直连。用户自己设了代理环境变量或 git 的 http.proxy 时不插手。
"""

import os
import socket
import subprocess
import sys

# 这些 git 子命令要联网
NetworkCommands = {"fetch", "pull", "push", "clone", "ls-remote"}
# 检查代理端口是否在监听的超时（秒）
ProbeTimeout = 0.3


def _system_proxy() -> str:
    """读 Windows 系统代理，返回 host:port；没开或不是 Windows 时返回空字符串。"""
    if sys.platform != "win32":
        return ""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            if not winreg.QueryValueEx(key, "ProxyEnable")[0]:
                return ""
            server = str(winreg.QueryValueEx(key, "ProxyServer")[0]).strip()
    except OSError:
        return ""
    # 也可能写成 "http=host:port;https=host:port"
    if "=" in server:
        parts = dict(item.split("=", 1) for item in server.split(";") if "=" in item)
        server = parts.get("https") or parts.get("http") or ""
    return server.split("://")[-1].strip("/")


def _listening(address: str) -> bool:
    """代理端口能连上才算可用，免得代理软件关了以后 git 全部失败。"""
    host, _, port = address.rpartition(":")
    if not host or not port.isdigit():
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=ProbeTimeout):
            return True
    except OSError:
        return False


def _user_configured(env: dict) -> bool:
    """用户自己配过代理（环境变量或 git 全局 http.proxy）就不插手。"""
    if any(env.get(name) for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")):
        return True
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        done = subprocess.run(["git", "config", "--global", "--get", "http.proxy"], capture_output=True, text=True,
                              timeout=10, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(done.stdout.strip())


def network_env(base: dict = None) -> dict:
    """返回联网命令用的环境变量：系统代理可用时加上代理设置。

    :param base: 基础环境变量，默认当前进程的
    :returns: 新的环境变量字典
    """
    env = dict(os.environ if base is None else base)
    if _user_configured(env):
        return env
    address = _system_proxy()
    if address and _listening(address):
        proxy = f"http://{address}"
        env.update(HTTPS_PROXY=proxy, HTTP_PROXY=proxy, NO_PROXY="localhost,127.0.0.1")
    return env


def git_env(args: tuple, base: dict = None) -> dict:
    """git 命令的环境变量：只有联网的子命令才去读系统代理。"""
    command = next((arg for arg in args if not arg.startswith("-")), "")
    env = dict(os.environ if base is None else base)
    return network_env(env) if command in NetworkCommands else env
