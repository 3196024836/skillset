/**
 * 「密钥」页：每条密钥有名字、说明、环境变量名和按顺序排的多个 key（第一个没失效的在用）。
 * 明文只在点「显示」「复制」时向后端要一次，不存在页面状态里。
 * 用到 app.js 里的 api / sheet / confirmAlert / promptSheet / toast / esc / I / ui / task / confirmed。
 * 列表跟着 app.js 的 refresh 刷新：keys.dat 变了，指纹就变。
 */

"use strict";

// 显示的明文多久后自动藏回去（毫秒）
const RevealMs = 30000;

/** 读取密钥列表（不含明文），在「密钥」页时重画。 */
async function loadKeys() {
  try {
    ui.keys = await api("/api/keys/list");
  } catch (error) {
    ui.keys = { entries: [], error: error.message };
  }
  if (ui.view === "keys") renderContent();
}

/** 「密钥」页内容。 */
function renderKeys() {
  const data = ui.keys;
  if (!data) return `<div class="page"><div class="empty-row">正在读取…</div></div>`;
  if (data.error) return emptyHtml(I.key, "读不到密钥", data.error);
  const help = `<details class="key-help"><summary>怎么给 AI 和脚本用</summary>
    <p><code>python app/keycli.py run 名字 -- 命令</code> 只把在用的 key 交给这个命令，不写进系统；认证失败会自动标成失效、换下一个 key 重跑。</p>
    <p><code>keycli.py add 名字 --env 变量名 --clipboard</code> 把剪贴板里的 key 加进来（已有这条就加成备用）；<code>keycli.py list</code> 看有哪些。</p>
    <p>密钥用 Windows 加密存在本机 data/keys.dat，只有这台电脑、你这个账户能解开。换电脑解不开，记得「导出备份」存到网盘或 U 盘。</p></details>`;
  if (!data.entries.length) {
    return `<div class="page">${emptyHtml(I.key, "还没有密钥", "点右上角「新建密钥」，或用「导入」把 KeePassXC 导出的 CSV 导进来。")}${help}</div>`;
  }
  return `<div class="page key-page">${data.entries.map(keyCardHtml).join("")}${help}</div>`;
}

/** 一条密钥的卡片：头部是名字、变量名和状态，下面按使用顺序列出 key。 */
function keyCardHtml(entry) {
  const failed = entry.keys.filter((key) => key.status === "failed").length;
  const status = !entry.activeId ? '<span class="chip danger">没有可用的 key</span>'
    : failed ? `<span class="chip warn">${failed} 个失效</span>` : "";
  const rows = entry.keys.map((key, index) => {
    const isFailed = key.status === "failed";
    const chip = isFailed ? '<span class="chip warn">失效</span>'
      : key.id === entry.activeId ? '<span class="chip ok">在用</span>' : '<span class="chip plain">备用</span>';
    const detail = isFailed ? `${key.failedAt} 标为失效${key.reason ? `：${key.reason}` : ""}`
      : key.lastUsedAt ? `上次使用 ${key.lastUsedAt}` : `添加于 ${key.addedAt}`;
    const ids = `data-id="${esc(entry.id)}" data-kid="${esc(key.id)}"`;
    return `<div class="key-row ${isFailed ? "failed" : ""}">
        <span class="key-no">${index + 1}</span>
        <div class="key-main"><div class="key-line"><span class="key-value" data-kid="${esc(key.id)}">${esc(key.masked)}</span>
          ${key.label ? `<span class="key-label">${esc(key.label)}</span>` : ""}</div>
          <div class="key-detail" title="${esc(detail)}">${esc(detail)}</div></div>
        ${chip}
        <div class="key-tools">
          <button class="btn ghost icon sm" data-act="key-reveal" ${ids} title="显示">${I.eye}</button>
          <button class="btn ghost icon sm" data-act="key-copy" ${ids} title="复制">${I.copy}</button>
          ${isFailed ? `<button class="btn sm" data-act="key-restore" ${ids}>恢复</button>`
            : `<button class="btn sm" data-act="key-fail" ${ids} title="这个 key 不好使了，以后用下一个">标为失效</button>`}</div></div>`;
  }).join("");
  return `<section class="card key-card">
      <header class="key-card-head">
        <span class="key-avatar">${esc([...entry.name][0]?.toUpperCase() || "?")}</span>
        <div class="key-title"><div><b>${esc(entry.name)}</b>${entry.env ? `<code>${esc(entry.env)}</code>` : ""}${status}</div>
          ${entry.note ? `<p>${esc(entry.note)}</p>` : ""}</div>
        <button class="btn ghost icon sm" data-act="key-edit" data-id="${esc(entry.id)}" title="编辑">${I.pencil}</button>
        <button class="btn ghost icon sm" data-act="key-delete" data-id="${esc(entry.id)}" title="删除">${I.trash}</button>
      </header>${rows}</section>`;
}

/** 新建或编辑一条密钥的弹窗。已有的 key 不回填明文：留空表示不改。 */
async function editKey(entryId = "") {
  // 打开前取最新的，免得拿旧列表保存时把刚加的 key 覆盖掉
  if (entryId) await loadKeys();
  const entry = ui.keys?.entries.find((item) => item.id === entryId);
  if (entryId && !entry) return toast("这条密钥已经不存在了", { type: "err" });
  const keyRow = (key = {}) => `<div class="key-edit-row" data-kid="${esc(key.id || "")}">
      <input class="field mono" name="value" type="password" autocomplete="off" spellcheck="false"
        placeholder="${key.id ? `${esc(key.masked)}（不改就留空）` : "粘贴 key"}">
      <input class="field" name="label" value="${esc(key.label || "")}" placeholder="备注，比如“公司账号”" spellcheck="false">
      <button type="button" class="btn ghost icon sm" data-peek title="显示 / 隐藏">${I.eye}</button>
      <button type="button" class="btn ghost icon sm" data-move="-1" title="往前挪">↑</button>
      <button type="button" class="btn ghost icon sm" data-move="1" title="往后挪">↓</button>
      <button type="button" class="btn ghost icon sm" data-remove title="删掉这个 key">${I.x}</button></div>`;
  let fields = null;
  const result = await sheet(`<form method="dialog"><div class="sheet-body"><h3>${entry ? "编辑密钥" : "新建密钥"}</h3>
      <div class="form-row"><label>名字</label><input class="field" name="name" value="${esc(entry?.name || "")}" placeholder="比如 OpenAI" autofocus spellcheck="false"></div>
      <div class="form-row"><label>介绍</label><textarea class="field" name="note" placeholder="用在哪个项目、什么权限、什么时候到期">${esc(entry?.note || "")}</textarea></div>
      <div class="form-row"><label>环境变量名（命令行注入时用）</label><input class="field mono" name="env" value="${esc(entry?.env || "")}" placeholder="比如 OPENAI_API_KEY" spellcheck="false"></div>
      <div class="form-row"><label>key（从上到下依次使用，前面的失效了就用后面的）</label>
        <div class="key-edit-list">${(entry?.keys || [{}]).map(keyRow).join("")}</div>
        <button type="button" class="btn sm" data-add style="margin-top:8px">${I.plus}添加备用 key</button></div></div>
      <div class="sheet-foot"><button type="button" class="btn" data-close="cancel">取消</button>
        <button type="submit" class="btn primary">保存</button></div></form>`, "wide", (dialog, close) => {
    const list = dialog.querySelector(".key-edit-list");
    dialog.querySelector("[data-add]").addEventListener("click", () => {
      list.insertAdjacentHTML("beforeend", keyRow());
      list.lastElementChild.querySelector("input").focus();
    });
    list.addEventListener("click", async (event) => {
      const row = event.target.closest(".key-edit-row");
      if (!row) return;
      const peek = event.target.closest("[data-peek]");
      if (peek) {
        // 已有的 key 没回填明文，第一次看时向后端要；填回去的值和原来一样，保存时算没改
        const input = row.querySelector('[name="value"]');
        if (input.type === "password" && !input.value && row.dataset.kid) {
          try {
            input.value = await keyValue({ dataset: { id: entryId, kid: row.dataset.kid } });
          } catch (error) {
            return toast(error.message, { type: "err" });
          }
        }
        input.type = input.type === "password" ? "text" : "password";
        peek.classList.toggle("on", input.type === "text");
        return;
      }
      if (event.target.closest("[data-remove]")) row.remove();
      const move = event.target.closest("[data-move]")?.dataset.move;
      if (move === "-1" && row.previousElementSibling) row.previousElementSibling.before(row);
      if (move === "1" && row.nextElementSibling) row.nextElementSibling.after(row);
    });
    const form = dialog.querySelector("form");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const data = new FormData(form);
      fields = {
        id: entryId, name: data.get("name"), note: data.get("note"), env: data.get("env"),
        keys: [...list.children].map((row) => ({ id: row.dataset.kid, value: row.querySelector('[name="value"]').value,
          label: row.querySelector('[name="label"]').value })),
      };
      close("ok");
    });
  });
  if (result !== "ok") return;
  await task("保存密钥", () => api("/api/keys/save", { fields }));
}

/** 取一个 key 的明文。 */
async function keyValue(actor) {
  const { value } = await api("/api/keys/reveal", { id: actor.dataset.id, keyId: actor.dataset.kid });
  return value;
}

/** 选一个文件，读成文本。 */
function pickText(accept) {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.addEventListener("change", () => (input.files[0] ? input.files[0].text().then(resolve) : resolve(null)));
    input.click();
  });
}

// 「密钥」页的按钮（app.js 的点击处理会先找这里）
const KeyActions = {
  "key-new": () => editKey(),
  "key-edit": (actor) => editKey(actor.dataset.id),
  "key-delete": (actor) => task("删除密钥", async () => {
    const first = await api("/api/keys/delete", { id: actor.dataset.id });
    return confirmed("/api/keys/delete", { id: actor.dataset.id }, first, { head: "删除这条密钥？", action: "删除" });
  }),
  "key-reveal": async (actor) => {
    const span = document.querySelector(`.key-value[data-kid="${CSS.escape(actor.dataset.kid)}"]`);
    if (!span) return;
    if (span.dataset.masked) {
      span.textContent = span.dataset.masked;
      delete span.dataset.masked;
      actor.title = "显示";
      actor.classList.remove("on");
      return;
    }
    try {
      const value = await keyValue(actor);
      span.dataset.masked = span.textContent;
      span.textContent = value;
      actor.title = "隐藏";
      actor.classList.add("on");
      setTimeout(() => span.dataset.masked && actor.click(), RevealMs);
    } catch (error) {
      toast(error.message, { type: "err" });
    }
  },
  "key-copy": async (actor) => {
    try {
      await navigator.clipboard.writeText(await keyValue(actor));
      toast("已复制。用完记得别贴到聊天或代码里");
    } catch (error) {
      toast(`复制失败：${error.message}`, { type: "err" });
    }
  },
  "key-fail": async (actor) => {
    const values = await promptSheet({ head: "把这个 key 标成失效？", text: "以后会用它后面的 key。之后可以随时恢复。",
      fields: [{ name: "reason", label: "原因（可不填）", placeholder: "比如：401、余额不足、已作废" }], action: "标为失效" });
    if (values) await task("标记失效", () => api("/api/keys/mark", { id: actor.dataset.id, keyId: actor.dataset.kid, failed: true, reason: values.reason }));
  },
  "key-restore": (actor) => task("恢复 key", () => api("/api/keys/mark", { id: actor.dataset.id, keyId: actor.dataset.kid, failed: false })),
  "key-export": async () => {
    const values = await promptSheet({ head: "导出密钥备份", text: "用你设的密码加密，存到下载文件夹。换电脑时用它和这个密码导回来。密码忘了就打不开。",
      fields: [{ name: "password", label: "备份密码（至少 8 位）", type: "password" }, { name: "again", label: "再输一次", type: "password" }], action: "导出" });
    if (!values) return;
    if (values.password !== values.again) return toast("两次密码不一样", { type: "err" });
    await task("导出备份", () => api("/api/keys/export", { password: values.password }));
  },
  "key-import-backup": async () => {
    const text = await pickText(".json");
    if (text === null) return;
    const values = await promptSheet({ head: "导入密钥备份", fields: [{ name: "password", label: "备份密码", type: "password" }], action: "导入" });
    if (values) await task("导入备份", () => api("/api/keys/import-backup", { text, password: values.password }));
  },
  "key-import-csv": async () => {
    const choice = await confirmAlert({ head: "从 KeePassXC 导入", danger: false, action: "选择 CSV 文件",
      message: "先在 KeePassXC 里点「数据库 → 导出 → CSV 文件」，再选那个文件。标题当名字、备注当介绍、密码当 key；名字已存在的会跳过。" });
    if (choice !== "ok") return;
    const text = await pickText(".csv");
    if (text !== null) await task("导入 CSV", () => api("/api/keys/import-csv", { text }));
  },
};
