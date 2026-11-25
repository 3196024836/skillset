"""
一键导出本机全部内容（混入 SkillService）：skill、插件、MCP 工具的清单和 skill 文件，全局规则，密钥，打成一个 zip。

密钥用密码加密，格式和「密钥」页的备份一样，换电脑时选 zip 里的 keys.json 就能用「导入备份」导回；
其他内容是明文。插件和 MCP 只导出清单（重装即可），不导出它们的程序文件。

「备份到云端」：把本机 skill、规则、加密后的密钥都推到云端仓库，再从云端重新下载一份核对，
核对无误后把下载下来的内容打成 zip 放进下载文件夹。
"""

import json
import subprocess
import platform
import re
import time
import zipfile
from pathlib import Path

import config as cfg
import skillfs
from keystore import KeyStoreError, unseal
from library import GitError, GitTimeout
from netproxy import NoWindow, git_env

# 云端库里放加密密钥的位置（和「密钥」页的备份文件同一格式）
CloudKeysPath = "keys/keys.json"

# zip 里第一个看到的说明文件
InventoryName = "清单.md"


def _cell(value) -> str:
    """表格单元格：去掉换行和竖线，免得把 Markdown 表格撑坏。"""
    return ("" if value is None else str(value)).replace("|", "/").replace("\n", " ").strip() or "-"


def _table(head: list, rows: list) -> str:
    """拼一张 Markdown 表格；没有行时返回"无"。"""
    if not rows:
        return "无\n"
    lines = ["| " + " | ".join(head) + " |", "|" + " --- |" * len(head)]
    lines += ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


class ExportMixin:
    """设置页的「导出本机全部」。需要宿主类提供 state()、self.library、self.keys。"""

    def export_all(self, password: str) -> dict:
        """把本机的 skill、插件和 MCP 清单、全局规则、密钥导出成下载文件夹里的一个 zip。

        :param password: 加密密钥用的密码（至少 8 位）
        :returns: {"message", "path"}
        """
        # 先加密密钥：密码不合格时直接报错，不留下半截文件
        keyBackup, keyCount = self.keys.backup(password)
        state = self.state()
        target = cfg.home_dir() / "Downloads" / f"skillset-export-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        target.parent.mkdir(parents=True, exist_ok=True)
        fileCount = 0
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(InventoryName, self._inventory(state))
            archive.writestr("keys.json", json.dumps(keyBackup, indent=1))
            if self.library.manifestPath.is_file():
                archive.write(self.library.manifestPath, "manifest.json")
            if state["rules"]["inCloud"]:
                archive.writestr("rules/global.md", state["rules"]["text"])
            for row in state["rules"]["targets"]:
                if row["exists"]:
                    label = re.sub(r'[\\/:*?"<>|]+', "_", row["label"]).strip()
                    archive.write(row["path"], f"rules/本机/{label}/{Path(row['path']).name}")
            # skill 文件：云端有的取正本，只在本机的取第一份副本
            for skill in state["skills"]:
                source = (self.library.skill_path(skill["id"]) if skill["inCloud"]
                          else Path(skill["copies"][0]["dir"]) / skill["id"])
                for relPath, fullPath in skillfs.iter_files(source):
                    archive.write(fullPath, f"skills/{skill['id']}/{relPath}")
                    fileCount += 1
        return {"message": f"已导出到 {target}\n{len(state['skills'])} 个 skill（{fileCount} 个文件）、"
                           f"{len(state['plugins']['items'])} 个插件、{len(state['mcps']['items'])} 个 MCP 工具、"
                           f"全局规则、{keyCount} 条密钥（已用密码加密）。\n"
                           "除密钥外都是明文，请存到你自己的网盘或 U 盘。",
                "path": str(target)}

    def _inventory(self, state: dict) -> str:
        """zip 里的 清单.md：人能直接看的完整列表，加上怎么恢复。

        :param state: state() 的结果
        :returns: Markdown 文本
        """
        groups = {group["id"]: group["name"] for group in state["groups"]}
        skills = [[s["id"], s["nameZh"], groups.get(s["group"], ""), "启用" if s["enabled"] else "关闭",
                   "云端有" if s["inCloud"] else "只在本机", "、".join(c["label"] for c in s["copies"])]
                  for s in state["skills"]]
        plugins = [[p["id"], p["nameZh"], p["version"],
                    "未安装" if not p["installed"] else "启用" if p["enabled"] else "关闭"]
                   for p in state["plugins"]["items"]]
        mcps = [[m["name"], m["nameZh"], "启用" if m["enabled"] else "关闭", "云端有" if m["inCloud"] else "只在本机",
                 "、".join(c["label"] for c in m["copies"])] for m in state["mcps"]["items"]]
        rules = [[row["label"], row["path"], "有" if row["exists"] else "没有"] for row in state["rules"]["targets"]]
        agents = [[a["name"], "是" if a["active"] else "否", "已安装" if a["installed"] else "没装"] for a in state["agents"]]
        # 密钥只列名字、变量名和个数，绝不写 key 本身
        keys = [[e["name"], e.get("env", ""), len(e["keys"]), sum(k["status"] == "failed" for k in e["keys"])]
                for e in self.keys.list_masked()]
        pluginError = f"\n读取插件列表失败：{state['plugins']['error']}\n" if state["plugins"].get("error") else ""
        return "\n".join([
            f"# Skillset 导出 · {time.strftime('%Y-%m-%d %H:%M')} · {platform.node()}", "",
            f"云端仓库：{state['config'].get('remoteUrl') or '没连'}", "",
            f"## 技能（{len(skills)}）", "文件在 skills/ 下。", "",
            _table(["英文名", "中文名", "分组", "状态", "云端", "装在"], skills),
            f"## 插件（{len(plugins)}）", "只导出列表，在新电脑上从插件市场重装。" + pluginError, "",
            _table(["名字", "中文名", "版本", "状态"], plugins),
            f"## MCP 工具（{len(mcps)}）", "定义在 manifest.json 的 mcps 里（不含密钥）。", "",
            _table(["名字", "中文名", "状态", "云端", "装在"], mcps),
            "## 全局规则", "正本在 rules/global.md，各 AI 实际用的文件在 rules/本机/ 下。", "",
            _table(["AI", "文件", "本机"], rules),
            "## AI 应用", "", _table(["AI", "装 skill", "本机"], agents),
            f"## 密钥（{len(keys)}）", "key 在 keys.json 里，用导出时的密码加密；这里只列名字。", "",
            _table(["名字", "环境变量", "key 个数", "失效"], keys),
            "## 怎么恢复",
            "- 密钥：Skillset「密钥」页 → 导入 → 从备份文件导入，选 keys.json，输入导出时的密码。",
            "- skill：在新电脑上连同一个云端仓库，点「更新」；只在本机的 skill，把 skills/ 里对应文件夹复制到 AI 的 skills 目录，再在 Skillset 里上传。",
            "- 全局规则：「规则」页 → 导入，选 rules/global.md 或 rules/本机/ 下的文件。",
            "- 插件：照上面的列表在 Claude Code 里重装。", ""])

    def cloud_backup(self, password: str) -> dict:
        """一键备份到云端：上传本机 skill、规则、加密密钥并推送，再从云端下载一份核对，打包到下载文件夹。

        :param password: 加密密钥用的密码（至少 8 位）
        :returns: {"message", "path"}
        """
        if not self.library.remote_url():
            raise self.UserError("还没有连接云端仓库，先在设置里连接")
        keyBackup, keyCount = self.keys.backup(password)
        with self.lock:
            # 逐个上传时先不推送，最后统一推一次
            autoPush = self.config.get("autoPush")
            self.config["autoPush"] = False
            try:
                uploadMessage = self.upload_all()["message"]
            finally:
                self.config["autoPush"] = autoPush
            rules = self.state()["rules"]
            if not rules["inCloud"]:
                source = next((row["path"] for row in rules["targets"] if row["exists"]), "")
                if source:
                    self.rules_import(source)
            keysFile = self.library.path / CloudKeysPath
            keysFile.parent.mkdir(parents=True, exist_ok=True)
            keysFile.write_text(json.dumps(keyBackup, indent=1), encoding="utf-8", newline="\n")
            try:
                self.library.commit("备份到云端")
                self.library.pull()
                self.library.push()
            except GitError as error:
                raise self.UserError(f"推送云端失败：{error}")
            localHead = self.library.git("rev-parse", "HEAD")
        return self._verify_cloud(localHead, password, keyCount, uploadMessage)

    def _verify_cloud(self, localHead: str, password: str, keyCount: int, uploadMessage: str) -> dict:
        """从云端重新克隆一份，逐项和本机库核对，通过后打成 zip。

        :returns: {"message", "path"}
        """
        stamp = time.strftime("%Y%m%d-%H%M%S")
        downloads = cfg.home_dir() / "Downloads"
        clone = downloads / f"skillset-cloud-check-{stamp}"
        target = downloads / f"skillset-cloud-backup-{stamp}.zip"
        # 和本机库一样不转换换行，才能逐字节核对
        args = ("clone", "-c", "core.autocrlf=false", "--depth", "1", "--branch", "main", self.library.remote_url(), str(clone))
        try:
            result = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=GitTimeout, env=git_env(args), creationflags=NoWindow)
            if result.returncode != 0:
                raise self.UserError(f"已推送，但从云端下载核对失败：{result.stderr.strip()}")
            problems = []
            cloudHead = subprocess.run(["git", "rev-parse", "HEAD"], cwd=clone, capture_output=True, text=True,
                                       creationflags=NoWindow).stdout.strip()
            if cloudHead != localHead:
                problems.append("云端最新提交和本机不一致")
            localSkills = skillfs.list_skills(self.library.skillsDir)
            cloudSkills = skillfs.list_skills(clone / "skills")
            for skillId, path in localSkills.items():
                if skillId not in cloudSkills:
                    problems.append(f"云端缺少 skill：{skillId}")
                elif skillfs.dir_hash(path) != skillfs.dir_hash(cloudSkills[skillId]):
                    problems.append(f"云端的 {skillId} 和本机不一样")
            if self.library.rulesPath.is_file() and (clone / "rules" / "global.md").read_bytes() != self.library.rulesPath.read_bytes():
                problems.append("云端的全局规则和本机不一样")
            try:
                cloudKeys = json.loads(unseal(json.loads((clone / CloudKeysPath).read_text(encoding="utf-8")), password))
                if len(cloudKeys["entries"]) != keyCount:
                    problems.append("云端的密钥条数和本机不一样")
            except (OSError, ValueError, KeyError, KeyStoreError) as error:
                problems.append(f"云端的密钥备份解不开：{error}")
            if problems:
                raise self.UserError("已推送，但核对云端时发现问题：\n" + "\n".join(problems))
            fileCount = 0
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
                for relPath, fullPath in skillfs.iter_files(clone):
                    archive.write(fullPath, relPath)
                    fileCount += 1
        finally:
            skillfs.remove_dir(clone)
        return {"message": f"已备份到云端，并从云端下载核对通过（{len(localSkills)} 个 skill、全局规则、{keyCount} 条密钥）。\n"
                           f"{uploadMessage}\n下载下来的完整备份：{target}（{fileCount} 个文件）\n"
                           f"密钥在 zip 里的 {CloudKeysPath}，用这次输入的密码加密；恢复时在「密钥」页选它导入。",
                "path": str(target)}
