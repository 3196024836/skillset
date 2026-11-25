"""
全局规则的业务逻辑，作为 SkillService 的一部分（混入类）。

- 通用部分的正本在库的 rules/global.md，所有 AI 都用这一份。
- 某个 AI 专用的内容（例如只给 Antigravity 的说明）放在库的 rules/<AI id>.md，
  写进那个 AI 的规则文件时接在通用部分后面，中间用 Marker 这一行隔开。
- 库里存占位符（见 portable），写给 AI 时换成本机路径。
- 每次写规则文件都记下写进去的内容指纹（data/rules_written.json）；之后文件和记录不一样，
  说明是在本机直接改过，同步时先把改动收进库，不会被云端版本盖掉。
"""

import hashlib
import json
from pathlib import Path

import config as cfg
import mcp
import portable
import skillfs

# 通用部分和本 AI 专用部分的分界线
Marker = "<!-- 以下是本 AI 专用的规则（Skillset 管理）。上面是所有 AI 通用的部分，在哪个 AI 里改都会同步到全部 AI -->"


def _rules_text(path: Path) -> str:
    """读规则文件，统一换行，便于比较。

    :param path: 文件
    :returns: 文本；不存在返回空
    """
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def split_rules(text: str) -> tuple:
    """把规则文件拆成通用部分和本 AI 专用部分。

    :param text: 规则文件全文
    :returns: (通用部分, 专用部分)，都去掉首尾空行
    """
    common, _, extra = text.replace("\r\n", "\n").partition(Marker)
    return common.strip("\n"), extra.strip("\n")


def compose_rules(common: str, extra: str) -> str:
    """拼出写给某个 AI 的规则文件全文。

    :param common: 通用部分
    :param extra: 本 AI 专用部分，可以为空
    :returns: 全文（以换行结尾）
    """
    text = common.strip("\n") + "\n"
    if extra.strip():
        text += "\n" + Marker + "\n\n" + extra.strip("\n") + "\n"
    return text


def _fingerprint(text: str) -> str:
    """规则内容指纹：统一换行、路径换成占位符、去掉首尾空白后再算。"""
    normal = portable.collapse(text.replace("\r\n", "\n")).strip()
    return hashlib.sha256(normal.encode("utf-8")).hexdigest()


def _secret_kinds(text: str) -> list:
    """检查文本里有没有疑似密钥。

    :param text: 文本
    :returns: 发现的密钥类型
    """
    return [kind for kind, pattern in skillfs.SecretPatterns.items() if pattern.search(text)]


class RulesMixin:
    """全局规则操作。依赖 SkillService 的 lock、library、agentsDef、agent_rows、_commit。"""

    def _rules_targets(self) -> list:
        """各 AI 的全局规则文件，同一个文件只列一次。

        :returns: [{"path": Path, "id": 第一个 AI 的 id, "label": str, "install": bool}]
        """
        merged = {}
        for agent, row in zip(self.agentsDef, self.agent_rows()):
            if "rules" not in agent:
                continue
            path = cfg.expand_path(agent["rules"])
            entry = merged.setdefault(str(path).lower(), {"path": path, "id": agent["id"], "agents": [], "install": False})
            entry["agents"].append(agent["name"])
            entry["install"] = entry["install"] or row["active"]
        result = [entry for entry in merged.values() if entry["install"] or entry["path"].is_file()]
        for entry in result:
            entry["label"] = " / ".join(entry["agents"])
        return result

    def _extra_path(self, agentId: str) -> Path:
        """某个 AI 专用规则在库里的位置。"""
        return self.library.rulesPath.parent / f"{agentId}.md"

    def _rules_expected(self, entry: dict) -> str:
        """这个规则文件应该是什么内容（库里的写法，带占位符）。"""
        return compose_rules(_rules_text(self.library.rulesPath), _rules_text(self._extra_path(entry["id"])))

    def _rules_same(self, entry: dict) -> bool:
        """规则文件和库里的一样（路径写法不同不算不一样）。"""
        return _fingerprint(_rules_text(entry["path"])) == _fingerprint(self._rules_expected(entry))

    # -------------------------------------------------- 写入记录

    def _written_path(self) -> Path:
        return cfg.data_dir() / "rules_written.json"

    def _written(self) -> dict:
        """:returns: {规则文件路径小写: 上次写进去的内容指纹}"""
        path = self._written_path()
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def _write_rules(self, entry: dict) -> None:
        """把库里的规则写进一个 AI 的规则文件，原文件先备份，并记下写了什么。"""
        path = entry["path"]
        mcp.backup(path, "rules")
        path.parent.mkdir(parents=True, exist_ok=True)
        text = portable.expand(self._rules_expected(entry))
        path.write_text(text, encoding="utf-8", newline="\n")
        self._remember(path, text)

    def _remember(self, path: Path, text: str) -> None:
        """记下这个规则文件现在和库一致的内容，之后再变就是本机改的。"""
        written = self._written()
        if written.get(str(path).lower()) == _fingerprint(text):
            return
        written[str(path).lower()] = _fingerprint(text)
        self._written_path().parent.mkdir(parents=True, exist_ok=True)
        self._written_path().write_text(json.dumps(written, ensure_ascii=False, indent=1), encoding="utf-8")

    # -------------------------------------------------- 页面和手动操作

    def _rules_state(self) -> dict:
        """生成全局规则页面要的状态。

        :returns: {"inCloud", "text", "targets": [{"path", "label", "exists", "state", "install", "extra"}]}
        """
        inCloud = self.library.rulesPath.is_file()
        rows = []
        for entry in self._rules_targets():
            exists = entry["path"].is_file()
            state = "missing" if not exists else ("same" if inCloud and self._rules_same(entry) else "different")
            rows.append({"path": str(entry["path"]), "label": entry["label"], "exists": exists, "state": state,
                         "install": entry["install"], "extra": self._extra_path(entry["id"]).is_file()})
        return {"inCloud": inCloud, "text": portable.expand(_rules_text(self.library.rulesPath)), "targets": rows}

    def _save_master(self, text: str) -> None:
        """保存通用部分正本（换成占位符写法）。"""
        text = text.replace("\r\n", "\n")
        kinds = _secret_kinds(text)
        if kinds:
            raise self.UserError(f"规则里疑似有密钥（{', '.join(kinds)}），已拦下，没有保存。")
        self.library.rulesPath.parent.mkdir(parents=True, exist_ok=True)
        common, _extra = split_rules(text)
        self.library.rulesPath.write_text(portable.collapse(common) + "\n", encoding="utf-8", newline="\n")

    def _save_extra(self, agentId: str, text: str) -> bool:
        """保存某个 AI 的专用部分；空的就删掉。

        :returns: 有没有变化
        """
        path = self._extra_path(agentId)
        text = portable.collapse(text.strip("\n"))
        if _secret_kinds(text):
            raise self.UserError(f"{agentId} 的专用规则里疑似有密钥，已拦下，没有保存。")
        if text == _rules_text(path).strip("\n"):
            return False
        if text:
            path.write_text(text + "\n", encoding="utf-8", newline="\n")
        elif path.is_file():
            path.unlink()
        return True

    def rules_save(self, text: str) -> dict:
        """保存规则正本并推送云端（不自动写到各 AI，要再点「应用」）。

        :param text: 规则全文（通用部分）
        :returns: 结果
        """
        with self.lock:
            self._save_master(text)
            return {"message": "已保存全局规则。点「应用到所有 AI」写进各 AI 的规则文件。",
                    "warning": self._commit(self.library.load(), "更新全局规则", push=True)}

    def rules_import(self, path: str) -> dict:
        """把某个 AI 现有的规则文件导入成正本：分界线以上当通用部分，以下当这个 AI 的专用部分。

        :param path: 规则文件路径（必须是已知的规则文件）
        :returns: 结果
        """
        with self.lock:
            known = {str(entry["path"]).lower(): entry for entry in self._rules_targets()}
            entry = known.get(str(path).lower())
            if entry is None or not entry["path"].is_file():
                raise self.UserError(f"不是可导入的规则文件：{path}")
            common, extra = split_rules(_rules_text(entry["path"]))
            self._save_master(common)
            self._save_extra(entry["id"], extra)
            return {"message": f"已从 {entry['path']} 导入。",
                    "warning": self._commit(self.library.load(), "导入全局规则", push=True)}

    def rules_apply(self, force: bool = False) -> dict:
        """把规则写到所有勾选的 AI；原文件先备份到 data/backups。

        :param force: 覆盖内容不同的文件
        :returns: 结果
        """
        with self.lock:
            if not self.library.rulesPath.is_file():
                raise self.UserError("还没有全局规则，先写好保存，或从某个 AI 导入一份")
            targets = [entry for entry in self._rules_targets() if entry["install"]]
            different = [str(entry["path"]) for entry in targets if entry["path"].is_file() and not self._rules_same(entry)]
            if different and not force:
                return {"needConfirm": "这些规则文件和正本不一样，会被覆盖（原文件先备份到 data/backups）：\n"
                                       + "\n".join(different) + "\n\n要覆盖吗？"}
            written = []
            for entry in targets:
                if entry["path"].is_file() and self._rules_same(entry):
                    self._remember(entry["path"], _rules_text(entry["path"]))
                    continue
                self._write_rules(entry)
                written.append(str(entry["path"]))
            if not written:
                return {"message": "所有 AI 的规则已经是最新的"}
            return {"message": "已写入：\n" + "\n".join(written)}

    # -------------------------------------------------- 同步

    def _rules_absorb(self) -> list:
        """把本机直接改过的规则文件收进库（同步按钮第一步）。

        判断"改过"：文件和上次 Skillset 写进去（或确认一致）的不一样。从没和库一致过的文件不收
        （可能是很久以前的旧版本），同步时备份后换成云端版本。
        通用部分有多份改动时取最近改的那份；专用部分各收各的。

        :returns: 说明
        """
        lines = []
        if not self.library.rulesPath.is_file():
            source = next((entry for entry in self._rules_targets() if entry["path"].is_file()), None)
            if source:
                common, extra = split_rules(_rules_text(source["path"]))
                self._save_master(common)
                self._save_extra(source["id"], extra)
                lines.append(f"全局规则：云端还没有，用 {source['label']} 的规则文件建了正本")
            return lines
        written = self._written()
        master = _rules_text(self.library.rulesPath).strip("\n")
        edited = []
        for entry in self._rules_targets():
            path = entry["path"]
            if not path.is_file():
                continue
            if self._rules_same(entry):
                self._remember(path, _rules_text(path))
                continue
            text = _rules_text(path)
            recorded = written.get(str(path).lower())
            if not recorded or recorded == _fingerprint(text):
                continue
            common, extra = split_rules(portable.collapse(text))
            if self._save_extra(entry["id"], extra):
                lines.append(f"全局规则：收进了 {entry['label']} 的专用部分")
            if common.strip() != master.strip():
                edited.append((path.stat().st_mtime, common, entry["label"]))
        if edited:
            _mtime, common, label = max(edited, key=lambda item: item[0])
            self._save_master(common)
            lines.append(f"全局规则：用 {label} 里改过的通用部分更新了正本")
        return lines

    def _sync_rules(self, force: bool = False) -> tuple:
        """按库写规则文件。不强制时只给还没有的 AI 写上，已有但不同的只报告。

        :param force: 不同的也覆盖（原文件先备份到 data/backups）
        :returns: (说明, 跳过的位置)
        """
        if not self.library.rulesPath.is_file():
            return "", []
        written, skipped = 0, []
        for entry in self._rules_targets():
            if not entry["install"]:
                continue
            path = entry["path"]
            if path.is_file() and self._rules_same(entry):
                self._remember(path, _rules_text(path))
                continue
            if not path.is_file() or force:
                self._write_rules(entry)
                written += 1
            else:
                skipped.append(f"规则文件 {path}（和正本不同，去「全局规则」页处理）")
        return (f"规则写入 {written} 处。" if written else ""), skipped
