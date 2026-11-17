"""
密钥（API key、token）保管箱：每条密钥有名字、说明、默认环境变量名，和按顺序排好的多个 key。
第一个没被标为失效的 key 就是当前在用的；它不好使了标成失效，就自动轮到下一个。

存放：data/keys.dat，用 Windows 自带的 DPAPI 加密，只有本机、本用户能解开。
注意：没有主密码，本用户下运行的程序都能解开；它防的是文件被拷走、换电脑读取。
DPAPI 加密的文件换电脑解不开，所以另有“导出备份”：用你设的密码加密，任何电脑都能导回来。

例：
    store = KeyStore()
    store.save_entry({"name": "OpenAI", "note": "个人项目", "env": "OPENAI_API_KEY",
                      "keys": [{"value": "sk-..."}]})
    entry, key = store.active("OpenAI")
"""

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sys
import time
import uuid
from pathlib import Path

import config as cfg

# 文件开头的标记，用来认出格式
Magic = b"SKK1"
# DPAPI 附加熵：别的程序不带它解不开（只是多一道门槛，不是密码）
Entropy = b"skillset-keys"
# 备份文件格式名和密码派生的迭代次数
BackupFormat = "skillset-keys-backup"
BackupIterations = 600_000
# 环境变量名的合法写法
EnvPattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# KeePassXC 导出 CSV 时要跳过的模板条目
TemplateTitles = {"示例 - 复制我来新建"}


class KeyStoreError(Exception):
    """可预期的失败（名字重复、密码不对等），消息直接显示给用户。"""


# ------------------------------------------------------------------ DPAPI

def _dpapi(data: bytes, protect: bool) -> bytes:
    """用 Windows DPAPI 加密或解密（绑定当前用户和本机）。

    :param data: 明文或密文
    :param protect: True 加密，False 解密
    :returns: 结果
    """
    if sys.platform != "win32":
        raise KeyStoreError("密钥管理暂只支持 Windows")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffers = []  # 调用结束前不能被回收

    def blob(raw: bytes) -> Blob:
        buffers.append(ctypes.create_string_buffer(raw, len(raw)))
        return Blob(len(raw), ctypes.cast(buffers[-1], ctypes.POINTER(ctypes.c_char)))

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    source, entropy, result = blob(data), blob(Entropy), Blob()
    # 0x1 = CRYPTPROTECT_UI_FORBIDDEN：不弹任何窗口
    call = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not call(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0x1, ctypes.byref(result)):
        if protect:
            raise KeyStoreError("加密密钥失败（Windows DPAPI 出错）")
        raise KeyStoreError("解不开密钥文件：它不是这台电脑、这个 Windows 用户加密的。请用“导入备份”恢复")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


# ------------------------------------------------------------------ 密码加密（备份用）

def _derive(password: str, salt: bytes, iterations: int) -> tuple:
    """从密码派生加密钥和校验钥。

    :returns: (encKey, macKey)
    """
    material = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, 64)
    return material[:32], material[32:]


def _keystream_xor(key: bytes, nonce: bytes, data: bytes) -> bytes:
    """HMAC-SHA256 计数器模式：生成密钥流再和数据异或，加密解密是同一个操作。"""
    out = bytearray()
    for counter in range(0, (len(data) + 31) // 32):
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
    return bytes(a ^ b for a, b in zip(data, out))


def seal(data: bytes, password: str) -> dict:
    """用密码加密（先加密再算校验码），返回可写成 JSON 的字典。

    :param data: 明文
    :param password: 密码
    :returns: 备份字典
    """
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(16)
    encKey, macKey = _derive(password, salt, BackupIterations)
    cipher = _keystream_xor(encKey, nonce, data)
    tag = hmac.new(macKey, nonce + cipher, hashlib.sha256).digest()
    pack = lambda raw: base64.b64encode(raw).decode("ascii")  # noqa: E731
    return {"format": BackupFormat, "version": 1, "kdf": "pbkdf2-sha256", "iterations": BackupIterations,
            "salt": pack(salt), "nonce": pack(nonce), "data": pack(cipher), "tag": pack(tag)}


def unseal(backup: dict, password: str) -> bytes:
    """解开 seal 生成的备份；密码不对或文件被改过都会报错。

    :param backup: 备份字典
    :param password: 密码
    :returns: 明文
    """
    if not isinstance(backup, dict) or backup.get("format") != BackupFormat:
        raise KeyStoreError("这不是 Skillset 导出的密钥备份文件")
    try:
        salt, nonce, cipher, tag = (base64.b64decode(backup[name]) for name in ("salt", "nonce", "data", "tag"))
        iterations = int(backup["iterations"])
    except (KeyError, ValueError, TypeError):
        raise KeyStoreError("备份文件已损坏")
    encKey, macKey = _derive(password, salt, iterations)
    if not hmac.compare_digest(tag, hmac.new(macKey, nonce + cipher, hashlib.sha256).digest()):
        raise KeyStoreError("密码不对，或者备份文件被改过")
    return _keystream_xor(encKey, nonce, cipher)


# ------------------------------------------------------------------ 保管箱

def _now() -> str:
    """当前时间，给人看的格式。"""
    return time.strftime("%Y-%m-%d %H:%M")


def mask(value: str) -> str:
    """只露出末 4 位。"""
    return "••••" + value[-4:] if len(value) > 8 else "••••"


class KeyStore:
    """密钥文件的读写。每次操作都从磁盘重新读，界面和命令行同时用也不会互相覆盖旧数据。"""

    def __init__(self, path: Path = None):
        """:param path: 密钥文件，默认 data/keys.dat"""
        self.path = Path(path) if path else cfg.data_dir() / "keys.dat"

    def load(self) -> list:
        """读出全部条目。

        :returns: [{id, name, note, env, keys: [{id, value, label, status, failedAt, reason, addedAt, lastUsedAt}]}]
        """
        if not self.path.is_file():
            return []
        raw = self.path.read_bytes()
        if not raw.startswith(Magic):
            raise KeyStoreError(f"密钥文件格式不对：{self.path}")
        return json.loads(_dpapi(raw[len(Magic):], protect=False).decode("utf-8"))["entries"]

    def save(self, entries: list) -> None:
        """加密写回；先写临时文件再替换，旧文件留一份 keys.dat.bak。

        :param entries: 全部条目
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        blob = Magic + _dpapi(json.dumps({"entries": entries}, ensure_ascii=False).encode("utf-8"), protect=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_bytes(blob)
        if self.path.is_file():
            os.replace(self.path, self.path.with_name(self.path.name + ".bak"))
        os.replace(temp, self.path)

    # -------------------------------------------------- 查询

    def summary(self) -> dict:
        """给导航角标用：条目数、有失效 key 的条目数。不解密时也不报错。

        :returns: {"count", "failed", "error"}
        """
        try:
            entries = self.load()
        except (KeyStoreError, ValueError, OSError) as error:
            return {"count": 0, "failed": 0, "error": str(error)}
        failed = sum(1 for entry in entries if any(key["status"] == "failed" for key in entry["keys"]))
        return {"count": len(entries), "failed": failed, "error": ""}

    def list_masked(self) -> list:
        """全部条目，key 只显示末 4 位。

        :returns: 条目列表，每个 key 多一个 masked、少了 value；条目多一个 activeId
        """
        result = []
        for entry in self.load():
            keys = [{**{k: v for k, v in key.items() if k != "value"}, "masked": mask(key["value"])}
                    for key in entry["keys"]]
            active = next((key["id"] for key in entry["keys"] if key["status"] == "ok"), "")
            result.append({**entry, "keys": keys, "activeId": active})
        return result

    def _find(self, entries: list, name: str) -> dict:
        """按 id 或名字（不分大小写）找条目。"""
        entry = next((item for item in entries if item["id"] == name), None) or next(
            (item for item in entries if item["name"].lower() == name.lower()), None)
        if entry is None:
            raise KeyStoreError(f"没有叫 {name} 的密钥")
        return entry

    def _find_key(self, entry: dict, keyId: str) -> dict:
        """在条目里按 id 找 key。"""
        key = next((item for item in entry["keys"] if item["id"] == keyId), None)
        if key is None:
            raise KeyStoreError("这个 key 已经不存在，请刷新")
        return key

    def reveal(self, entryId: str, keyId: str) -> str:
        """取一个 key 的明文（界面点“显示/复制”时用）。"""
        return self._find_key(self._find(self.load(), entryId), keyId)["value"]

    def active(self, name: str) -> tuple:
        """当前在用的 key：第一个没标失效的。

        :param name: 条目名字或 id
        :returns: (条目, key, 第几个从 1 数)；全部失效时 key 为 None
        """
        entry = self._find(self.load(), name)
        for index, key in enumerate(entry["keys"], 1):
            if key["status"] == "ok":
                return entry, key, index
        return entry, None, 0

    # -------------------------------------------------- 修改

    def save_entry(self, fields: dict) -> dict:
        """新建或修改一条密钥。

        :param fields: {id?, name, note, env, keys: [{id?, value?, label}]}；
            已有的 key 只传 id（不传 value 表示不改），新 key 传 value；数组顺序就是使用顺序
        :returns: 保存后的条目（不含明文）
        """
        entries = self.load()
        entryId = fields.get("id") or ""
        name = str(fields.get("name", "")).strip()
        env = str(fields.get("env", "")).strip()
        if not name:
            raise KeyStoreError("请填写名字")
        if env and not EnvPattern.match(env):
            raise KeyStoreError("环境变量名只能用字母、数字和下划线，且不能以数字开头")
        if any(item["name"].lower() == name.lower() and item["id"] != entryId for item in entries):
            raise KeyStoreError(f"已经有叫 {name} 的密钥了")
        old = next((item for item in entries if item["id"] == entryId), None) if entryId else None
        if entryId and old is None:
            raise KeyStoreError("这条密钥已经不存在，请刷新")
        oldKeys = {key["id"]: key for key in (old["keys"] if old else [])}

        keys, seen = [], set()
        for item in fields.get("keys") or []:
            value = str(item.get("value") or "").strip()
            label = str(item.get("label") or "").strip()
            if item.get("id") in oldKeys:
                key = dict(oldKeys[item["id"]], label=label)
                if value and value != key["value"]:
                    key.update(value=value, status="ok", failedAt="", reason="", addedAt=_now())
            elif value:
                key = {"id": uuid.uuid4().hex[:12], "value": value, "label": label, "status": "ok",
                       "failedAt": "", "reason": "", "addedAt": _now(), "lastUsedAt": ""}
            else:
                continue
            if key["value"] in seen:
                raise KeyStoreError("同一条里有重复的 key")
            seen.add(key["value"])
            keys.append(key)
        if not keys:
            raise KeyStoreError("至少要填一个 key")

        entry = {"id": entryId or uuid.uuid4().hex[:12], "name": name,
                 "note": str(fields.get("note", "")).strip(), "env": env, "keys": keys}
        if old:
            entries[entries.index(old)] = entry
        else:
            entries.append(entry)
        self.save(entries)
        return entry

    def mark(self, entryId: str, keyId: str, failed: bool, reason: str = "") -> dict:
        """把 key 标成失效，或恢复成可用。

        :returns: 条目
        """
        entries = self.load()
        entry = self._find(entries, entryId)
        key = self._find_key(entry, keyId)
        if failed:
            key.update(status="failed", failedAt=_now(), reason=reason.strip()[:300] or "手动标记")
        else:
            key.update(status="ok", failedAt="", reason="")
        self.save(entries)
        return entry

    def touch(self, entryId: str, keyId: str) -> None:
        """记录 key 最近一次被命令行使用的时间。"""
        entries = self.load()
        self._find_key(self._find(entries, entryId), keyId)["lastUsedAt"] = _now()
        self.save(entries)

    def delete(self, entryId: str) -> str:
        """删除整条密钥。

        :returns: 被删的名字
        """
        entries = self.load()
        entry = self._find(entries, entryId)
        entries.remove(entry)
        self.save(entries)
        return entry["name"]

    # -------------------------------------------------- 备份和导入

    def export_backup(self, password: str, target: Path) -> int:
        """用密码加密导出全部密钥，换电脑也能导回。

        :param password: 备份密码
        :param target: 写到哪个文件
        :returns: 导出的条目数
        """
        if len(password) < 8:
            raise KeyStoreError("备份密码至少 8 位")
        entries = self.load()
        backup = seal(json.dumps({"entries": entries}, ensure_ascii=False).encode("utf-8"), password)
        Path(target).write_text(json.dumps(backup, indent=1), encoding="utf-8")
        return len(entries)

    def _merge(self, incoming: list) -> tuple:
        """把导入的条目加进来；名字已存在的跳过。

        :returns: (加了几条, 跳过的名字)
        """
        entries = self.load()
        names = {item["name"].lower() for item in entries}
        added, skipped = 0, []
        for entry in incoming:
            if entry["name"].lower() in names:
                skipped.append(entry["name"])
                continue
            names.add(entry["name"].lower())
            entries.append(entry)
            added += 1
        if added:
            self.save(entries)
        return added, skipped

    def import_backup(self, text: str, password: str) -> tuple:
        """导入 export_backup 生成的备份。

        :param text: 备份文件内容
        :param password: 备份密码
        :returns: (加了几条, 跳过的名字)
        """
        try:
            backup = json.loads(text)
        except ValueError:
            raise KeyStoreError("这不是 Skillset 导出的密钥备份文件")
        return self._merge(json.loads(unseal(backup, password).decode("utf-8"))["entries"])

    def import_csv(self, text: str) -> tuple:
        """导入 KeePassXC 导出的 CSV：标题当名字，备注当说明，密码当 key。

        :param text: CSV 内容
        :returns: (加了几条, 跳过的名字)
        """
        rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
        if rows and not {"Title", "Password"} <= set(rows[0]):
            raise KeyStoreError("没认出 CSV 的列：需要 KeePassXC 导出的 Title、Password 列")
        incoming = []
        for row in rows:
            title, value = (row.get("Title") or "").strip(), (row.get("Password") or "").strip()
            if not title or not value or title in TemplateTitles:
                continue
            group = (row.get("Group") or "").strip()
            note = "\n".join(part for part in [(row.get("Notes") or "").strip(),
                                               f"来自 KeePassXC：{group}" if group else ""] if part)
            incoming.append({"id": uuid.uuid4().hex[:12], "name": title, "note": note, "env": "",
                             "keys": [{"id": uuid.uuid4().hex[:12], "value": value,
                                       "label": (row.get("UserName") or row.get("Username") or "").strip(),
                                       "status": "ok", "failedAt": "", "reason": "", "addedAt": _now(),
                                       "lastUsedAt": ""}]})
        return self._merge(incoming)
