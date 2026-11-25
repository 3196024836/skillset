"""
两台以上电脑之间同步全部内容（skill、MCP、插件、全局规则、密钥），作为 SkillService 的一部分（混入类）。

两个按钮都是"合并"，不会丢东西：
    拉下来合并：本机改动先收进库 → 和云端合并 → 合并结果装到本机所有 AI
    上传本机所有：同上，最后再推送到云端
本机改过、合并后被别的版本换掉的 skill 副本和规则文件，先备份到 data/backups。

密钥：云端存 keys/keys.json，用"同步密码"加密；每台电脑第一次同步时输一次，
用 Windows 加密（DPAPI）存在本机 data/sync_password.bin，之后不用再输。
合并规则：按名字取并集，同一条里的 key 按值取并集；一边标了失效就算失效；删除不会同步。

另外可以开"自动同步"：启动后和每 30 分钟自动做一次「拉下来合并」（不更新插件、不推送）。
"""

import json
import threading
import time

import config as cfg
import plugins
import portable
import skillfs
from keystore import KeyStoreError, _dpapi, unseal
from library import GitError
from plugins import PluginError

# 云端库里的加密密钥文件
CloudKeysPath = "keys/keys.json"
# 自动同步间隔（秒）
AutoSyncSeconds = 30 * 60


def _key_view(entries: list) -> list:
    """密钥的可比较内容（不含 id、使用时间这些每台电脑不同、天天变的字段）。"""
    return sorted(([entry["name"].lower(), entry.get("note", ""), entry.get("env", ""),
                    [[key["value"], key.get("label", ""), key.get("status", "ok")] for key in entry["keys"]]]
                   for entry in entries), key=lambda item: item[0])


class SyncMixin:
    """全量同步。依赖 SkillService 的 lock、library、keys、config 和 skill / MCP / 规则操作。"""

    # -------------------------------------------------- 本机路径设置

    def paths_state(self) -> dict:
        """:returns: {"values": 本机在用的路径, "defaults": 默认值, "names": 说明}"""
        return {"values": portable.values(), "defaults": portable.defaults(), "names": portable.Names}

    def paths_save(self, values: dict) -> dict:
        """保存本机路径。

        :param values: {名字: 路径}，空的表示用默认值
        :returns: 结果
        """
        with self.lock:
            paths = {}
            for name, value in (values or {}).items():
                value = str(value or "").strip().rstrip("\\/")
                if name not in portable.Names or not value:
                    continue
                if len(value) < 4 or value[1:3] != ":\\":
                    raise self.UserError(f"{portable.Names[name]}要写完整的 Windows 路径，例如 C:\\Projects")
                paths[name] = value
            self.config["paths"] = paths
            cfg.save_config(self.config)
            portable.reload()
            self._hashCache = {}
            return {"message": "已保存本机路径。点「拉下来合并」把 skill、规则、MCP 按新路径重新装一遍。"}

    def set_auto_sync(self, enabled: bool) -> dict:
        """开关自动同步。"""
        with self.lock:
            self.config["autoSync"] = bool(enabled)
            cfg.save_config(self.config)
            return {"message": "已开启自动同步（启动时和每 30 分钟拉一次）" if enabled else "已关闭自动同步"}

    # -------------------------------------------------- 密钥

    def _password_path(self):
        return cfg.data_dir() / "sync_password.bin"

    def _sync_password(self) -> str:
        """:returns: 本机存的同步密码；没存返回空"""
        path = self._password_path()
        if not path.is_file():
            return ""
        try:
            return _dpapi(path.read_bytes(), protect=False).decode("utf-8")
        except (KeyStoreError, OSError, UnicodeDecodeError):
            return ""

    def _cloud_keys(self, password: str):
        """解开云端的密钥文件。

        :returns: 条目列表；云端没有返回 None
        """
        path = self.library.path / CloudKeysPath
        if not path.is_file():
            return None
        try:
            backup = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            raise self.UserError("云端的密钥文件 keys/keys.json 已损坏")
        try:
            return json.loads(unseal(backup, password).decode("utf-8"))["entries"]
        except KeyStoreError as error:
            raise self.UserError(f"同步密码不对，解不开云端的密钥（{error}）")

    def _set_sync_password(self, password: str) -> None:
        """核对并保存同步密码：云端已有密钥文件就必须能解开它。"""
        if len(password) < 8:
            raise self.UserError("同步密码至少 8 位")
        self._cloud_keys(password)
        self._password_path().parent.mkdir(parents=True, exist_ok=True)
        self._password_path().write_bytes(_dpapi(password.encode("utf-8"), protect=True))

    def _keys_need_password(self) -> bool:
        """本机或云端有密钥、但本机还没存同步密码。"""
        if self._sync_password():
            return False
        try:
            hasLocal = bool(self.keys.load())
        except KeyStoreError:
            hasLocal = False
        return hasLocal or (self.library.path / CloudKeysPath).is_file()

    def _sync_keys(self) -> str:
        """合并本机和云端的密钥：云端有本机没有的加进本机；本机有云端没有的写回云端文件。

        :returns: 说明
        """
        password = self._sync_password()
        if not password:
            return ""
        cloud = self._cloud_keys(password) or []
        try:
            added = self.keys.merge_sync(cloud)
            local = self.keys.load()
        except KeyStoreError as error:
            return f"密钥：本机密钥读不了，没同步（{error}）"
        line = f"密钥：从云端加了 {added} 处。" if added else ""
        if _key_view(local) != _key_view(cloud):
            backup, _count = self.keys.backup(password)
            path = self.library.path / CloudKeysPath
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(backup, indent=1), encoding="utf-8", newline="\n")
            line += "本机的密钥已加密写进云端库。"
        return line

    # -------------------------------------------------- 全量同步

    def _absorb_local(self) -> list:
        """把本机所有改动收进库（不推送）：skill、MCP、插件、全局规则。

        :returns: 说明
        """
        lines = []
        upload = self.upload_all()["message"]
        if not upload.startswith("上传了 0 个") or "失败" in upload:
            lines.append("skill：" + upload)
        state = self.state()
        done, failed = [], []
        for item in state["mcps"]["items"]:
            if item["inCloud"] and not item["modified"]:
                continue
            try:
                self.mcp_upload(item["id"])
                done.append(item["name"])
            except self.UserError as error:
                failed.append(f"{item['name']}：{error}")
        for item in state["plugins"]["items"]:
            if item["installed"] and not item["inCloud"]:
                try:
                    self.plugin_upload(item["id"])
                    done.append(item["id"])
                except self.UserError as error:
                    failed.append(f"{item['id']}：{error}")
        if done:
            lines.append(f"MCP 和插件：收进了 {len(done)} 个（{'、'.join(done)}）")
        if failed:
            lines.append("这些没收进云端（本机保留原样）：\n" + "\n".join(failed))
        lines += self._rules_absorb()
        return lines

    def _remove_deleted(self, baseHashes: dict, baseMcps: dict) -> str:
        """别的电脑从云端删掉的 skill / MCP：本机没改过的副本也删掉，免得下次又被当成本机新加的传回去。

        :returns: 说明
        """
        removed = []
        current = skillfs.list_skills(self.library.skillsDir)
        for skillId, baseHash in baseHashes.items():
            if skillId in current:
                continue
            for copy in self._copies(skillId):
                if skillfs.dir_hash(copy) == baseHash:
                    skillfs.remove_dir(copy)
                    removed.append(skillId)
        manifest = self.library.load()
        gone = {mcpId: info for mcpId, info in baseMcps.items() if "spec" in info and "spec" not in manifest["mcps"].get(mcpId, {})}
        if gone:
            copies, _errors = self._mcp_scan(self._mcp_targets())
            for mcpId, info in gone.items():
                for target, name, spec in copies.get(mcpId, []):
                    if self._same_spec(spec, info):
                        self._mcp_remove(target, name)
                        removed.append(f"MCP {name}")
        return f"云端已删除，本机也删了：{'、'.join(sorted(set(removed)))}" if removed else ""

    @staticmethod
    def _same_spec(spec: dict, info: dict) -> bool:
        import mcp
        return mcp.same(spec, info["spec"], info.get("secretEnv", []))

    def _sync(self, push: bool, password: str = "", auto: bool = False) -> dict:
        """全量同步的公共流程。

        :param push: 最后是否推送到云端
        :param password: 这次输入的同步密码（第一次同步时）
        :param auto: 自动同步：不问密码、不更新插件
        :returns: 结果；本机还没存同步密码时返回 needPassword
        """
        if not self.library.remote_url():
            raise self.UserError("还没有连接云端仓库，先在设置里连接")
        with self.lock:
            if password:
                self._set_sync_password(password)
            if not auto and self._keys_need_password():
                return {"needPassword": "第一次在这台电脑同步，需要设一个「同步密码」：密钥用它加密后存到云端。"
                                        "云端已经有密钥时，输入当初设的那个密码。之后这台电脑会记住，不用再输。"}
            # 1. 本机改动收进库（逐个上传时先不推送）
            autoPush = self.config.get("autoPush")
            self.config["autoPush"] = False
            try:
                lines = self._absorb_local()
            finally:
                self.config["autoPush"] = autoPush
            try:
                self.library.commit("同步本机改动")
            except GitError as error:
                raise self.UserError(f"保存到本机库失败：{error}")
            baseHashes = {skillId: skillfs.dir_hash(path)
                          for skillId, path in skillfs.list_skills(self.library.skillsDir).items()}
            baseMcps = self.library.load()["mcps"]

            # 2. 和云端合并（两边都改了同一处时本机优先）
            try:
                lines.insert(0, self.library.pull())
            except GitError as error:
                raise self.UserError(f"从云端拉取失败：{error}")

            # 3. 密钥合并
            keyLine = self._sync_keys()
            if keyLine:
                lines.append(keyLine)
            try:
                self.library.commit("同步密钥")
                if push:
                    self.library.push()
            except GitError as error:
                raise self.UserError(f"推送云端失败：{error}")

            # 4. 合并结果装到本机所有 AI（本机改过的先备份）
            deleted = self._remove_deleted(baseHashes, baseMcps)
            if deleted:
                lines.append(deleted)
            lines.append(self.sync_all(baseHashes, baseMcps, force=True)["message"])
            if not auto:
                lines += self._update_plugins()
            head = "已上传到云端，并和云端合并。" if push else "已从云端拉下来，和本机合并。"
            result = {"message": "\n".join([head] + [line for line in lines if line])}
            if not auto and not self._sync_password() and (self.library.path / CloudKeysPath).is_file():
                result["needPassword"] = "云端有加密的密钥，输入同步密码后就能拉到这台电脑。"
            return result

    def _update_plugins(self) -> list:
        """更新插件市场和已安装插件。

        :returns: 出错说明
        """
        try:
            installed = list(plugins.list_installed())
            if installed:
                plugins.update_marketplaces()
            for plugin in installed:
                plugins.action("update", plugin["id"])
        except (PluginError, OSError, ValueError) as error:
            return [f"插件更新失败：{error}"]
        return []

    def sync_pull(self, password: str = "") -> dict:
        """「拉下来合并」按钮。"""
        return self._sync(False, password)

    def sync_upload(self, password: str = "") -> dict:
        """「上传本机所有」按钮。"""
        return self._sync(True, password)

    # -------------------------------------------------- 自动同步

    def start_auto_sync(self) -> None:
        """后台线程：启动 1 分钟后、之后每 30 分钟做一次「拉下来合并」。"""
        def loop():
            time.sleep(60)
            while True:
                if self.config.get("autoSync", True) and self.library.remote_url():
                    try:
                        result = self._sync(False, auto=True)
                        print(time.strftime("%Y-%m-%d %H:%M:%S"), "自动同步：", result["message"].replace("\n", " | "), flush=True)
                    except Exception as error:  # noqa: BLE001  后台线程不能因为一次失败就停掉
                        print(time.strftime("%Y-%m-%d %H:%M:%S"), "自动同步失败：", error, flush=True)
                time.sleep(AutoSyncSeconds)

        threading.Thread(target=loop, daemon=True, name="auto-sync").start()
