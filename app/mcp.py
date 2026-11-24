"""
MCP 服务器配置：读写各个 AI 的 MCP 配置文件，统一成同一种格式来比较和安装。

统一格式（spec）：
    {"type": "stdio", "command": str, "args": [str], "env": {str: str}}
    {"type": "http" 或 "sse", "url": str, "headers": {str: str}}

各 AI 的配置写法在 agents.json 的 "mcp" 字段：
    {"format": "claude"}                                      Claude Code，通过 claude mcp 命令改（~/.claude.json 由它自己管）
    {"format": "toml", "path": "~/.codex/config.toml"}        Codex
    {"format": "json", "path": "...", "urlKey": "httpUrl"}    Gemini CLI、Antigravity、Cursor、Windsurf

例：
    target = Target("codex", "Codex", {"format": "toml", "path": "~/.codex/config.toml"}, [])
    servers = target.read()              # {名字: spec}
    target.write("cua-driver", spec)
"""

import json
import os
import re
import time
import tomllib
from pathlib import Path

import config as cfg
import plugins
import skillfs

# 统一格式里由本工具管理的键；各 AI 自己的其他键（超时、disabled 等）写回时保留
ManagedKeys = {"type", "command", "args", "env", "url", "httpUrl", "serverUrl", "headers", "http_headers"}
# 环境变量或请求头名字像密钥的：值不上传云端，只留在本机
SecretName = re.compile(r"key|token|secret|passw|auth|credential|cookie", re.I)
# TOML 里可以不加引号的键名
BareKey = re.compile(r"^[A-Za-z0-9_-]+$")


class McpError(Exception):
    """读写 MCP 配置失败时抛出，消息直接显示给用户。"""


# ------------------------------------------------------------------ 统一格式


def normalize(raw: dict) -> dict:
    """把各家写法转成统一格式。

    :param raw: 某个 AI 配置里的一条服务器定义
    :returns: spec
    """
    url = raw.get("url") or raw.get("httpUrl") or raw.get("serverUrl") or ""
    if url or raw.get("type") in ("http", "sse", "streamable-http"):
        kind = "sse" if raw.get("type") == "sse" else "http"
        headers = raw.get("headers") or raw.get("http_headers") or {}
        return {"type": kind, "url": str(url), "headers": {str(k): str(v) for k, v in headers.items()}}
    return {"type": "stdio", "command": str(raw.get("command", "")),
            "args": [str(arg) for arg in raw.get("args") or []],
            "env": {str(k): str(v) for k, v in (raw.get("env") or {}).items()}}


def secret_table(spec: dict) -> dict:
    """返回 spec 里放密钥的那张表（env 或 headers）。

    :param spec: 统一格式
    :returns: 字典
    """
    return spec.get("env") if spec["type"] == "stdio" else spec.get("headers")


def secret_names(spec: dict) -> list:
    """找出像密钥的环境变量 / 请求头名字（按名字或值判断）。

    :param spec: 统一格式
    :returns: 名字列表
    """
    found = []
    for name, value in (secret_table(spec) or {}).items():
        if SecretName.search(name) or any(pattern.search(value) for pattern in skillfs.SecretPatterns.values()):
            found.append(name)
    return sorted(found)


def scan_plain(spec: dict) -> list:
    """检查命令、参数、地址里有没有直接写着的密钥（这些不能自动剥离，只能拦下）。

    :param spec: 统一格式
    :returns: 发现的密钥类型列表
    """
    text = "\n".join([spec.get("command", ""), spec.get("url", ""), *spec.get("args", [])])
    return [kind for kind, pattern in skillfs.SecretPatterns.items() if pattern.search(text)]


def strip_secrets(spec: dict, names: list) -> dict:
    """把指定名字的值清空，得到可以上传的副本。

    :param spec: 统一格式
    :param names: 要清空的名字
    :returns: 新 spec
    """
    clean = json.loads(json.dumps(spec))
    table = secret_table(clean)
    for name in names:
        if table is not None and name in table:
            table[name] = ""
    return clean


def program_missing(spec: dict) -> bool:
    """命令写的是绝对路径、本机却没有这个程序（例如另一台电脑上的安装位置）。

    :param spec: 统一格式
    :returns: 缺程序返回 True
    """
    command = spec.get("command", "")
    return spec["type"] == "stdio" and Path(command).is_absolute() and not Path(command).exists()


def same(a: dict, b: dict, secretNames: list) -> bool:
    """比较两个 spec 是否一样，密钥的值不参与比较（云端那份本来就是空的）。

    :param a: spec
    :param b: spec
    :param secretNames: 不比较值的名字
    :returns: 一样返回 True
    """
    return strip_secrets(a, secretNames) == strip_secrets(b, secretNames)


def masked(spec: dict, secretNames: list) -> dict:
    """给界面显示用：密钥的值换成圆点。

    :param spec: 统一格式
    :param secretNames: 密钥名字
    :returns: 新 spec
    """
    shown = json.loads(json.dumps(spec))
    table = secret_table(shown)
    for name in secretNames:
        if table is not None and table.get(name):
            table[name] = "••••••"
    return shown


def summary(spec: dict) -> str:
    """一行说明：命令和参数，或地址。

    :param spec: 统一格式
    :returns: 文本
    """
    if spec["type"] != "stdio":
        return spec.get("url", "")
    return " ".join([Path(spec.get("command", "")).name, *spec.get("args", [])])


# ------------------------------------------------------------------ 备份


def backup(path: Path, agentId: str) -> None:
    """改配置文件前先备份一份到 data/backups。

    :param path: 要改的文件
    :param agentId: AI id，用在备份文件名里
    """
    if not path.is_file():
        return
    folder = cfg.data_dir() / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (folder / f"{stamp}-{agentId}-{path.name}").write_bytes(path.read_bytes())


# ------------------------------------------------------------------ TOML 写入（只覆盖 Codex 配置里用到的值类型）


def _toml_key(key: str) -> str:
    """TOML 键名，需要时加引号。

    :param key: 键名
    :returns: 文本
    """
    return key if BareKey.match(key) else json.dumps(key, ensure_ascii=False)


def _toml_value(value) -> str:
    """把值写成 TOML 字面量（JSON 的字符串转义在 TOML 基本字符串里同样有效）。

    :param value: 字符串、数字、布尔、列表或字典
    :returns: 文本
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_toml_key(k)} = {_toml_value(v)}" for k, v in value.items()) + " }"
    return json.dumps(str(value), ensure_ascii=False)


def _toml_block(name: str, table: dict) -> str:
    """生成一个 [mcp_servers.<name>] 段落。

    :param name: 服务器名
    :param table: 内容
    :returns: 文本
    """
    lines = [f"[mcp_servers.{_toml_key(name)}]"]
    lines += [f"{_toml_key(k)} = {_toml_value(v)}" for k, v in table.items()]
    return "\n".join(lines) + "\n"


def _toml_remove(text: str, name: str) -> str:
    """删掉 [mcp_servers.<name>] 和它的子表段落。

    :param text: 原文
    :param name: 服务器名
    :returns: 新文本
    """
    quoted = re.escape(name)
    header = re.compile(rf"^\s*\[\s*mcp_servers\s*\.\s*(?:{quoted}|\"{quoted}\"|'{quoted}')\s*(\.[^\]]*)?\]\s*(#.*)?$")
    anyHeader = re.compile(r"^\s*\[")
    kept, skipping = [], False
    for line in text.splitlines(keepends=True):
        if anyHeader.match(line):
            skipping = bool(header.match(line))
        if not skipping:
            kept.append(line)
    return "".join(kept)


# ------------------------------------------------------------------ 各个 AI


def claude_config_path() -> Path:
    """Claude Code 用户级配置文件 ~/.claude.json 的位置。

    :returns: 路径
    """
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override) / ".claude.json" if override else cfg.home_dir() / ".claude.json"


class Target:
    """一个 AI 的 MCP 配置。

    例：
        Target("gemini", "Gemini CLI", {"format": "json", "path": "~/.gemini/settings.json", "urlKey": "httpUrl"}, [])
    """

    def __init__(self, agentId: str, name: str, mcpDef: dict, ignore: list):
        """绑定 AI 和配置位置。

        :param agentId: AI id
        :param name: AI 显示名
        :param mcpDef: agents.json 里的 mcp 字段
        :param ignore: 这个 AI 自己管理、不该同步的服务器名
        """
        # AI id 和显示名
        self.agentId, self.name = agentId, name
        # 配置格式：claude / toml / json
        self.format = mcpDef["format"]
        # 配置文件
        self.path = claude_config_path() if self.format == "claude" else cfg.expand_path(mcpDef["path"])
        # 远程服务器地址写在哪个键
        self.urlKey = mcpDef.get("urlKey", "url")
        # 不管理的服务器名（小写）
        self.ignore = {item.lower() for item in ignore}

    def _raw(self) -> dict:
        """读出原始的 {名字: 定义}。

        :returns: 字典
        """
        if not self.path.is_file():
            return {}
        try:
            if self.format == "toml":
                with open(self.path, "rb") as file:
                    servers = tomllib.load(file).get("mcp_servers", {})
            else:
                servers = json.loads(self.path.read_text(encoding="utf-8") or "{}").get("mcpServers", {})
        except (OSError, ValueError) as error:
            raise McpError(f"读不了 {self.path}：{error}")
        return {name: value for name, value in servers.items() if isinstance(value, dict)}

    def read(self) -> dict:
        """读出这个 AI 配置的所有 MCP 服务器。

        :returns: {名字: spec}
        """
        return {name: normalize(raw) for name, raw in self._raw().items() if name.lower() not in self.ignore}

    def write(self, name: str, spec: dict) -> None:
        """添加或覆盖一个服务器，保留这条定义里本工具不管的键。

        :param name: 服务器名
        :param spec: 统一格式
        """
        existing = self._raw().get(name, {})
        if self.format == "claude":
            self._claude_write(name, spec, bool(existing))
            return
        backup(self.path, self.agentId)
        table = {key: value for key, value in existing.items() if key not in ManagedKeys}
        if spec["type"] == "stdio":
            table.update({"command": spec["command"], "args": spec["args"]})
            if spec["env"]:
                table["env"] = spec["env"]
        elif self.format == "toml":
            table["url"] = spec["url"]
            if spec["headers"]:
                table["http_headers"] = spec["headers"]
        else:
            table[self.urlKey] = spec["url"]
            if spec["headers"]:
                table["headers"] = spec["headers"]
        if self.format == "toml":
            self._toml_write(name, table)
        else:
            self._json_update(lambda servers: servers.__setitem__(name, table))

    def remove(self, name: str) -> None:
        """删除一个服务器。

        :param name: 服务器名
        """
        if name not in self._raw():
            return
        if self.format == "claude":
            self._claude_run("remove", name, "-s", "user")
            return
        backup(self.path, self.agentId)
        if self.format == "toml":
            self._toml_write(name, None)
        else:
            self._json_update(lambda servers: servers.pop(name, None))

    def _claude_run(self, *args: str) -> None:
        """执行 claude mcp 命令。

        :param args: mcp 之后的参数
        """
        try:
            plugins.run_claude("mcp", *args)
        except (plugins.PluginError, OSError) as error:
            raise McpError(f"Claude Code：{error}")

    def _claude_write(self, name: str, spec: dict, exists: bool) -> None:
        """通过 claude mcp add-json 写入用户级配置。

        :param name: 服务器名
        :param spec: 统一格式
        :param exists: 已存在时先删除
        """
        body = {"type": spec["type"]}
        if spec["type"] == "stdio":
            body.update({"command": spec["command"], "args": spec["args"], "env": spec["env"]})
        else:
            body.update({"url": spec["url"], "headers": spec["headers"]})
        if exists:
            self._claude_run("remove", name, "-s", "user")
        self._claude_run("add-json", name, json.dumps(body, ensure_ascii=False), "-s", "user")

    def _json_update(self, change) -> None:
        """读出 JSON 配置，改 mcpServers，写回；其他内容原样保留。

        :param change: 接收 mcpServers 字典的函数
        """
        data = json.loads(self.path.read_text(encoding="utf-8") or "{}") if self.path.is_file() else {}
        change(data.setdefault("mcpServers", {}))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _toml_write(self, name: str, table) -> None:
        """在 config.toml 里替换或删除一个服务器段落，写完校验，失败就恢复原文。

        :param name: 服务器名
        :param table: 新内容；None 表示删除
        """
        original = self.path.read_text(encoding="utf-8") if self.path.is_file() else ""
        text = _toml_remove(original, name).rstrip("\n")
        if table is not None:
            text = (text + "\n\n" if text else "") + _toml_block(name, table)
        else:
            text += "\n"
        try:
            parsed = tomllib.loads(text).get("mcp_servers", {})
            ok = (name not in parsed) if table is None else (normalize(parsed.get(name, {})) == normalize(table))
        except tomllib.TOMLDecodeError:
            ok = False
        if not ok:
            raise McpError(f"{self.path} 的写法比较特殊，没能安全修改 {name}，请手动改")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8")
