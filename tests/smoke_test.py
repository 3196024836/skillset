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
    fake_claude_mcp()

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

    # 本机改动：指纹变化（界面靠它自动发现）；关闭时询问；上传后其他副本同步
    stamp = service.stamp()
    check(service.stamp() == stamp and service.state()["stamp"] == stamp, "没有改动时指纹不变")
    (home / ".claude/skills/alpha/SKILL.md").write_text("---\nname: alpha\ndescription: changed\n---\nv2\n", encoding="utf-8")
    check(service.stamp() != stamp, "外面改了 skill，指纹跟着变")
    stamp = service.stamp()
    write_skill(home / ".agents/skills", "gamma")
    check(service.stamp() != stamp, "外面新建了 skill，指纹跟着变")
    shutil.rmtree(home / ".agents/skills/gamma")
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
    test_mcp_rules(service, home, UserError)
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
    check((newHome / ".claude/CLAUDE.md").read_text(encoding="utf-8") == "# rules A\n", "新电脑一键更新写上全局规则")

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


def fake_claude_mcp() -> None:
    """把 `claude mcp add-json/remove` 换成直接改测试目录里的 .claude.json，其他 claude 命令照旧。"""
    import mcp
    import plugins
    original = plugins.run_claude

    def run(*args):
        if args[0] != "mcp":
            return original(*args)
        path = mcp.claude_config_path()
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        servers = data.setdefault("mcpServers", {})
        if args[1] == "add-json":
            servers[args[2]] = json.loads(args[3])
        elif args[1] == "remove":
            servers.pop(args[2], None)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return ""

    plugins.run_claude = run


def mcp_row(service, mcpId: str) -> dict:
    """取某个 MCP 的状态行。

    :param service: SkillService
    :param mcpId: id
    :returns: 行，找不到返回 None
    """
    return next((item for item in service.state()["mcps"]["items"] if item["id"] == mcpId), None)


def test_mcp_rules(service, home: Path, UserError) -> None:
    """MCP 工具和全局规则：扫描、上传（密钥不上云）、同步、关闭、启用、规则导入和应用。

    :param service: SkillService
    :param home: 测试主目录
    :param UserError: 可预期错误类型
    """
    import tomllib
    secret = "secret-value-" + "9" * 8
    codexToml = home / ".codex/config.toml"
    codexToml.write_text('# 我的注释\nmodel = "gpt"\n\n[mcp_servers.demo]\ncommand = "demo.exe"\nargs = ["serve"]\n'
                         f'startup_timeout_sec = 30\n\n[mcp_servers.demo.env]\nAPI_KEY = "{secret}"\n\n'
                         '[mcp_servers.node_repl]\ncommand = "node"\n', encoding="utf-8")
    claudeJson = WorkDir / "claude-cfg/.claude.json"
    claudeJson.parent.mkdir(parents=True, exist_ok=True)
    claudeJson.write_text(json.dumps({"otherKey": 1, "mcpServers": {"Demo": {
        "type": "stdio", "command": "demo.exe", "args": ["serve"], "env": {"API_KEY": secret}}}}), encoding="utf-8")
    geminiJson = home / ".gemini/settings.json"
    geminiJson.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")

    item = mcp_row(service, "demo")
    check(item and not item["inCloud"] and len(item["copies"]) == 2, "扫描到 Claude 和 Codex 里的 MCP（大小写不同算同一个）")
    check(mcp_row(service, "node_repl") is None, "Codex 内部的 node_repl 不管理")
    check(item["missing"] == ["Gemini CLI"], f"MCP 缺 Gemini CLI {item['missing']}")
    check(secret not in json.dumps(item), "界面状态里不出现密钥值")
    check("needConfirm" in service.mcp_disable("demo"), "关闭未上传的 MCP 会先询问")

    service.mcp_upload("demo")
    manifestText = service.library.manifestPath.read_text(encoding="utf-8")
    check("demo.exe" in manifestText and secret not in manifestText, "MCP 上传到清单，密钥值没上传")
    check(mcp_row(service, "demo")["secretEnv"] == ["API_KEY"], "记下密钥名字")
    check(all(copy["state"] == "same" for copy in mcp_row(service, "demo")["copies"]), "上传后各 AI 配置一致")

    service.sync_all()
    gemini = json.loads(geminiJson.read_text(encoding="utf-8"))
    check(gemini["theme"] == "dark" and gemini["mcpServers"]["Demo"]["env"]["API_KEY"] == secret,
          "同步装到 Gemini，密钥值从本机补上，其他设置保留")

    codexToml.write_text(codexToml.read_text(encoding="utf-8").replace('["serve"]', '["serve", "-v"]'), encoding="utf-8")
    check(mcp_row(service, "demo")["modified"], "Codex 里的 MCP 改动被识别")
    check("needConfirm" in service.mcp_enable("demo"), "启用时覆盖本机改动会先询问")

    service.mcp_disable("demo", True)
    parsed = tomllib.loads(codexToml.read_text(encoding="utf-8"))
    check("demo" not in parsed["mcp_servers"] and "node_repl" in parsed["mcp_servers"] and parsed["model"] == "gpt",
          "关闭后从 Codex 删掉（连子表），其他配置保留")
    check("# 我的注释" in codexToml.read_text(encoding="utf-8"), "Codex 配置里的注释保留")
    check("Demo" not in json.loads(claudeJson.read_text(encoding="utf-8"))["mcpServers"], "关闭后从 Claude 删掉")
    closed = mcp_row(service, "demo")
    check(closed["inCloud"] and not closed["enabled"] and not closed["copies"], "关闭后云端保留")

    result = service.mcp_enable("demo")
    check("API_KEY" in result["message"], "本机没有密钥值时提示要手动填")
    parsed = tomllib.loads(codexToml.read_text(encoding="utf-8"))
    check(parsed["mcp_servers"]["Demo"]["args"] == ["serve"], "启用后写回 Codex")
    check(len(mcp_row(service, "demo")["copies"]) == 3, "启用后三个 AI 都有")
    check(list((WorkDir / "data/backups").glob("*-codex-config.toml")), "改 Codex 配置前做了备份")

    check("needConfirm" in service.mcp_delete("demo"), "彻底删除 MCP 会先询问")
    service.mcp_delete("demo", True)
    check(mcp_row(service, "demo") is None and "demo" not in service.library.load()["mcps"], "MCP 彻底删除")

    # 全局规则
    (home / ".claude/CLAUDE.md").write_text("# rules A\n", encoding="utf-8")
    (home / ".codex/AGENTS.md").write_bytes(b"# rules B\r\n")
    rules = service.state()["rules"]
    labels = {target["label"]: target["state"] for target in rules["targets"]}
    check(not rules["inCloud"] and labels.get("Claude Code") == "different" and "missing" in labels.values(),
          f"列出各 AI 的规则文件 {labels}")
    service.rules_import(str(home / ".claude/CLAUDE.md"))
    check(service.state()["rules"]["text"] == "# rules A\n", "从 Claude 导入规则正本")
    try:
        service.rules_save("token sk-" + "b" * 30)
        check(False, "含密钥的规则应被拦下")
    except UserError:
        check(service.state()["rules"]["text"] == "# rules A\n", "含密钥的规则被拦下，正本不变")
    service.sync_all()
    check((home / ".gemini/GEMINI.md").read_text(encoding="utf-8") == "# rules A\n", "同步给没有规则的 AI 写上")
    check((home / ".codex/AGENTS.md").read_bytes() == b"# rules B\r\n", "同步不覆盖已有的不同规则")
    check("needConfirm" in service.rules_apply(), "应用规则覆盖不同的文件前先询问")
    service.rules_apply(True)
    check((home / ".codex/AGENTS.md").read_bytes() == b"# rules A\n", "应用后 Codex 规则和正本一致")
    check(list((WorkDir / "data/backups").glob("*-rules-AGENTS.md")), "覆盖规则前做了备份")


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
