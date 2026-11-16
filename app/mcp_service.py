"""
MCP 工具和全局规则文档的业务逻辑，作为 SkillService 的一部分（混入类）。

约定和 skill 一样：
- 库里的清单（manifest.json 的 "mcps"）是正本；各 AI 配置文件里的是副本。
- MCP 的 id 是名字的小写（不同 AI 里大小写可能不同），写回时沿用那个 AI 里原来的名字。
- 像密钥的环境变量 / 请求头只上传名字，值留在本机；安装时从本机其他 AI 的副本里补上。
- 全局规则正本在库的 rules/global.md，应用时写到各 AI 的规则文件（CLAUDE.md、AGENTS.md、GEMINI.md 等）。
"""

import json
from pathlib import Path

import config as cfg
import mcp
import skillfs

# 改了 MCP 以后各 AI 要重开才生效
RestartNote = "（重开对应的 AI 后生效）"


def _rules_text(path: Path) -> str:
    """读规则文件，统一换行，便于比较。

    :param path: 文件
    :returns: 文本；不存在返回空
    """
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def _secret_kinds(text: str) -> list:
    """检查文本里有没有疑似密钥。

    :param text: 文本
    :returns: 发现的密钥类型
    """
    return [kind for kind, pattern in skillfs.SecretPatterns.items() if pattern.search(text)]


class McpMixin:
    """MCP 和全局规则操作。依赖 SkillService 的 lock、library、agentsDef、agent_rows、_commit。"""

    # ------------------------------------------------------------------ MCP 扫描

    def _mcp_targets(self) -> list:
        """列出要管理的 AI 的 MCP 配置：勾选了的 AI 装到这里，没勾选但配置存在的只读出来。

        :returns: [mcp.Target]，每个多一个 install 属性
        """
        targets = []
        for agent, row in zip(self.agentsDef, self.agent_rows()):
            if "mcp" not in agent:
                continue
            target = mcp.Target(agent["id"], agent["name"], agent["mcp"], agent.get("mcpIgnore", []))
            target.install = row["active"]
            if target.install or target.path.is_file():
                targets.append(target)
        return targets

    def _mcp_scan(self, targets: list) -> tuple:
        """读出所有 AI 的 MCP 服务器。

        :param targets: _mcp_targets 的结果
        :returns: ({id: [(target, 原名, spec)]}, [错误信息])
        """
        copies, errors = {}, []
        for target in targets:
            try:
                servers = target.read()
            except mcp.McpError as error:
                errors.append(str(error))
                continue
            for name, spec in servers.items():
                copies.setdefault(name.lower(), []).append((target, name, spec))
        return copies, errors

    @staticmethod
    def _mcp_secrets(info: dict, found: list) -> list:
        """某个 MCP 的密钥名字：云端记录的加上本机副本里看出来的。

        :param info: 清单里的记录
        :param found: 本机副本
        :returns: 名字列表
        """
        names = set(info.get("secretEnv", []))
        for _target, _name, spec in found:
            names.update(mcp.secret_names(spec))
        return sorted(names)

    @staticmethod
    def _mcp_fill(spec: dict, names: list, found: list) -> tuple:
        """把空着的密钥值从本机其他副本里补上。

        :param spec: 云端的 spec（密钥值是空的）
        :param names: 密钥名字
        :param found: 本机副本
        :returns: (补好的 spec, 仍然缺值的名字)
        """
        filled = json.loads(json.dumps(spec))
        table = mcp.secret_table(filled)
        lacking = []
        for name in names:
            if table is None or table.get(name):
                continue
            values = [mcp.secret_table(other).get(name) for _t, _n, other in found
                      if (mcp.secret_table(other) or {}).get(name)]
            if values:
                table[name] = values[0]
            elif name in table:
                lacking.append(name)
        return filled, lacking

    def _mcp_state(self, manifest: dict) -> dict:
        """生成 MCP 列表。

        :param manifest: 清单
        :returns: {"items": [...], "errors": [...], "targets": [...]}
        """
        targets = self._mcp_targets()
        copies, errors = self._mcp_scan(targets)
        items = []
        for mcpId in sorted(set(manifest["mcps"]) | set(copies)):
            info = manifest["mcps"].get(mcpId, {})
            found = copies.get(mcpId, [])
            inCloud = "spec" in info
            if not inCloud and not found:
                continue
            names = self._mcp_secrets(info, found)
            copyRows = []
            for target, name, spec in found:
                status = "local" if not inCloud else ("same" if mcp.same(spec, info["spec"], names) else "modified")
                copyRows.append({"label": target.name, "dir": str(target.path), "name": name,
                                 "state": status, "install": target.install})
            enabled = info.get("enabled", False) if inCloud else True
            present = {row["label"] for row in copyRows}
            spec = info["spec"] if inCloud else found[0][2]
            items.append({
                "id": mcpId, "name": info.get("name") or found[0][1], "nameZh": info.get("nameZh", ""),
                "note": info.get("note", ""), "group": info.get("group", ""), "description": mcp.summary(spec),
                "spec": mcp.masked(spec, names), "secretEnv": names, "inCloud": inCloud, "enabled": enabled,
                "copies": copyRows, "missing": [t.name for t in targets if t.install and enabled and t.name not in present],
                "modified": any(row["state"] == "modified" for row in copyRows), "duplicate": False,
            })
        return {"items": items, "errors": errors,
                "targets": [{"label": t.name, "path": str(t.path), "install": t.install} for t in targets]}

    def _mcp_find(self, mcpId: str) -> tuple:
        """找到某个 MCP 的清单记录和本机副本。

        :param mcpId: id（小写名字）
        :returns: (manifest, info, targets, found)
        """
        manifest = self.library.load()
        targets = self._mcp_targets()
        copies, errors = self._mcp_scan(targets)
        if errors:
            raise self.UserError("\n".join(errors))
        info = manifest["mcps"].get(mcpId, {})
        found = copies.get(mcpId, [])
        if "spec" not in info and not found:
            raise self.UserError(f"找不到 MCP {mcpId}")
        return manifest, info, targets, found

    def _mcp_write(self, target, name: str, spec: dict) -> None:
        """写一个 AI 的配置，错误转成给用户看的消息。

        :param target: mcp.Target
        :param name: 服务器名
        :param spec: 统一格式
        """
        try:
            target.write(name, spec)
        except (mcp.McpError, OSError, ValueError) as error:
            raise self.UserError(f"{target.name}：{error}")

    def _mcp_remove(self, target, name: str) -> None:
        """从一个 AI 的配置里删除，错误转成给用户看的消息。

        :param target: mcp.Target
        :param name: 服务器名
        """
        try:
            target.remove(name)
        except (mcp.McpError, OSError, ValueError) as error:
            raise self.UserError(f"{target.name}：{error}")

    # ------------------------------------------------------------------ MCP 操作

    def mcp_enable(self, mcpId: str, force: bool = False) -> dict:
        """启用：把这个 MCP 写进所有勾选的 AI。

        :param mcpId: id
        :param force: 覆盖本机有改动的配置
        :returns: 结果
        """
        with self.lock:
            manifest, info, targets, found = self._mcp_find(mcpId)
            names = self._mcp_secrets(info, found)
            inCloud = "spec" in info
            source = info["spec"] if inCloud else next((spec for t, _n, spec in found if t.agentId == "claude"),
                                                        found[0][2] if found else None)
            spec, lacking = self._mcp_fill(source, names, found)
            name = info.get("name") or found[0][1]
            existing = {t.agentId: (n, s) for t, n, s in found}

            skipped = [t.name for t in targets if t.install and t.agentId in existing and not force
                       and not mcp.same(existing[t.agentId][1], source, names)]
            if skipped and inCloud:
                return {"needConfirm": f"这些 AI 里的 {name} 配置和云端不一样，覆盖会丢失本机改动：\n"
                                       + "\n".join(skipped) + "\n\n要覆盖吗？"}
            for target in targets:
                if not target.install:
                    continue
                if target.agentId in existing and (mcp.same(existing[target.agentId][1], source, names) or not inCloud):
                    continue
                self._mcp_write(target, existing.get(target.agentId, (name,))[0], spec)

            message = f"已启用 MCP {name}{RestartNote}"
            if lacking:
                message += f"\n本机找不到这些密钥的值，请到各 AI 配置里填上：{', '.join(lacking)}"
            if not inCloud:
                return {"message": message}
            info["enabled"] = True
            return {"message": message, "warning": self._commit(manifest, f"启用 MCP {name}")}

    def mcp_disable(self, mcpId: str, force: bool = False) -> dict:
        """关闭：从本机所有 AI 的配置里删除，云端保留。

        :param mcpId: id
        :param force: 用户已确认
        :returns: 结果
        """
        with self.lock:
            manifest, info, _targets, found = self._mcp_find(mcpId)
            inCloud = "spec" in info
            name = info.get("name") or found[0][1]
            if not force:
                if not inCloud:
                    return {"needConfirm": f"MCP {name} 云端没有备份。关闭就是从所有 AI 配置里删掉，以后要自己重新配。\n确定吗？",
                            "canUpload": True}
                names = self._mcp_secrets(info, found)
                changed = [t.name for t, _n, spec in found if not mcp.same(spec, info["spec"], names)]
                if changed:
                    return {"needConfirm": f"这些 AI 里的 {name} 配置和云端不一样，关闭会丢失：\n" + "\n".join(changed),
                            "canUpload": True}
            for target, serverName, _spec in found:
                self._mcp_remove(target, serverName)
            if not inCloud:
                return {"message": f"已删除 MCP {name}{RestartNote}"}
            info["enabled"] = False
            return {"message": f"已关闭 MCP {name}（云端保留，可随时启用装回）{RestartNote}",
                    "warning": self._commit(manifest, f"关闭 MCP {name}")}

    def mcp_upload(self, mcpId: str) -> dict:
        """上传：把本机的配置存进云端清单（密钥只存名字）。

        :param mcpId: id
        :returns: 结果
        """
        with self.lock:
            manifest, info, _targets, found = self._mcp_find(mcpId)
            if not found:
                raise self.UserError(f"本机没有 MCP {mcpId}")
            # Claude Code 的配置最常用，优先拿它当正本
            target, name, spec = next((item for item in found if item[0].agentId == "claude"), found[0])
            plain = mcp.scan_plain(spec)
            if plain:
                raise self.UserError(f"{name} 的命令、参数或地址里直接写着疑似密钥（{', '.join(plain)}），已拦下，没有上传。\n"
                                     "请改成用环境变量传密钥，再上传。")
            names = self._mcp_secrets(info, found)
            oldSpec = info.get("spec")
            clean = mcp.strip_secrets(spec, names)
            entry = manifest["mcps"].setdefault(mcpId, {})
            entry.update({"name": info.get("name") or name, "spec": clean, "secretEnv": names, "enabled": True})
            warning = self._commit(manifest, f"上传 MCP {name}", push=True)

            # 其他 AI 里和旧正本一样的配置跟着更新（保留各自的密钥值）
            updated = []
            for other, otherName, otherSpec in found:
                if other is target or not oldSpec or not mcp.same(otherSpec, oldSpec, names):
                    continue
                filled, _lacking = self._mcp_fill(clean, names, [(other, otherName, otherSpec)] + found)
                self._mcp_write(other, otherName, filled)
                updated.append(other.name)
            message = f"已上传 MCP {name}（来源：{target.name}）"
            if names:
                message += f"\n这些密钥只上传了名字，值留在本机：{', '.join(names)}"
            if updated:
                message += f"\n已同步更新：{', '.join(updated)}"
            return {"message": message, "warning": warning}

    def mcp_delete(self, mcpId: str, force: bool = False) -> dict:
        """彻底删除：云端清单和本机所有 AI 配置都删。

        :param mcpId: id
        :param force: 用户已确认
        :returns: 结果
        """
        with self.lock:
            manifest, info, _targets, found = self._mcp_find(mcpId)
            name = info.get("name") or found[0][1]
            if not force:
                where = "云端和所有 AI 配置" if "spec" in info else "所有 AI 配置（云端本来就没有）"
                return {"needConfirm": f"彻底删除 MCP {name}：从{where}里删掉，无法恢复。\n确定吗？"}
            for target, serverName, _spec in found:
                self._mcp_remove(target, serverName)
            manifest["mcps"].pop(mcpId, None)
            return {"message": f"已彻底删除 MCP {name}{RestartNote}",
                    "warning": self._commit(manifest, f"删除 MCP {name}", push=True)}

    def _sync_mcps(self, manifest: dict, baseMcps: dict = None) -> tuple:
        """按清单同步 MCP：启用的补装，关闭的删掉没改过的。

        :param manifest: 清单
        :param baseMcps: 拉取前的清单记录；和它一样的副本也算没改过
        :returns: (说明, 跳过的位置)
        """
        targets = self._mcp_targets()
        copies, errors = self._mcp_scan(targets)
        installed, removed, skipped = 0, 0, list(errors)
        for mcpId, info in manifest["mcps"].items():
            if "spec" not in info:
                continue
            found = copies.get(mcpId, [])
            names = self._mcp_secrets(info, found)
            base = (baseMcps or {}).get(mcpId, {}).get("spec")
            existing = {t.agentId: (n, s) for t, n, s in found}

            def clean(spec):
                return mcp.same(spec, info["spec"], names) or (base is not None and mcp.same(spec, base, names))

            try:
                if info.get("enabled"):
                    spec, _lacking = self._mcp_fill(info["spec"], names, found)
                    for target in targets:
                        if not target.install:
                            continue
                        if target.agentId not in existing:
                            target.write(info.get("name") or mcpId, spec)
                            installed += 1
                        elif not mcp.same(existing[target.agentId][1], info["spec"], names):
                            if clean(existing[target.agentId][1]):
                                target.write(existing[target.agentId][0], spec)
                            else:
                                skipped.append(f"{target.name} 的 MCP {existing[target.agentId][0]}")
                    continue
                for target, serverName, spec in found:
                    if clean(spec):
                        target.remove(serverName)
                        removed += 1
                    else:
                        skipped.append(f"{target.name} 的 MCP {serverName}")
            except (mcp.McpError, OSError, ValueError) as error:
                skipped.append(f"MCP {mcpId}：{error}")
        note = f"MCP 新装 {installed} 处、删除 {removed} 处。" if installed or removed else ""
        return note, skipped

    # ------------------------------------------------------------------ 全局规则

    def _rules_targets(self) -> list:
        """各 AI 的全局规则文件，同一个文件只列一次。

        :returns: [{"path": Path, "label": str, "install": bool}]
        """
        merged = {}
        for agent, row in zip(self.agentsDef, self.agent_rows()):
            if "rules" not in agent:
                continue
            path = cfg.expand_path(agent["rules"])
            entry = merged.setdefault(str(path).lower(), {"path": path, "agents": [], "install": False})
            entry["agents"].append(agent["name"])
            entry["install"] = entry["install"] or row["active"]
        result = [entry for entry in merged.values() if entry["install"] or entry["path"].is_file()]
        for entry in result:
            entry["label"] = " / ".join(entry["agents"])
        return result

    def _rules_state(self) -> dict:
        """生成全局规则页面要的状态。

        :returns: {"inCloud", "text", "targets": [{"path", "label", "exists", "state", "install"}]}
        """
        master = _rules_text(self.library.rulesPath)
        inCloud = self.library.rulesPath.is_file()
        rows = []
        for entry in self._rules_targets():
            text = _rules_text(entry["path"])
            exists = entry["path"].is_file()
            state = "missing" if not exists else ("same" if inCloud and text.strip() == master.strip() else "different")
            rows.append({"path": str(entry["path"]), "label": entry["label"], "exists": exists,
                         "state": state, "install": entry["install"]})
        return {"inCloud": inCloud, "text": master, "targets": rows}

    def rules_save(self, text: str) -> dict:
        """保存规则正本并推送云端（不自动写到各 AI，要再点「应用」）。

        :param text: 规则全文
        :returns: 结果
        """
        with self.lock:
            text = text.replace("\r\n", "\n")
            kinds = _secret_kinds(text)
            if kinds:
                raise self.UserError(f"规则里疑似有密钥（{', '.join(kinds)}），已拦下，没有保存。")
            self.library.rulesPath.parent.mkdir(parents=True, exist_ok=True)
            self.library.rulesPath.write_text(text.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
            return {"message": "已保存全局规则。点「应用到所有 AI」写进各 AI 的规则文件。",
                    "warning": self._commit(self.library.load(), "更新全局规则", push=True)}

    def rules_import(self, path: str) -> dict:
        """把某个 AI 现有的规则文件导入成正本。

        :param path: 规则文件路径（必须是已知的规则文件）
        :returns: 结果
        """
        with self.lock:
            known = {str(entry["path"]).lower(): entry["path"] for entry in self._rules_targets()}
            source = known.get(str(path).lower())
            if source is None or not source.is_file():
                raise self.UserError(f"不是可导入的规则文件：{path}")
            result = self.rules_save(_rules_text(source))
            result["message"] = f"已从 {source} 导入。" + result["message"].replace("已保存全局规则。", "")
            return result

    def rules_apply(self, force: bool = False) -> dict:
        """把规则正本写到所有勾选的 AI；原文件先备份到 data/backups。

        :param force: 覆盖内容不同的文件
        :returns: 结果
        """
        with self.lock:
            if not self.library.rulesPath.is_file():
                raise self.UserError("还没有全局规则，先写好保存，或从某个 AI 导入一份")
            master = _rules_text(self.library.rulesPath)
            targets = [entry for entry in self._rules_targets() if entry["install"]]
            different = [str(entry["path"]) for entry in targets
                         if entry["path"].is_file() and _rules_text(entry["path"]).strip() != master.strip()]
            if different and not force:
                return {"needConfirm": "这些规则文件和正本不一样，会被覆盖（原文件先备份到 data/backups）：\n"
                                       + "\n".join(different) + "\n\n要覆盖吗？"}
            written = []
            for entry in targets:
                path = entry["path"]
                if path.is_file() and _rules_text(path).strip() == master.strip():
                    continue
                mcp.backup(path, "rules")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(master, encoding="utf-8", newline="\n")
                written.append(str(path))
            if not written:
                return {"message": "所有 AI 的规则已经是最新的"}
            return {"message": "已写入：\n" + "\n".join(written)}

    def _sync_rules(self) -> tuple:
        """同步时只给还没有规则文件的 AI 写上；已有但不同的只报告，不覆盖。

        :returns: (说明, 跳过的位置)
        """
        if not self.library.rulesPath.is_file():
            return "", []
        master = _rules_text(self.library.rulesPath)
        created, skipped = 0, []
        for entry in self._rules_targets():
            if not entry["install"]:
                continue
            path = entry["path"]
            if not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(master, encoding="utf-8", newline="\n")
                created += 1
            elif _rules_text(path).strip() != master.strip():
                skipped.append(f"规则文件 {path}（和正本不同，去「全局规则」页处理）")
        return (f"规则新写 {created} 处。" if created else ""), skipped
