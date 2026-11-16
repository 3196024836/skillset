"""
冒烟测试：在隔离的假主目录里跑一遍主要流程，不碰真实的 AI 目录和 Claude 配置。

用法：python -I tests/smoke_test.py [--plugins]
    --plugins  同时测试插件（需要本机有 claude 命令，使用临时 CLAUDE_CONFIG_DIR）
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 测试工作目录（放在下载目录，跑完可删）
WorkDir = Path.home() / "Downloads" / "skillmgr-test" / "run"
# 工具代码目录
AppDir = Path(__file__).resolve().parent.parent / "app"


def write_skill(root: Path, name: str, text: str = "body") -> None:
    """造一个最小 skill。

    :param root: skills 目录
    :param name: skill 名
    :param text: 正文
    """
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: >\n  test {name}\n  second line\n---\n{text}\n",
                                          encoding="utf-8")


def check(condition: bool, label: str) -> None:
    """断言并打印结果。

    :param condition: 条件
    :param label: 说明
    """
    print(("通过 " if condition else "失败 ") + label, flush=True)
    if not condition:
        sys.exit(1)


def row(service, skillId: str) -> dict:
    """取某个 skill 的状态行。

    :param service: SkillService
    :param skillId: skill 名
    :returns: 状态行，没有返回 None
    """
    return next((item for item in service.state()["skills"] if item["id"] == skillId), None)


def main() -> None:
    """跑测试。"""
    testPlugins = "--plugins" in sys.argv

    # 准备隔离环境：假主目录、假远程仓库
    if WorkDir.exists():
        shutil.rmtree(WorkDir, onexc=lambda f, p, e: (os.chmod(p, 0o777), f(p)))
    home = WorkDir / "home"
    remote = WorkDir / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    write_skill(home / ".claude/skills", "alpha")
    write_skill(home / ".claude/skills", "beta")
    write_skill(home / ".codex/skills", "alpha")
    (home / ".codex/skills/.system").mkdir(parents=True)
    (home / ".gemini").mkdir()
    os.environ["SKILLMGR_HOME"] = str(home)
    os.environ["SKILLMGR_DATA"] = str(WorkDir / "data")
    os.environ["CLAUDE_CONFIG_DIR"] = str(WorkDir / "claude-cfg")
    sys.path.insert(0, str(AppDir))
    from service import SkillService, UserError

    service = SkillService()
    service.set_settings(str(remote), True)
    if not testPlugins:
        # 不测插件时让插件读取直接失败，避免调用真实 claude
        import plugins
        plugins.claude_exe = lambda: (_ for _ in ()).throw(plugins.PluginError("测试中跳过插件"))

    # 扫描：本机 skill 都识别为未上传，隐藏目录被忽略
    state = service.state()
    ids = [item["id"] for item in state["skills"]]
    check(ids == ["alpha", "beta"], f"扫描到本机 skill {ids}")
    check(not row(service, "alpha")["inCloud"], "alpha 显示未上传")
    check(row(service, "alpha")["description"] == "test alpha second line", "多行 description 解析")
    active = [agent["id"] for agent in state["agents"] if agent["active"]]
    check(set(active) == {"claude", "codex", "gemini"}, f"默认只勾本机已装的 AI {active}")

    # 关闭未上传的：必须询问
    check("needConfirm" in service.disable("beta"), "关闭未上传的 skill 会先询问")

    # 上传全部 -> 推送到假远程
    service.upload_all()
    check(row(service, "alpha")["inCloud"] and row(service, "beta")["inCloud"], "全部上传后都在云端")
    log = subprocess.run(["git", "--git-dir", str(remote), "log", "--oneline"], capture_output=True, text=True, encoding="utf-8").stdout
    check("上传 alpha" in log, "已推送到云端仓库")

    # 一键同步：装到通用目录（Codex/Gemini）和 Claude
    service.sync_all()
    check((home / ".agents/skills/alpha/SKILL.md").is_file(), "同步后通用目录里有 alpha")
    check(row(service, "alpha")["duplicate"], "检测到 ~/.codex/skills 旧目录重复")
    service.clean_duplicates()
    check(not (home / ".codex/skills/alpha").exists(), "清理重复删掉了旧目录里相同的副本")

    # 密钥拦截
    write_skill(home / ".claude/skills", "leaky", "key = sk-" + "a" * 30)
    try:
        service.upload("leaky")
        check(False, "含密钥的 skill 应被拦下")
    except UserError as error:
        check("密钥" in str(error) and "aaaa" not in str(error), "含密钥被拦下，且不回显密钥")
    service.disable("leaky", True)

    # 本机改动：关闭时询问；上传后其他副本同步
    (home / ".claude/skills/alpha/SKILL.md").write_text("---\nname: alpha\ndescription: changed\n---\nv2\n", encoding="utf-8")
    check(row(service, "alpha")["modified"], "本机改动被识别")
    check("needConfirm" in service.disable("alpha"), "有改动时关闭会先询问")
    service.upload("alpha")
    check(not row(service, "alpha")["modified"], "上传后所有副本一致")

    # 关闭 -> 本机删除，云端保留；启用 -> 装回
    service.disable("beta")
    check(not service._copies("beta"), "关闭后本机所有副本被删除")
    check(row(service, "beta")["inCloud"] and not row(service, "beta")["enabled"], "关闭后云端保留")
    service.enable("beta")
    check(len(service._copies("beta")) == 2, "启用后装回 Claude 和通用目录")

    # 分组
    service.group_add("生图")
    groupId = service.state()["groups"][0]["id"]
    service.edit("skill", "alpha", {"nameZh": "阿尔法", "note": "测试备注", "group": groupId})
    service.edit("skill", "beta", {"group": groupId})
    service.group_toggle(groupId, False)
    check(not row(service, "alpha")["enabled"] and not row(service, "beta")["enabled"], "整组关闭")
    service.group_toggle(groupId, True)
    check(row(service, "alpha")["enabled"] and row(service, "alpha")["nameZh"] == "阿尔法", "整组启用，中文名保留")
    service.push()

    # 换新电脑：新主目录 + 同一个远程，一键更新后全部装好
    newHome = WorkDir / "newhome"
    (newHome / ".claude").mkdir(parents=True)
    os.environ["SKILLMGR_HOME"] = str(newHome)
    os.environ["SKILLMGR_DATA"] = str(WorkDir / "data2")
    fresh = SkillService()
    fresh.set_settings(str(remote), True)
    fresh.update()
    check((newHome / ".claude/skills/alpha/SKILL.md").read_text(encoding="utf-8").endswith("v2\n"), "新电脑一键更新装回最新版")
    check(row(fresh, "alpha")["nameZh"] == "阿尔法", "新电脑上中文名和分组都在")

    # 彻底删除
    check("needConfirm" in fresh.delete("beta"), "彻底删除会先询问")
    fresh.delete("beta", True)
    check(row(fresh, "beta") is None, "彻底删除后云端和本机都没了")

    # 首次引导：第三台电脑用「使用已有仓库」连接，自动下载云端内容
    os.environ["SKILLMGR_HOME"] = str(WorkDir / "home3")
    os.environ["SKILLMGR_DATA"] = str(WorkDir / "data3")
    (WorkDir / "home3" / ".claude").mkdir(parents=True)
    third = SkillService()
    check(not third.state()["config"]["onboarded"], "新电脑未连接时显示引导")
    third.connect(str(remote))
    check(row(third, "alpha") and row(third, "alpha")["inCloud"] and third.state()["config"]["onboarded"], "连接已有仓库后下载云端内容")

    # 首次引导：本机已有 skill，连接一个空仓库会把本机内容推上去
    emptyRemote = WorkDir / "empty.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(emptyRemote)], check=True, capture_output=True)
    os.environ["SKILLMGR_HOME"] = str(WorkDir / "home4")
    os.environ["SKILLMGR_DATA"] = str(WorkDir / "data4")
    write_skill(WorkDir / "home4" / ".claude" / "skills", "gamma")
    fourth = SkillService()
    fourth.upload("gamma")
    fourth.connect(str(emptyRemote))
    log = subprocess.run(["git", "--git-dir", str(emptyRemote), "log", "--oneline"], capture_output=True, text=True, encoding="utf-8").stdout
    check("gamma" in log, "连接空仓库后本机内容已推送")

    if testPlugins:
        test_plugins(fresh)
    print("全部通过")


def test_plugins(service) -> None:
    """用本地临时市场测试插件流程。

    :param service: SkillService
    """
    market = WorkDir / "market"
    pluginDir = market / "plugins" / "demo-img"
    (market / ".claude-plugin").mkdir(parents=True)
    (pluginDir / ".claude-plugin").mkdir(parents=True)
    (market / ".claude-plugin/marketplace.json").write_text(json.dumps(
        {"name": "test-market", "owner": {"name": "t"},
         "plugins": [{"name": "demo-img", "source": "./plugins/demo-img", "description": "测试"}]}), encoding="utf-8")
    (pluginDir / ".claude-plugin/plugin.json").write_text('{"name":"demo-img","version":"0.1.0"}', encoding="utf-8")
    write_skill(pluginDir / "skills", "draw-a")

    service.plugin_add_market(str(market))
    service.plugin_enable("demo-img@test-market")
    item = next(p for p in service.state()["plugins"]["items"] if p["id"] == "demo-img@test-market")
    check(item["installed"] and item["skills"][0]["id"] == "draw-a", "插件安装并展开出里面的 skill")
    check("needConfirm" in service.plugin_disable("demo-img@test-market"), "未上传插件关闭前询问")
    service.plugin_upload("demo-img@test-market")
    service.plugin_disable("demo-img@test-market")
    item = next(p for p in service.state()["plugins"]["items"] if p["id"] == "demo-img@test-market")
    check(not item["installed"] and item["inCloud"], "插件关闭=卸载，云端记录保留")

    # 模拟新电脑：清单里是启用，但本机没装，也没添加市场
    manifest = service.library.load()
    manifest["plugins"]["demo-img@test-market"]["enabled"] = True
    service.library.save(manifest)
    subprocess.run(["claude", "plugin", "marketplace", "remove", "test-market"], capture_output=True, shell=True)
    service.sync_all()
    item = next(p for p in service.state()["plugins"]["items"] if p["id"] == "demo-img@test-market")
    check(item["installed"], "一键同步按清单把插件装回")
    service.plugin_delete("demo-img@test-market", True)
    check(not service.state()["plugins"]["items"], "插件彻底删除")


if __name__ == "__main__":
    main()
