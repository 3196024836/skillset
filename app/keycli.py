"""
密钥命令行：不开界面也能用，给 AI 和脚本调用。永远不打印 key 的明文。

用法：
    python app/keycli.py list
    python app/keycli.py run OpenAI -- python script.py          # 用条目里设的环境变量名
    python app/keycli.py run OpenAI=MY_KEY Other -- some-cmd     # 指定环境变量名，可以一次给多个
    python app/keycli.py fail OpenAI --reason "401"              # 把当前在用的 key 标成失效
    python app/keycli.py add OpenAI --env OPENAI_API_KEY --clipboard   # 从剪贴板添加；已有这条就加成备用
    某命令 | python app/keycli.py add OpenAI                      # 从管道添加

run 只把 key 交给这一个子进程，不写进系统环境变量；子进程输出里出现的 key 会被替换成 ***。
只给一条密钥时，如果命令失败且输出像是认证失败（401、invalid api key、余额不足等），
会把这个 key 标成失效、换下一个 key 重跑；加 --no-retry 关掉。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from keystore import KeyStore, KeyStoreError  # noqa: E402

# 认证失败的样子（限流 429 不算：换 key 不一定有用）
AuthFailure = re.compile(
    r"\b40[13]\b|unauthori[sz]ed|invalid[ _-]?api[ _-]?key|incorrect[ _-]api[ _-]key|authentication[ _-]error"
    r"|invalid[ _-]token|insufficient[ _-]quota|余额不足|密钥无效|key\s*无效", re.IGNORECASE)
# 保留最后多少行输出用来判断失败原因
TailLines = 200


def _say(text: str) -> None:
    """提示写到标准错误，不混进子进程的正常输出。"""
    print(f"[keys] {text}", file=sys.stderr, flush=True)


def _pump(source, target, secrets: list, tail: list) -> None:
    """把子进程输出逐行转给自己的输出，并隐藏 key。

    :param source: 子进程管道
    :param target: 自己的输出（二进制）
    :param secrets: 要隐藏的 key（字节）
    :param tail: 收集最后若干行（已隐藏）
    """
    for line in iter(source.readline, b""):
        for secret in secrets:
            line = line.replace(secret, b"***")
        target.write(line)
        target.flush()
        tail.append(line)
        del tail[:-TailLines]
    source.close()


def run_once(command: list, env: dict, secrets: list) -> tuple:
    """跑一次命令。

    :returns: (退出码, 最后若干行输出的文本)
    """
    # Windows 上 npm、npx 这类是 .cmd，要先找到完整路径才能启动
    command = [shutil.which(command[0]) or command[0], *command[1:]]
    try:
        child = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as error:
        _say(f"启动命令失败：{error}")
        return 127, ""
    tail = []
    threads = [threading.Thread(target=_pump, args=(pipe, out, secrets, tail), daemon=True)
               for pipe, out in ((child.stdout, sys.stdout.buffer), (child.stderr, sys.stderr.buffer))]
    for thread in threads:
        thread.start()
    code = child.wait()
    for thread in threads:
        thread.join()
    return code, b"".join(tail).decode("utf-8", "replace")


def cmd_list(store: KeyStore) -> int:
    """列出所有密钥（不含明文）。"""
    entries = store.list_masked()
    if not entries:
        print("还没有密钥。在 Skillset 界面的「密钥」页添加。")
    for entry in entries:
        ok = sum(1 for key in entry["keys"] if key["status"] == "ok")
        failed = len(entry["keys"]) - ok
        env = entry["env"] or "（没设环境变量名）"
        print(f"{entry['name']}  {env}  可用 {ok} 个" + (f"，失效 {failed} 个" if failed else "")
              + (f"  —— {entry['note'].splitlines()[0]}" if entry["note"] else ""))
    return 0


def cmd_run(store: KeyStore, specs: list, command: list, retry: bool) -> int:
    """注入 key 运行命令；只给一条密钥时认证失败自动换下一个 key 重跑。"""
    if not command:
        _say("缺少要运行的命令，写在 -- 后面")
        return 2
    while True:
        env, secrets, used = dict(os.environ), [], []
        for spec in specs:
            name, _, envName = spec.partition("=")
            entry, key, index = store.active(name)
            envName = envName or entry["env"]
            if not envName:
                _say(f"{entry['name']} 没设环境变量名，请写成 {entry['name']}=变量名")
                return 2
            if key is None:
                _say(f"{entry['name']} 的 {len(entry['keys'])} 个 key 都标成失效了，请在 Skillset 里添加或恢复")
                return 3
            env[envName] = key["value"]
            secrets += [item["value"].encode("utf-8") for item in entry["keys"]]
            used.append((entry, key, index))
            _say(f"{entry['name']} → {envName}，用第 {index} 个 key（共 {len(entry['keys'])} 个）")
        for entry, key, _index in used:
            store.touch(entry["id"], key["id"])
        code, tail = run_once(command, env, sorted(set(secrets), key=len, reverse=True))
        if code == 0 or not retry or len(used) != 1:
            return code
        match = next((line for line in reversed(tail.splitlines()) if AuthFailure.search(line)), "")
        if not match:
            return code
        entry, key, index = used[0]
        store.mark(entry["id"], key["id"], True, match.strip()[:200])
        _say(f"第 {index} 个 key 像是认证失败，已标成失效：{match.strip()[:120]}")
        if store.active(entry["id"])[1] is None:
            _say("没有可用的备用 key 了")
            return code
        _say("换下一个 key 重跑")


def read_value(fromClipboard: bool) -> str:
    """取要添加的 key：剪贴板、管道，或者在终端里隐藏输入。不从命令行参数取，免得留在命令记录里。"""
    if fromClipboard:
        if sys.platform != "win32":
            raise KeyStoreError("--clipboard 只支持 Windows，请改用管道：... | keycli.py add 名字")
        done = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        return done.stdout.strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    import getpass
    return getpass.getpass("粘贴 key（不会显示）：").strip()


def cmd_add(store: KeyStore, name: str, env: str, note: str, label: str, fromClipboard: bool) -> int:
    """添加 key：没有这条密钥就新建，有就加在最后当备用。"""
    value = read_value(fromClipboard)
    if not value:
        _say("没读到 key：剪贴板或输入是空的")
        return 2
    try:
        entry = store._find(store.load(), name)
    except KeyStoreError:
        entry = None
    keys = [{"id": key["id"], "label": key["label"]} for key in entry["keys"]] if entry else []
    saved = store.save_entry({
        "id": entry["id"] if entry else "", "name": entry["name"] if entry else name,
        "note": note or (entry["note"] if entry else ""), "env": env or (entry["env"] if entry else ""),
        "keys": keys + [{"value": value, "label": label}]})
    count = len(saved["keys"])
    _say(f"已把 key（末 4 位 {value[-4:]}）加到 {saved['name']}，" + ("新建了这条密钥" if count == 1 else f"排第 {count} 个，当备用")
         + ("" if saved["env"] else "。还没设环境变量名，用 --env 补上"))
    return 0


def cmd_fail(store: KeyStore, name: str, reason: str) -> int:
    """把当前在用的 key 标成失效。"""
    entry, key, index = store.active(name)
    if key is None:
        _say(f"{entry['name']} 没有可用的 key 了")
        return 3
    store.mark(entry["id"], key["id"], True, reason or "命令行标记")
    nextKey = store.active(entry["id"])[2]
    _say(f"已把 {entry['name']} 的第 {index} 个 key 标成失效" + (f"，之后用第 {nextKey} 个" if nextKey else "，已没有可用的 key"))
    return 0


def main() -> int:
    """解析参数并执行。"""
    # 被 AI 或脚本通过管道调用时，提示一律用 UTF-8，免得中文变乱码
    for stream in (sys.stdout, sys.stderr):
        if not stream.isatty():
            stream.reconfigure(encoding="utf-8")
    argv = sys.argv[1:]
    command = []
    if "--" in argv:
        command, argv = argv[argv.index("--") + 1:], argv[:argv.index("--")]
    parser = argparse.ArgumentParser(prog="keycli", description="Skillset 密钥命令行（不打印明文）")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="列出密钥")
    runParser = sub.add_parser("run", help="注入 key 运行命令：run 名字[=变量名] ... -- 命令")
    runParser.add_argument("names", nargs="+")
    runParser.add_argument("--no-retry", action="store_true", help="认证失败时不自动换 key 重跑")
    addParser = sub.add_parser("add", help="添加 key（从剪贴板或管道读，没有这条就新建，有就加成备用）")
    addParser.add_argument("name")
    addParser.add_argument("--env", default="", help="环境变量名，比如 OPENAI_API_KEY")
    addParser.add_argument("--note", default="", help="介绍：用在哪、权限、到期时间")
    addParser.add_argument("--label", default="", help="这个 key 的备注，比如“公司账号”")
    addParser.add_argument("--clipboard", action="store_true", help="从剪贴板读 key")
    failParser = sub.add_parser("fail", help="把当前在用的 key 标成失效")
    failParser.add_argument("name")
    failParser.add_argument("--reason", default="")
    args = parser.parse_args(argv)
    store = KeyStore()
    try:
        if args.action == "list":
            return cmd_list(store)
        if args.action == "run":
            return cmd_run(store, args.names, command, not args.no_retry)
        if args.action == "add":
            return cmd_add(store, args.name, args.env, args.note, args.label, args.clipboard)
        return cmd_fail(store, args.name, args.reason)
    except KeyStoreError as error:
        _say(str(error))
        return 2


if __name__ == "__main__":
    sys.exit(main())
