"""
密钥管理的界面接口（混入 SkillService）。具体读写在 keystore.py。
"""

import time

import config as cfg
from keystore import KeyStoreError


class KeyMixin:
    """「密钥」页的操作。需要宿主类提供 self.keys（KeyStore）。"""

    def keys_list(self) -> dict:
        """全部密钥，key 只露末 4 位。

        :returns: {"entries", "error"}
        """
        try:
            return {"entries": self.keys.list_masked(), "error": ""}
        except (KeyStoreError, ValueError) as error:
            return {"entries": [], "error": str(error)}

    def keys_save(self, fields: dict) -> dict:
        """新建或修改一条密钥。"""
        entry = self.keys.save_entry(fields)
        if entry.get("appended"):
            return {"message": f"已经有 {entry['name']} 了，新 key 加在它后面当备用（现在共 {len(entry['keys'])} 个）",
                    "id": entry["id"]}
        return {"message": f"已保存密钥 {entry['name']}", "id": entry["id"]}

    def keys_reveal(self, entryId: str, keyId: str) -> dict:
        """取一个 key 的明文，界面显示或复制用。"""
        return {"value": self.keys.reveal(entryId, keyId)}

    def keys_mark(self, entryId: str, keyId: str, failed: bool, reason: str) -> dict:
        """标成失效或恢复。"""
        entry = self.keys.mark(entryId, keyId, failed, reason)
        return {"message": f"已把 {entry['name']} 的这个 key {'标成失效' if failed else '恢复可用'}"}

    def keys_delete(self, entryId: str, force: bool) -> dict:
        """删除整条密钥（先确认）。"""
        if not force:
            return {"needConfirm": "删除这条密钥和它的所有备用 key，无法恢复（除非有备份）。\n确定吗？"}
        return {"message": f"已删除密钥 {self.keys.delete(entryId)}"}

    def keys_export(self, password: str) -> dict:
        """用密码加密导出到下载文件夹。"""
        target = cfg.home_dir() / "Downloads" / f"skillset-keys-{time.strftime('%Y%m%d-%H%M%S')}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        count = self.keys.export_backup(password, target)
        return {"message": f"已导出 {count} 条密钥到 {target}\n换电脑时用「导入备份」和这个密码恢复。请把它存到网盘或 U 盘。",
                "path": str(target)}

    def keys_import_backup(self, text: str, password: str) -> dict:
        """导入密码备份。"""
        return self._import_result(*self.keys.import_backup(text, password), "")

    def keys_import_csv(self, text: str) -> dict:
        """导入 KeePassXC 导出的 CSV。"""
        return self._import_result(*self.keys.import_csv(text),
                                   "\n导入完了请删掉那个 CSV 文件：它是明文，谁拿到都能看到你的 key。")

    @staticmethod
    def _import_result(added: int, skipped: list, tail: str) -> dict:
        """导入结果的提示。"""
        message = f"导入了 {added} 条密钥"
        if skipped:
            message += f"；名字已存在、跳过 {len(skipped)} 条：{'、'.join(skipped[:10])}" + ("等" if len(skipped) > 10 else "")
        return {"message": message + tail}

