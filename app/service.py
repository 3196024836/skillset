"""
业务核心：把"库（云端正本）"、"各 AI 的本机目录"和"插件"拼成界面要的状态，并执行所有操作。

约定：
- 库里的 skill 是正本；各 AI 目录里的是副本。
- 启用 = 从库复制到所有目标 AI；关闭 = 从本机所有 AI 目录物理删除（库里保留）。
- 彻底删除 = 库和本机都删。
- 有可能丢数据的操作（云端没有备份、本机副本有改动），先返回 needConfirm 让界面询问，用户确认后带 force 再调一次。
- 本机副本和正本不一样时，同步不会覆盖它，只标出来。
- MCP 工具和全局规则文档的逻辑在 mcp_service.py，一键导出在 export_service.py（都混入本类）。
"""

import hashlib
import json
import threading
import time
import uuid
from pathlib import Path

import config as cfg
import github
import mcp
import plugins
import skillfs
from library import GitError, Library
from export_service import ExportMixin
from key_service import KeyMixin
from keystore import KeyStore
from mcp_service import McpMixin
from watcher import Watcher
from plugins import PluginError

# 指纹最长缓存多久（秒）：万一系统通知漏了，到时间也会重算
StampMaxAge = 60


class UserError(Exception):
    """可预期的失败（参数不对、git 报错等），消息直接显示给用户。"""


def _key(path: Path) -> str:
    """把路径归一成比较用的键（Windows 不区分大小写）。

    :param path: 路径
    :returns: 小写绝对路径字符串
    """
    return str(path.resolve() if path.exists() else path.absolute()).lower()


class SkillService(McpMixin, KeyMixin, ExportMixin):
    """所有界面操作的入口，内部加锁，同一时间只执行一个操作。

    例：
        service = SkillService()
        state = service.state()
        service.enable("ui-ugui")
    """

    # 混入类里也用这个异常
    UserError = UserError

    def __init__(self):
        """加载配置和库。"""
        # 串行化所有操作，避免两个按钮同时改文件
        self.lock = threading.RLock()
        # 盯着各 AI 目录的变化，没变时界面问指纹几乎不花时间
        self.watcher = Watcher()
        # 上次算的指纹和时间
        self._stampValue, self._stampAt = "", 0.0
        # 界面显示用的 skill 内容指纹缓存 {路径: (文件大小和时间的摘要, 指纹)}
        self._hashCache = {}
        # 已装插件列表缓存 (Claude 插件记录的状态, 列表)
        self._pluginCache = None
        # 密钥保管箱 data/keys.dat
        self.keys = KeyStore()
        self.reload()

    def reload(self) -> None:
        """重新读取配置、AI 定义和库。"""
        with self.lock:
            # 本机配置
            self.config = cfg.load_config()
            # AI 目标定义
            self.agentsDef = cfg.load_agents()
            # 通用目录（多家 AI 共同读取）
            self.sharedDir = cfg.expand_path("~/.agents/skills")
            # 库
            self.library = Library(Path(self.config["libraryPath"]))
            self.library.ensure()
            self._stampValue = ""

    # ------------------------------------------------------------------ 目录

    def agent_rows(self) -> list:
        """列出所有 AI 目标及其在本机的状态。

        :returns: [{"id", "name", "installed", "active", "installDir", "readsShared"}]
        """
        chosen = self.config.get("targets")
        rows = []
        for agent in self.agentsDef:
            installed = cfg.expand_path(agent["detect"]).exists()
            installDir = self.sharedDir if agent.get("readsShared") else cfg.expand_path(agent["skillsDir"])
            rows.append({
                "id": agent["id"], "name": agent["name"], "installed": installed,
                "active": (agent["id"] in chosen) if chosen is not None else installed,
                "installDir": str(installDir), "readsShared": bool(agent.get("readsShared")),
            })
        return rows

    def _dirs(self) -> list:
        """汇总所有要扫描的目录：安装目录、各 AI 自有目录、旧版目录。

        :returns: [{"path": Path, "label": str, "install": bool, "legacy": bool}]
        """
        merged = {}

        def add(path: Path, agentName: str, install: bool, legacy: bool, twin: Path = None):
            # 同一物理目录只保留一条，合并使用它的 AI 名称；twin 是旧目录对应的新目录
            key = _key(path)
            entry = merged.setdefault(key, {"path": path, "agents": [], "install": False, "legacy": legacy,
                                            "twin": _key(twin) if twin else ""})
            if agentName not in entry["agents"]:
                entry["agents"].append(agentName)
            entry["install"] = entry["install"] or install
            entry["legacy"] = entry["legacy"] and legacy

        for agent, row in zip(self.agentsDef, self.agent_rows()):
            add(Path(row["installDir"]), agent["name"], row["active"], False)
            ownDir = cfg.expand_path(agent["skillsDir"])
            if ownDir.exists():
                add(ownDir, agent["name"], False, False)
            for legacy in agent.get("legacyDirs", []):
                legacyDir = cfg.expand_path(legacy)
                if legacyDir.exists():
                    add(legacyDir, agent["name"] + "（旧目录）", False, True, Path(row["installDir"]))

        # 只保留存在的目录和需要安装的目录
        result = [entry for entry in merged.values() if entry["install"] or entry["path"].exists()]
        for entry in result:
            entry["label"] = " / ".join(entry["agents"][:4]) + (" 等" if len(entry["agents"]) > 4 else "")
        return result

    def _install_dirs(self) -> list:
        """返回当前要安装到的目录。

        :returns: [Path]
        """
        return [entry["path"] for entry in self._dirs() if entry["install"]]

    # ------------------------------------------------------------------ 状态

    def _watched_files(self) -> list:
        """除 skill 文件夹外，变了也要提醒的单个文件：清单、插件记录、规则文件、各 AI 的 MCP 配置。

        :returns: [Path]
        """
        return ([self.library.manifestPath, plugins.installed_record(), plugins.settings_path(), self.keys.path]
                + [entry["path"] for entry in self._rules_targets()]
                + [target.path for target in self._mcp_targets()])

    def _watch_roots(self, skillRoots: list, files: list) -> list:
        """要让系统盯着的文件夹：skill 目录连子文件夹一起，单个文件只盯它所在的文件夹。
        还不存在的就盯最近一层存在的上级，它被建出来时也能发现。

        :param skillRoots: skill 根目录
        :param files: 单个文件
        :returns: [(Path, 是否含子文件夹)]
        """
        roots = []
        for path, recursive in [(root, True) for root in skillRoots] + [(file.parent, False) for file in files]:
            while not path.is_dir() and path.parent != path:
                path, recursive = path.parent, False
            roots.append((path, recursive))
        return roots

    def stamp(self) -> str:
        """本机和库里相关文件的轻量指纹；变了说明外面有改动（比如 AI 新建、更新了 skill）。

        系统通知说没变时直接返回上次的结果，几乎不花时间；变了才重新算（约 0.1 秒）：
        skill 只看文件名、大小和修改时间，不读内容；MCP 按解析后的内容算，
        因为 ~/.claude.json 平时也会被 Claude Code 频繁改写。
        万一系统通知漏了，最多 60 秒也会重算一次。

        :returns: 短十六进制字符串
        """
        if self._stampValue and time.monotonic() - self._stampAt < StampMaxAge and not self.watcher.take():
            return self._stampValue
        skillRoots = [self.library.skillsDir] + [entry["path"] for entry in self._dirs()]
        files = self._watched_files()
        # 先开始监视再计算，计算途中的改动下次还能发现
        self.watcher.watch(self._watch_roots(skillRoots, files))
        digest = hashlib.sha256()

        def add(text: str) -> None:
            digest.update(text.encode("utf-8", "surrogateescape") + b"\0")

        for root in skillRoots:
            for path in skillfs.list_skills(root).values():
                add(f"{path}\n{skillfs.dir_stamp(path)}")
        mcpFiles = {str(target.path) for target in self._mcp_targets()}
        for path in files:
            if str(path) in mcpFiles:
                continue
            info = path.stat() if path.is_file() else None
            add(f"{path}|{info.st_size}|{info.st_mtime_ns}" if info else str(path))
        for target in self._mcp_targets():
            try:
                servers = target.read()
            except mcp.McpError as error:
                servers = str(error)
            add(json.dumps([target.agentId, servers], sort_keys=True, default=str))
        self._stampValue, self._stampAt = digest.hexdigest()[:16], time.monotonic()
        return self._stampValue

    def _quick_hash(self, path: Path, cache: dict) -> str:
        """界面显示用的内容指纹：文件名、大小、修改时间都没变就用上次算的，不再读文件内容。
        会删改文件的操作仍然直接用 skillfs.dir_hash，不走这个缓存。

        :param path: skill 文件夹
        :param cache: 本次用到的缓存，算完后替换旧缓存（顺带丢掉已经不存在的文件夹）
        :returns: 内容指纹
        """
        key = hashlib.sha1(skillfs.dir_stamp(path).encode("utf-8", "surrogateescape")).digest()
        cached = self._hashCache.get(str(path))
        value = cached[1] if cached and cached[0] == key else skillfs.dir_hash(path)
        cache[str(path)] = (key, value)
        return value

    def state(self) -> dict:
        """生成界面需要的完整状态。

        :returns: {"stamp", "keys", "skills", "groups", "plugins", "mcps", "rules", "agents", "dirs", "git", "config"}
        """
        # 重新扫描时指纹也重算（顺带更新监视的文件夹，比如刚勾选了新的 AI）；
        # 先取指纹再扫描：扫描途中发生的改动，下次比对时还能发现
        self._stampValue = ""
        stamp = self.stamp()
        with self.lock:
            manifest = self.library.load()
            dirs = self._dirs()
            hashCache = {}
            librarySkills = skillfs.list_skills(self.library.skillsDir)
            libraryHashes = {skillId: self._quick_hash(path, hashCache) for skillId, path in librarySkills.items()}

            # 收集每个 skill 在本机的所有副本
            copies = {}
            for entry in dirs:
                for skillId, path in skillfs.list_skills(entry["path"]).items():
                    copies.setdefault(skillId, []).append((entry, path))

            # 合并库和本机，生成每一行
            skills = []
            for skillId in sorted(set(librarySkills) | set(copies)):
                info = manifest["skills"].get(skillId, {})
                inCloud = skillId in librarySkills
                metaSource = librarySkills.get(skillId) or copies[skillId][0][1]
                copyRows = []
                for entry, path in copies.get(skillId, []):
                    status = "local" if not inCloud else (
                        "same" if self._quick_hash(path, hashCache) == libraryHashes[skillId] else "modified")
                    copyRows.append({"dir": str(entry["path"]), "label": entry["label"], "state": status,
                                     "install": entry["install"], "legacy": entry["legacy"], "twin": entry["twin"]})
                enabled = info.get("enabled", False) if inCloud else True
                presentKeys = {_key(Path(row["dir"])) for row in copyRows}
                # 旧目录和它对应的新目录都有这个 skill，同一个 AI 会读到两份
                duplicate = any(row["legacy"] and row["twin"] in presentKeys for row in copyRows)
                missing = [entry["label"] for entry in dirs
                           if entry["install"] and enabled and _key(entry["path"]) not in presentKeys]
                skills.append({
                    "id": skillId, "nameZh": info.get("nameZh", ""), "note": info.get("note", ""),
                    "group": info.get("group", ""), "description": skillfs.read_meta(metaSource)["description"],
                    "inCloud": inCloud, "enabled": enabled, "copies": copyRows, "missing": missing,
                    "modified": any(row["state"] == "modified" for row in copyRows),
                    "duplicate": duplicate,
                })

            self._hashCache = hashCache

            # git 状态失败不影响其他内容显示
            try:
                gitInfo = self.library.status()
            except GitError as error:
                gitInfo = {"error": str(error)}

            return {
                "stamp": stamp, "keys": self.keys.summary(), "skills": skills, "groups": manifest["groups"], "plugins": self._plugin_state(manifest),
                "mcps": self._mcp_state(manifest), "rules": self._rules_state(),
                "agents": self.agent_rows(),
                "dirs": [{"path": str(entry["path"]), "label": entry["label"], "install": entry["install"],
                          "legacy": entry["legacy"], "exists": entry["path"].exists()} for entry in dirs],
                "git": gitInfo,
                "config": {"libraryPath": self.config["libraryPath"], "autoPush": self.config["autoPush"],
                           "remoteUrl": gitInfo.get("remote", ""),
                           "onboarded": bool(self.config.get("onboarded") or gitInfo.get("remote"))},
            }

    def _plugin_state(self, manifest: dict) -> dict:
        """合并已安装插件和清单记录。

        :param manifest: 清单
        :returns: {"items": [...], "available": [...], "error": str}
        """
        # `claude plugin list` 要启动一个 claude 进程（约 1 秒）；Claude 的插件记录没变就用上次的结果
        key = plugins.record_key()
        if not self._pluginCache or self._pluginCache[0] != key:
            try:
                self._pluginCache = (key, plugins.list_installed())
            except (PluginError, OSError, ValueError) as error:
                self._pluginCache = None
                return {"items": [], "available": [], "error": str(error)}
        installed = {plugin["id"]: plugin for plugin in self._pluginCache[1]}

        items = []
        for pluginId in sorted(set(installed) | set(manifest["plugins"])):
            info = manifest["plugins"].get(pluginId, {})
            live = installed.get(pluginId)
            items.append({
                "id": pluginId, "nameZh": info.get("nameZh", ""), "note": info.get("note", ""),
                "group": info.get("group", ""), "inCloud": pluginId in manifest["plugins"],
                "installed": live is not None, "enabled": bool(live and live.get("enabled")),
                "version": live.get("version", "") if live else "", "skills": live["skills"] if live else [],
            })
        return {"items": items, "error": ""}

    # ------------------------------------------------------------------ 提交

    def _commit(self, manifest: dict, message: str, push: bool = False) -> str:
        """保存清单并提交；需要时推送到云端。

        :param manifest: 清单
        :param message: 提交说明
        :param push: 是否尝试推送
        :returns: 给用户看的附加提示（推送失败等），没有则为空
        """
        self.library.save(manifest)
        self.library.commit(message)
        if push and self.config.get("autoPush") and self.library.remote_url():
            try:
                self.library.push()
            except GitError as error:
                return f"已保存到本机库，但推送云端失败：{error}"
        return ""

    def _copies(self, skillId: str, installOnly: bool = False) -> list:
        """找到某个 skill 在本机的所有副本。

        :param skillId: skill 英文名
        :param installOnly: 只看安装目录
        :returns: [Path]
        """
        found = []
        for entry in self._dirs():
            if installOnly and not entry["install"]:
                continue
            path = entry["path"] / skillId
            if skillfs.is_skill_dir(path):
                found.append(path)
        return found

    def _install(self, skillId: str, cleanHashes: set, force: bool, onlyExisting: bool = False) -> list:
        """把库里的正本装到所有安装目录。

        :param skillId: skill 英文名
        :param cleanHashes: 视为"没改过"的指纹集合，这些副本可以直接覆盖
        :param force: 是否连有改动的副本也覆盖
        :param onlyExisting: 只更新已有的副本，不新装
        :returns: 因为有改动而跳过的副本路径
        """
        source = self.library.skill_path(skillId)
        skipped = []
        for directory in self._install_dirs():
            target = directory / skillId
            if onlyExisting and not target.exists():
                continue
            if target.exists() and not force and skillfs.dir_hash(target) not in cleanHashes:
                skipped.append(str(target))
                continue
            skillfs.copy_skill(source, target)
        return skipped

    # ------------------------------------------------------------------ skill 操作

    def enable(self, skillId: str, force: bool = False) -> dict:
        """启用：从库复制到所有目标 AI。

        :param skillId: skill 英文名
        :param force: 覆盖本机有改动的副本
        :returns: 结果
        """
        with self.lock:
            source = self.library.skill_path(skillId)
            if not skillfs.is_skill_dir(source):
                # 仅本机的 skill：用最新那份补装到其他 AI
                copies = self._copies(skillId)
                if not copies:
                    raise UserError(f"找不到 {skillId}")
                newest = max(copies, key=skillfs.newest_mtime)
                for directory in self._install_dirs():
                    if not (directory / skillId).exists():
                        skillfs.copy_skill(newest, directory / skillId)
                return {"message": f"已装到所有目标 AI：{skillId}"}

            skipped = self._install(skillId, {skillfs.dir_hash(source)}, force)
            if skipped and not force:
                return {"needConfirm": f"这些位置的 {skillId} 有本机改动，覆盖会丢失改动：\n" + "\n".join(skipped)
                        + "\n\n其他位置已经装好。要覆盖吗？"}
            manifest = self.library.load()
            manifest["skills"].setdefault(skillId, {})["enabled"] = True
            return {"message": f"已启用 {skillId}", "warning": self._commit(manifest, f"启用 {skillId}")}

    def disable(self, skillId: str, force: bool = False) -> dict:
        """关闭：从本机所有 AI 目录物理删除，库里保留。

        :param skillId: skill 英文名
        :param force: 用户已确认
        :returns: 结果
        """
        with self.lock:
            source = self.library.skill_path(skillId)
            inCloud = skillfs.is_skill_dir(source)
            copies = self._copies(skillId)

            # 可能丢数据时先询问
            if not force:
                if not inCloud:
                    return {"needConfirm": f"{skillId} 云端没有备份。关闭就是从电脑上彻底删除，无法恢复。\n确定删除吗？",
                            "canUpload": True}
                sourceHash = skillfs.dir_hash(source)
                changed = [str(path) for path in copies if skillfs.dir_hash(path) != sourceHash]
                if changed:
                    return {"needConfirm": f"{skillId} 的本机副本有未上传的改动，关闭会丢失：\n" + "\n".join(changed),
                            "canUpload": True}

            for path in copies:
                skillfs.remove_dir(path)
            manifest = self.library.load()
            if inCloud:
                manifest["skills"].setdefault(skillId, {})["enabled"] = False
                return {"message": f"已关闭 {skillId}（云端保留，可随时启用装回）",
                        "warning": self._commit(manifest, f"关闭 {skillId}")}
            manifest["skills"].pop(skillId, None)
            self.library.save(manifest)
            return {"message": f"已彻底删除 {skillId}"}

    def upload(self, skillId: str) -> dict:
        """上传：把本机最新的副本存进库并推送云端。

        :param skillId: skill 英文名
        :returns: 结果
        """
        with self.lock:
            copies = self._copies(skillId)
            if not copies:
                raise UserError(f"本机没有 {skillId}")
            newest = max(copies, key=skillfs.newest_mtime)

            # 上传前扫描密钥
            findings = skillfs.scan_secrets(newest)
            if findings:
                detail = "\n".join(f"{item['file']} 第 {item['line']} 行：{item['kind']}" for item in findings[:10])
                raise UserError(f"{skillId} 里疑似有密钥，已拦下，没有上传：\n{detail}\n\n"
                                "请把密钥移出文件（改用环境变量），再上传。")

            # 复制前记下旧正本的指纹，用来判断其他副本是否改过
            target = self.library.skill_path(skillId)
            oldHash = skillfs.dir_hash(target)
            skillfs.copy_skill(newest, target)
            manifest = self.library.load()
            manifest["skills"].setdefault(skillId, {})["enabled"] = True
            warning = self._commit(manifest, f"上传 {skillId}", push=True)

            # 本机其他没改过的副本更新成新版本（不新装，新装交给一键同步）
            skipped = self._install(skillId, {oldHash, skillfs.dir_hash(target)} - {""}, False, onlyExisting=True)
            message = f"已上传 {skillId}（来源：{newest}）"
            if skipped:
                message += "\n这些位置另有改动，没有覆盖：\n" + "\n".join(skipped)
            return {"message": message, "warning": warning}

    def delete(self, skillId: str, force: bool = False) -> dict:
        """彻底删除：库（云端）和本机全部删除。

        :param skillId: skill 英文名
        :param force: 用户已确认
        :returns: 结果
        """
        with self.lock:
            source = self.library.skill_path(skillId)
            inCloud = skillfs.is_skill_dir(source)
            if not force:
                where = "云端和本机" if inCloud else "本机（云端本来就没有）"
                return {"needConfirm": f"彻底删除 {skillId}：从{where}全部删掉，无法恢复。\n确定吗？"}

            for path in self._copies(skillId):
                skillfs.remove_dir(path)
            skillfs.remove_dir(source)
            manifest = self.library.load()
            manifest["skills"].pop(skillId, None)
            return {"message": f"已彻底删除 {skillId}", "warning": self._commit(manifest, f"删除 {skillId}", push=True)}

    def edit(self, kind: str, itemId: str, fields: dict) -> dict:
        """修改中文名、介绍、分组。

        :param kind: "skill"、"plugin" 或 "mcp"
        :param itemId: skill 英文名、插件 id 或 MCP id
        :param fields: 要改的字段，只接受 nameZh / note / group
        :returns: 结果
        """
        with self.lock:
            manifest = self.library.load()
            section = {"skill": manifest["skills"], "mcp": manifest["mcps"]}.get(kind, manifest["plugins"])
            entry = section.setdefault(itemId, {})
            for key in ("nameZh", "note", "group"):
                if key in fields:
                    entry[key] = str(fields[key]).strip()
            return {"message": "已保存", "warning": self._commit(manifest, f"编辑 {itemId}")}

    def upload_all(self) -> dict:
        """上传所有"仅本机"和"本机有改动"的 skill。

        :returns: 结果，含失败列表
        """
        with self.lock:
            pending = [row["id"] for row in self.state()["skills"] if not row["inCloud"] or row["modified"]]
            done, failed = [], []
            for skillId in pending:
                try:
                    self.upload(skillId)
                    done.append(skillId)
                except UserError as error:
                    failed.append(f"{skillId}：{error}")
            message = f"上传了 {len(done)} 个"
            if failed:
                message += f"，{len(failed)} 个失败：\n" + "\n\n".join(failed)
            return {"message": message}

    def sync_all(self, baseHashes: dict = None, baseMcps: dict = None, force: bool = False) -> dict:
        """一键同步：让所有目标 AI 和云端清单一致。

        启用的装上（缺的补上、没改过的更新），关闭的删掉（只删没改过的），插件按清单安装，
        MCP 同理；规则文件只给还没有的 AI 写上。

        :param baseHashes: 拉取前的正本指纹；这些也算"没改过"
        :param baseMcps: 拉取前的 MCP 清单记录；和它一样的配置也算"没改过"
        :param force: 本机改过的副本和 MCP 配置也用云端的覆盖（关闭的直接删）；规则文件仍只补没有的
        :returns: 结果
        """
        with self.lock:
            manifest = self.library.load()
            installed, removed, skipped = 0, 0, []
            for skillId, path in skillfs.list_skills(self.library.skillsDir).items():
                enabled = manifest["skills"].get(skillId, {}).get("enabled", False)
                clean = {skillfs.dir_hash(path), (baseHashes or {}).get(skillId, "")} - {""}
                if enabled:
                    before = len(self._copies(skillId, installOnly=True))
                    skipped += self._install(skillId, clean, force)
                    installed += max(0, len(self._install_dirs()) - before)
                    continue
                for copy in self._copies(skillId):
                    if force or skillfs.dir_hash(copy) in clean:
                        skillfs.remove_dir(copy)
                        removed += 1
                    else:
                        skipped.append(str(copy))

            pluginMessage = self._sync_plugins(manifest)
            mcpMessage, mcpSkipped = self._sync_mcps(manifest, baseMcps, force)
            rulesMessage, rulesSkipped = self._sync_rules()
            skipped += mcpSkipped + rulesSkipped
            message = f"同步完成：新装 {installed} 处，删除 {removed} 处。{mcpMessage}{rulesMessage}{pluginMessage}"
            if skipped:
                message += "\n这些副本有本机改动，没有动：\n" + "\n".join(skipped)
            return {"message": message}

    def upload_everything(self) -> dict:
        """一键上传：本机所有改动（skill、MCP、插件）存进云端库，和云端自动合并后推送。

        全局规则只在云端还没有时导入一份；各 AI 的规则文件可能故意不同（例如某个 AI 专用的段落），
        不自动拿哪一份当正本，在「全局规则」页手动处理。

        :returns: 结果
        """
        if not self.library.remote_url():
            raise UserError("还没有连接云端仓库，先在设置里连接")
        with self.lock:
            # 逐个上传时先不推送，最后合并后统一推一次
            autoPush = self.config.get("autoPush")
            self.config["autoPush"] = False
            try:
                lines = ["skill：" + self.upload_all()["message"]]
                state = self.state()
                done, failed = [], []
                for item in state["mcps"]["items"]:
                    if item["inCloud"] and not item["modified"]:
                        continue
                    try:
                        self.mcp_upload(item["id"])
                        done.append(item["name"])
                    except UserError as error:
                        failed.append(f"{item['name']}：{error}")
                for item in state["plugins"]["items"]:
                    if item["installed"] and not item["inCloud"]:
                        try:
                            self.plugin_upload(item["id"])
                            done.append(item["id"])
                        except UserError as error:
                            failed.append(f"{item['id']}：{error}")
                if not state["rules"]["inCloud"]:
                    source = next((row for row in state["rules"]["targets"] if row["exists"]), None)
                    if source:
                        self.rules_import(source["path"])
                        lines.append(f"全局规则：云端还没有，用 {source['label']} 的规则文件建了正本")
            finally:
                self.config["autoPush"] = autoPush
            if done:
                lines.append(f"MCP 和插件：上传了 {len(done)} 个（{'、'.join(done)}）")
            if failed:
                lines.append("这些没上传成功：\n" + "\n".join(failed))
            try:
                self.library.commit("上传本机改动")
                lines.insert(0, self.library.pull())
                self.library.push()
            except GitError as error:
                raise UserError(f"推送云端失败：{error}")
            lines.insert(0, "已上传到云端，并和云端的改动合并。")
            return {"message": "\n".join(lines)}

    def local_changes(self) -> list:
        """本机和云端不一样、拉取覆盖时会被换掉的东西。

        :returns: 说明列表
        """
        state = self.state()
        rows = [f"skill {item['id']}" for item in state["skills"] if item["inCloud"] and item["modified"]]
        rows += [f"MCP {item['name']}" for item in state["mcps"]["items"] if item["inCloud"] and item["modified"]]
        return rows

    def pull_overwrite(self, force: bool = False) -> dict:
        """一键拉取覆盖：从云端拉取，本机所有 skill 副本和 MCP 配置都换成云端版本（规则文件不动）。

        :param force: 用户已确认会丢掉本机改动
        :returns: 结果
        """
        if not force:
            changed = self.local_changes()
            if changed:
                return {"needConfirm": "这些本机改动还没上传，会被云端版本覆盖（建议先点「上传」）：\n"
                                       + "\n".join(changed) + "\n\n确定覆盖吗？"}
        return self.update(force=True)

    def update(self, force: bool = False) -> dict:
        """一键更新：从云端拉取，再同步到所有 AI，再更新插件。

        :param force: 本机改过的也用云端版本覆盖
        :returns: 结果
        """
        with self.lock:
            baseHashes = {skillId: skillfs.dir_hash(path)
                          for skillId, path in skillfs.list_skills(self.library.skillsDir).items()}
            baseMcps = self.library.load()["mcps"]
            if self.library.remote_url():
                try:
                    pullMessage = self.library.pull()
                    # 合并出了新提交（或之前有没推上去的），顺手推上去，免得两台电脑一直分叉
                    if self.config.get("autoPush") and self.library.status()["ahead"]:
                        self.library.push()
                        pullMessage += "，并已推送本机的改动"
                except GitError as error:
                    raise UserError(f"从云端拉取失败：{error}")
            else:
                pullMessage = "没有设置云端仓库，跳过拉取"
            result = self.sync_all(baseHashes, baseMcps, force)

            # 更新插件市场和已安装插件
            notes = []
            try:
                installed = [item for item in plugins.list_installed()]
                if installed:
                    plugins.update_marketplaces()
                for plugin in installed:
                    plugins.action("update", plugin["id"])
            except (PluginError, OSError, ValueError) as error:
                notes.append(f"插件更新失败：{error}")
            return {"message": f"{pullMessage}\n{result['message']}" + ("\n" + "\n".join(notes) if notes else "")}

    def clean_duplicates(self) -> dict:
        """清理旧目录里的重复副本（例如 ~/.codex/skills 和通用目录里都有同一个 skill）。

        只删和正本或通用目录副本完全一样的；不一样的保留并列出来。

        :returns: 结果
        """
        with self.lock:
            dirs = self._dirs()
            others = [entry["path"] for entry in dirs if not entry["legacy"]]
            removed, kept = [], []
            for entry in dirs:
                if not entry["legacy"]:
                    continue
                for skillId, path in skillfs.list_skills(entry["path"]).items():
                    twins = [directory / skillId for directory in others + [self.library.skillsDir]]
                    twinHashes = {skillfs.dir_hash(twin) for twin in twins if skillfs.is_skill_dir(twin)}
                    if not twinHashes:
                        continue
                    if skillfs.dir_hash(path) in twinHashes:
                        skillfs.remove_dir(path)
                        removed.append(str(path))
                    else:
                        kept.append(str(path))
            message = f"清理了 {len(removed)} 个重复副本。"
            if kept:
                message += "\n这些和新位置内容不同，保留了（请先比较再手动处理）：\n" + "\n".join(kept)
            return {"message": message}

    # ------------------------------------------------------------------ 分组

    def group_add(self, name: str) -> dict:
        """新建分组。

        :param name: 分组名
        :returns: 结果
        """
        with self.lock:
            if not name.strip():
                raise UserError("分组名不能为空")
            manifest = self.library.load()
            manifest["groups"].append({"id": uuid.uuid4().hex[:8], "name": name.strip(), "note": ""})
            return {"message": f"已新建分组 {name}", "warning": self._commit(manifest, f"新建分组 {name}")}

    def group_edit(self, groupId: str, name: str, note: str) -> dict:
        """改分组名和备注。

        :param groupId: 分组 id
        :param name: 新名字
        :param note: 新备注
        :returns: 结果
        """
        with self.lock:
            manifest = self.library.load()
            for group in manifest["groups"]:
                if group["id"] == groupId:
                    group["name"], group["note"] = name.strip() or group["name"], note.strip()
            return {"message": "已保存", "warning": self._commit(manifest, f"编辑分组 {name}")}

    def group_delete(self, groupId: str) -> dict:
        """删除分组（组里的 skill 和插件不删，只是变成未分组）。

        :param groupId: 分组 id
        :returns: 结果
        """
        with self.lock:
            manifest = self.library.load()
            manifest["groups"] = [group for group in manifest["groups"] if group["id"] != groupId]
            for section in (manifest["skills"], manifest["plugins"], manifest["mcps"]):
                for entry in section.values():
                    if entry.get("group") == groupId:
                        entry["group"] = ""
            return {"message": "已删除分组", "warning": self._commit(manifest, "删除分组")}

    def group_toggle(self, groupId: str, enabled: bool, force: bool = False) -> dict:
        """整组启用或关闭。

        :param groupId: 分组 id
        :param enabled: True 启用，False 关闭
        :param force: 用户已确认
        :returns: 结果
        """
        with self.lock:
            state = self.state()
            skillIds = [row["id"] for row in state["skills"] if row["group"] == groupId]
            pluginIds = [row["id"] for row in state["plugins"]["items"] if row["group"] == groupId]
            mcpIds = [row["id"] for row in state["mcps"]["items"] if row["group"] == groupId]

            # 关闭时先汇总所有需要确认的项
            if not enabled and not force:
                risky = [row["id"] for row in state["skills"] + state["mcps"]["items"]
                         if row["group"] == groupId and (not row["inCloud"] or row["modified"])]
                if risky:
                    return {"needConfirm": "这些 skill / MCP 云端没有备份或本机有改动，关闭会丢失：\n"
                                           + "\n".join(risky) + "\n\n确定整组关闭吗？"}

            messages = []
            for skillId in skillIds:
                result = self.enable(skillId, force) if enabled else self.disable(skillId, True)
                messages.append(result.get("needConfirm") or result.get("message", ""))
            for mcpId in mcpIds:
                result = self.mcp_enable(mcpId, force) if enabled else self.mcp_disable(mcpId, True)
                messages.append(result.get("needConfirm") or result.get("message", ""))
            for pluginId in pluginIds:
                result = self.plugin_enable(pluginId) if enabled else self.plugin_disable(pluginId, True)
                messages.append(result.get("needConfirm") or result.get("message", ""))
            return {"message": "\n".join(messages) or "组里还没有内容"}

    # ------------------------------------------------------------------ 插件

    def _ensure_marketplace(self, pluginId: str, manifest: dict) -> None:
        """安装插件前，确保它所在的市场已添加（新电脑上需要）。

        :param pluginId: 插件 id，形如 name@marketplace
        :param manifest: 清单
        """
        marketName = pluginId.split("@", 1)[1] if "@" in pluginId else ""
        if not marketName:
            return
        known = {market["name"] for market in plugins.list_marketplaces()}
        if marketName not in known:
            source = manifest["marketplaces"].get(marketName)
            if not source:
                raise UserError(f"不知道市场 {marketName} 的来源，请先在插件页添加这个市场")
            plugins.add_marketplace(source)

    def plugin_enable(self, pluginId: str) -> dict:
        """启用插件：没装就安装，装了没启用就启用。

        :param pluginId: 插件 id
        :returns: 结果
        """
        with self.lock:
            manifest = self.library.load()
            try:
                live = {plugin["id"]: plugin for plugin in plugins.list_installed()}.get(pluginId)
                if live is None:
                    self._ensure_marketplace(pluginId, manifest)
                    plugins.action("install", pluginId)
                elif not live.get("enabled"):
                    plugins.action("enable", pluginId)
            except PluginError as error:
                raise UserError(str(error))
            warning = ""
            if pluginId in manifest["plugins"]:
                manifest["plugins"][pluginId]["enabled"] = True
                warning = self._commit(manifest, f"启用插件 {pluginId}")
            return {"message": f"已启用插件 {pluginId}（重开 Claude Code 会话后生效）", "warning": warning}

    def plugin_disable(self, pluginId: str, force: bool = False) -> dict:
        """关闭插件：从本机卸载（保留插件数据），云端记录保留。

        :param pluginId: 插件 id
        :param force: 用户已确认
        :returns: 结果
        """
        with self.lock:
            manifest = self.library.load()
            if pluginId not in manifest["plugins"] and not force:
                return {"needConfirm": f"{pluginId} 还没上传到云端清单。关闭会卸载它，以后得自己重新找来安装。\n确定吗？",
                        "canUpload": True}
            try:
                plugins.run_claude("plugin", "uninstall", pluginId, "-s", "user", "--keep-data")
            except PluginError as error:
                raise UserError(str(error))
            warning = ""
            if pluginId in manifest["plugins"]:
                manifest["plugins"][pluginId]["enabled"] = False
                warning = self._commit(manifest, f"关闭插件 {pluginId}")
            return {"message": f"已卸载插件 {pluginId}", "warning": warning}

    def plugin_upload(self, pluginId: str) -> dict:
        """把插件记到云端清单（记录名字和市场来源，不复制插件文件）。

        :param pluginId: 插件 id
        :returns: 结果
        """
        with self.lock:
            manifest = self.library.load()
            marketName = pluginId.split("@", 1)[1] if "@" in pluginId else ""
            try:
                for market in plugins.list_marketplaces():
                    if market["name"] == marketName:
                        manifest["marketplaces"][marketName] = plugins.marketplace_source(market)
            except PluginError as error:
                raise UserError(str(error))
            entry = manifest["plugins"].setdefault(pluginId, {})
            entry["enabled"] = True
            return {"message": f"已记录插件 {pluginId}",
                    "warning": self._commit(manifest, f"上传插件 {pluginId}", push=True)}

    def plugin_delete(self, pluginId: str, force: bool = False) -> dict:
        """彻底删除插件：卸载（连数据一起）并从云端清单移除。

        :param pluginId: 插件 id
        :param force: 用户已确认
        :returns: 结果
        """
        with self.lock:
            if not force:
                return {"needConfirm": f"彻底删除插件 {pluginId}：卸载、删除插件数据、从云端清单移除。\n确定吗？"}
            installed = {plugin["id"] for plugin in plugins.list_installed()}
            try:
                if pluginId in installed:
                    plugins.run_claude("plugin", "uninstall", pluginId, "-s", "user")
            except PluginError as error:
                raise UserError(str(error))
            manifest = self.library.load()
            manifest["plugins"].pop(pluginId, None)
            return {"message": f"已彻底删除插件 {pluginId}",
                    "warning": self._commit(manifest, f"删除插件 {pluginId}", push=True)}

    def plugin_add_market(self, source: str) -> dict:
        """添加插件市场。

        :param source: owner/repo、git 地址或本地路径
        :returns: 结果
        """
        with self.lock:
            try:
                result = plugins.add_marketplace(source.strip())
            except PluginError as error:
                raise UserError(str(error))
            return {"message": result.get("message", "已添加市场")}

    def plugin_available(self) -> dict:
        """列出市场里可安装的插件。

        :returns: {"items": [...]}
        """
        try:
            return {"items": plugins.list_available()}
        except (PluginError, ValueError) as error:
            raise UserError(str(error))

    def _sync_plugins(self, manifest: dict) -> str:
        """按清单安装应启用但没装的插件。

        :param manifest: 清单
        :returns: 结果说明
        """
        wanted = [pluginId for pluginId, info in manifest["plugins"].items() if info.get("enabled")]
        if not wanted:
            return ""
        try:
            installed = {plugin["id"] for plugin in plugins.list_installed()}
        except (PluginError, OSError, ValueError) as error:
            return f"插件同步失败：{error}"
        failed = []
        count = 0
        for pluginId in wanted:
            if pluginId in installed:
                continue
            try:
                self._ensure_marketplace(pluginId, manifest)
                plugins.action("install", pluginId)
                count += 1
            except (PluginError, UserError) as error:
                failed.append(f"{pluginId}：{error}")
        return f"插件新装 {count} 个。" + ("\n插件失败：\n" + "\n".join(failed) if failed else "")

    # ------------------------------------------------------------------ 设置

    def set_targets(self, agentIds: list) -> dict:
        """设置要安装到哪些 AI。

        :param agentIds: AI id 列表
        :returns: 结果
        """
        with self.lock:
            self.config["targets"] = list(agentIds)
            cfg.save_config(self.config)
            return {"message": "已保存。点「一键同步」把 skill 装到新勾选的 AI。"}

    def set_settings(self, remoteUrl: str, autoPush: bool) -> dict:
        """设置云端仓库地址和是否自动推送。

        :param remoteUrl: 远程仓库地址，空表示不用云端
        :param autoPush: 上传和删除后是否自动推送
        :returns: 结果
        """
        with self.lock:
            try:
                self.library.set_remote(remoteUrl.strip())
            except GitError as error:
                raise UserError(str(error))
            self.config["remoteUrl"] = remoteUrl.strip()
            self.config["autoPush"] = bool(autoPush)
            cfg.save_config(self.config)
            return {"message": "已保存设置"}

    def connect(self, remoteUrl: str) -> dict:
        """连接云端仓库，并按两边的情况自动对齐。

        - 云端是空的：把本机库推上去。
        - 本机库是空的：直接用云端内容（换新电脑的情况）。
        - 两边都有内容：能快进就拉取，否则报错让用户决定，绝不自动覆盖。

        :param remoteUrl: 仓库地址
        :returns: 结果
        """
        with self.lock:
            url = remoteUrl.strip()
            if not url:
                raise UserError("请填写仓库地址")
            try:
                self.library.set_remote(url)
                self.library.git("fetch", "origin")
                remoteHead = self.library.git("rev-parse", "--verify", "origin/main", check=False)
                localEmpty = not skillfs.list_skills(self.library.skillsDir)

                # 按两边是否有内容分别处理
                if not remoteHead:
                    self.library.commit("初始化 skill 库")
                    message = "已连接，本机内容已推送到空的云端仓库" if self.library.has_commits() else "已连接空仓库"
                    if self.library.has_commits():
                        self.library.push()
                elif not self.library.has_commits() or localEmpty:
                    self.library.git("reset", "--hard", "origin/main")
                    self.library.git("branch", "--set-upstream-to=origin/main", check=False)
                    message = "已连接，并下载了云端内容。点「一键同步」装到本机所有 AI。"
                else:
                    self.library.pull()
                    self.library.push()
                    message = "已连接，本机和云端已对齐"
            except GitError as error:
                raise UserError(f"连接失败：{error}\n\n如果两边都已有不同的内容，请先在库目录手动处理，或换一个空仓库。")
            self.config["remoteUrl"] = url
            self.config["onboarded"] = True
            cfg.save_config(self.config)
            return {"message": message}

    def github_status(self) -> dict:
        """返回 git / GitHub CLI 的可用和登录状态。

        :returns: 状态
        """
        return github.status()

    def github_login(self) -> dict:
        """打开 GitHub CLI 登录窗口。

        :returns: 结果
        """
        if not github.status()["gh"]:
            raise UserError("没有安装 GitHub CLI。可以改为手动填写仓库地址，推送时 git 会弹出登录。")
        github.open_login()
        return {"message": "已打开登录窗口，按提示在浏览器里授权，完成后回来点「我已登录」"}

    def github_create(self, name: str) -> dict:
        """在登录的 GitHub 账号下创建私有仓库并连接。

        :param name: 仓库名
        :returns: 结果
        """
        name = name.strip() or "my-skills"
        try:
            url = github.create_private_repo(name)
        except (RuntimeError, OSError) as error:
            raise UserError(f"创建仓库失败：{error}")
        result = self.connect(url)
        return {"message": f"私有仓库 {name} 已就绪。\n{result['message']}", "url": url}

    def skip_onboarding(self) -> dict:
        """跳过首次引导，只在本机使用。

        :returns: 结果
        """
        with self.lock:
            self.config["onboarded"] = True
            cfg.save_config(self.config)
            return {"message": "好的，先只在本机使用，随时可以在设置里连接云端"}

    def push(self) -> dict:
        """把本机库的提交推送到云端。

        :returns: 结果
        """
        with self.lock:
            try:
                self.library.commit("同步清单")
                return {"message": self.library.push() or "已推送"}
            except GitError as error:
                raise UserError(f"推送失败：{error}")
