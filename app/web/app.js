/**
 * Skillset 前端：拉取 /api/state 渲染界面（空间切换栏 | 侧栏 | 主区 | 详情），技能和密钥是两个互不相干的空间，操作调用 /api/*。
 * 会丢数据的操作由后端返回 needConfirm，这里弹出警告框，用户确认后带 force 重发。
 */

"use strict";

// ============================================================ 常量

// 项目主页
const RepoUrl = "https://github.com/3196024836/skillset";
// 版本号
const Version = "1.0.0";
// 分组颜色轮换表
const GroupColors = ["#0088FF", "#AF52DE", "#FF8D28", "#34C759", "#FF2D55", "#00C3D0", "#6155F5", "#E5A000", "#00C8B3", "#FF383C"];
// 是否减少动效
const ReducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

// 筛选：名称和判断函数
const Filters = {
  all: ["全部", () => true],
  on: ["已启用", (item) => item.enabled],
  off: ["已关闭", (item) => !item.enabled],
  attention: ["需处理", (item) => needsAttention(item)],
};

/** 生成线条图标。 */
const svg = (paths) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;

// 图标（风格接近 SF Symbols）
const I = {
  search: svg('<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>'),
  stack: svg('<rect x="4" y="9" width="16" height="11" rx="3"/><path d="M7 6h10M9.5 3h5"/>'),
  puzzle: svg('<path d="M10 4.5a2 2 0 1 1 4 0V6h4v4h-1.5a2 2 0 1 0 0 4H18v4h-4v-1.5a2 2 0 1 0-4 0V18H6v-4h1.5a2 2 0 1 0 0-4H6V6h4z"/>'),
  alert: svg('<path d="M10.3 4.3 2.9 17.5A2 2 0 0 0 4.6 20.5h14.8a2 2 0 0 0 1.7-3L13.7 4.3a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4M12 16.8v.1"/>'),
  sparkle: svg('<path d="M11 3c.8 4.6 2.4 6.2 7 7-4.6.8-6.2 2.4-7 7-.8-4.6-2.4-6.2-7-7 4.6-.8 6.2-2.4 7-7z"/><path d="M19 15c.3 1.6.9 2.2 2.5 2.5-1.6.3-2.2.9-2.5 2.5-.3-1.6-.9-2.2-2.5-2.5 1.6-.3 2.2-.9 2.5-2.5z"/>'),
  gear: svg('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
  rescan: svg('<path d="M20 12a8 8 0 1 1-2.3-5.6L20 8.5"/><path d="M20 4v4.5h-4.5"/>'),
  sync: svg('<path d="M20 11a8 8 0 0 0-14.3-4.3L4 8.5"/><path d="M4 4v4.5h4.5"/><path d="M4 13a8 8 0 0 0 14.3 4.3l1.7-1.8"/><path d="M20 20v-4.5h-4.5"/>'),
  cloud: svg('<path d="M7 19a4.5 4.5 0 0 1-.6-9 6 6 0 0 1 11.5 1.5A3.8 3.8 0 0 1 17.5 19z"/>'),
  cloudUp: svg('<path d="M7 18a4.5 4.5 0 0 1-.6-9 6 6 0 0 1 11.5 1.5A3.8 3.8 0 0 1 17.5 18"/><path d="M12 20v-8m-3 3 3-3 3 3"/>'),
  cloudDown: svg('<path d="M7 18a4.5 4.5 0 0 1-.6-9 6 6 0 0 1 11.5 1.5A3.8 3.8 0 0 1 17.5 18"/><path d="M12 12v8m-3-3 3 3 3-3"/>'),
  more: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5.5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="18.5" cy="12" r="1.6"/></svg>',
  plus: svg('<path d="M12 5v14M5 12h14"/>'),
  folder: svg('<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>'),
  trash: svg('<path d="M4.5 6.5h15M9.5 6.5v-2h5v2M6.5 6.5l1 13h9l1-13"/>'),
  x: svg('<path d="m6.5 6.5 11 11M17.5 6.5l-11 11"/>'),
  chev: svg('<path d="m7 9.5 5 5 5-5"/>'),
  check: svg('<path d="m5 12.5 4.5 4.5L19 7.5"/>'),
  ok: svg('<circle cx="12" cy="12" r="9"/><path d="m8 12.3 2.8 2.8L16.2 9.5"/>'),
  warn: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.4v.1"/>'),
  err: svg('<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>'),
  tag: svg('<path d="M3.5 12.5v-8a1 1 0 0 1 1-1h8l8 8-9 9z"/><circle cx="8" cy="8" r="1.3"/>'),
  pencil: svg('<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>'),
  power: svg('<path d="M12 3.5v8"/><path d="M6.7 6.6a7.5 7.5 0 1 0 10.6 0"/>'),
  bag: svg('<path d="M5 8h14l-1 12H6z"/><path d="M9 8V6.5a3 3 0 0 1 6 0V8"/>'),
  link: svg('<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>'),
  key: svg('<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l2 2M15 8l2 2"/>'),
  eye: svg('<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>'),
  copy: svg('<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5v-3a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3"/>'),
  plug: svg('<path d="M9 3v4M15 3v4"/><path d="M6.5 7h11v3.5a5.5 5.5 0 0 1-11 0z"/><path d="M12 16v5"/>'),
  doc: svg('<path d="M6.5 3.5h7l4 4v13h-11z"/><path d="M13.5 3.5v4h4M9 12h6M9 15.5h6"/>'),
};

// MCP 条目的头像颜色
const McpColor = "#30B0C7";

// ============================================================ 状态

// 界面状态
const ui = {
  view: "all",                 // 技能：all | attention | plugins | mcps | group:<id> | ungrouped | agents | settings；密钥：keys；全局规则：rules；软件设置：app
  skillView: "all",            // 离开技能空间前停在哪个视图，切回来时回到那里
  filter: "all",
  search: "",
  collapsed: new Set(JSON.parse(localStorage.getItem("collapsed") || "[]")),
  selected: new Set(),         // 勾选的条目 key
  focus: "",                   // 键盘焦点所在条目 key
  inspect: "",                 // 详情面板显示的条目 key
  busyRows: new Set(),         // 正在操作的条目 key
  renderedView: "",            // 上次渲染的视图，用来决定是否播放入场动画
  task: "",                    // 正在执行的长操作
  rulesDraft: null,            // 全局规则编辑中但还没保存的文本
  notice: new Set(),           // 外面新出现、还没提醒过的待上传条目 key
  keys: null,                  // 「密钥」页的列表（不含明文），见 keys.js
  keySel: "",                  // 密钥空间里选中的那条
  keySearch: "",               // 密钥空间的搜索词
  keyFilter: "all",            // 密钥空间的筛选：all | failed
  updateNotified: false,       // 本次打开是否已经提醒过有新版本
};
// 后端状态
let state = null;
// 按 key 索引的条目（skill、插件和 MCP）
let items = new Map();

/** HTML 转义。 */
function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

/** 调用后端接口，失败时抛出带中文消息的错误。 */
async function api(path, body = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Skill-Manager": "1" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({ error: `请求失败 ${response.status}` }));
  if (!response.ok) throw new Error(data.error || `请求失败 ${response.status}`);
  return data;
}

/** 是否需要上传：云端还没有，或本机改过。 */
function needsUpload(item) {
  return !item.inCloud || (item.kind !== "plugin" && item.modified);
}

/** 是否需要用户处理：没上传、有改动、没装全、旧目录重复。 */
function needsAttention(item) {
  if (item.kind === "plugin") return !item.inCloud;
  return !item.inCloud || item.modified || (item.enabled && item.missing.length > 0) || item.duplicate;
}

/** 分组颜色。 */
function groupColor(groupId) {
  const index = (state?.groups || []).findIndex((group) => group.id === groupId);
  return index < 0 ? "#8E8E93" : GroupColors[index % GroupColors.length];
}

/** 分组名。 */
function groupName(groupId) {
  return (state?.groups || []).find((group) => group.id === groupId)?.name || "";
}

/** 条目显示名：有中文名用中文名，否则用英文名。 */
function title(item) {
  return item.nameZh || (item.kind === "mcp" ? item.name : item.id.split("@")[0]);
}

/** 条目头像：插件和 MCP 用图标，skill 用首字。 */
function avatarHtml(item) {
  if (item.kind === "plugin") return { color: "#7C7C86", html: I.puzzle };
  if (item.kind === "mcp") return { color: McpColor, html: I.plug };
  return { color: groupColor(item.group), html: esc([...title(item)][0].toUpperCase()) };
}

/** 当前所在的空间：技能和密钥互不相干，软件设置单独一页。 */
function space() {
  return ["keys", "rules", "app"].includes(ui.view) ? ui.view : "skills";
}

/** 规则文件里和正本不一致的数量（侧栏提示用）。 */
function rulesPending() {
  const rules = state.rules;
  return rules.inCloud ? rules.targets.filter((t) => t.install && t.state !== "same").length : 0;
}

// ============================================================ 加载

/**
 * 重新拉取状态并刷新界面。
 * @param {boolean} notify 是否提醒新出现的待上传条目（只有自动发现外部改动时才提醒）
 */
async function refresh(notify = false) {
  // 密钥列表很快，先单独刷新，不等较慢的全量状态；否则刚保存完点「编辑」会拿到旧数据
  const keysLoaded = space() === "keys" ? loadKeys() : null;
  try {
    state = await api("/api/state");
  } catch (error) {
    toast(`读取状态失败：${error.message}`, { type: "err" });
    return;
  }
  const before = new Set([...items.values()].filter(needsUpload).map((item) => item.key));
  const first = items.size === 0;
  items = new Map();
  for (const skill of state.skills) items.set(`s:${skill.id}`, { ...skill, kind: "skill", key: `s:${skill.id}` });
  for (const plugin of state.plugins.items) {
    items.set(`p:${plugin.id}`, { ...plugin, kind: "plugin", key: `p:${plugin.id}`, description: plugin.note, missing: [] });
  }
  for (const server of state.mcps.items) items.set(`m:${server.id}`, { ...server, kind: "mcp", key: `m:${server.id}` });
  // 清掉已经不存在的选择
  for (const key of [...ui.selected]) if (!items.has(key)) ui.selected.delete(key);
  if (ui.inspect && !items.has(ui.inspect)) ui.inspect = "";
  // 自己点的操作不提醒；外面（比如 AI）新建或改了的才提醒
  if (notify && !first) {
    for (const item of items.values()) if (needsUpload(item) && !before.has(item.key)) ui.notice.add(item.key);
  }
  render();
  showNotice();
  await keysLoaded;
}

// ============================================================ 发现外部改动

/**
 * 问后端指纹，变了就重新扫描。后端靠系统通知知道有没有变，没变时这一问几乎不花时间。
 * 窗口最小化或被挡住时放慢到每分钟一次，只为更新任务栏标题里的数量。
 */
async function watchChanges() {
  // 正在执行操作、开着对话框或菜单、或正在输入时不打断
  const typing = document.activeElement?.matches?.("input, textarea, select");
  if (!state || ui.task || ui.watching || typing || document.querySelector("dialog[open], .menu")) return;
  if (document.hidden && Date.now() - (ui.watchedAt || 0) < 60000) return;
  ui.watchedAt = Date.now();
  ui.watching = true;
  try {
    const { stamp, update } = await api("/api/stamp");
    noticeUpdate(update);
    if (stamp !== state.stamp && !ui.task) await refresh(true);
  } catch {
    // 服务暂时没响应时下次再试
  } finally {
    ui.watching = false;
  }
}

/** 界面在前台时，把新出现的待上传条目提醒出来，带一键上传。 */
function showNotice() {
  for (const key of [...ui.notice]) if (!items.get(key) || !needsUpload(items.get(key))) ui.notice.delete(key);
  if (document.hidden || !ui.notice.size) return;
  const keys = [...ui.notice];
  ui.notice.clear();
  const names = keys.slice(0, 3).map((key) => title(items.get(key))).join("、") + (keys.length > 3 ? ` 等 ${keys.length} 项` : "");
  toast(`发现新改动，还没上传：${names}`, {
    type: "warn", action: "上传", duration: 15000,
    onAction: () => task(`上传 ${keys.length} 项`, async () => {
      for (const key of keys) {
        const item = items.get(key);
        if (item && needsUpload(item)) await api(`${base(item)}/upload`, { id: item.id });
      }
      return { message: `已上传 ${keys.length} 项` };
    }),
  });
}

/** 首次加载时显示骨架屏。 */
function renderSkeleton() {
  const rows = Array.from({ length: 8 }, (_, i) => `
    <div class="row"><span></span><div class="avatar" style="--c:var(--fill)"></div>
      <div><div class="sk" style="width:${60 + (i * 37) % 40}%"></div><div class="sk" style="width:40%;margin-top:7px;height:8px"></div></div>
      <div class="sk" style="width:${50 + (i * 53) % 45}%"></div><span></span><span></span></div>`).join("");
  document.getElementById("content").innerHTML = `<div class="group skeleton"><div class="card">${rows}</div></div>`;
  document.getElementById("toolbar").innerHTML = `<div><h1>全部</h1><div class="subtitle">正在扫描本机的 skill…</div></div>`;
}

// ============================================================ 渲染：总入口

/** 渲染整个界面。动画只在切换视图时播放。 */
function render() {
  if (!state) return;
  const current = space();
  const app = document.getElementById("app");
  app.classList.remove("space-skills", "space-keys", "space-rules", "space-app");
  app.classList.add(`space-${current}`);
  renderRail();
  renderSideHead();
  renderNav();
  renderCloudCard();
  renderToolbar();
  renderContent();
  renderInspector();
  renderBatch();
  // 详情面板和批量操作只属于技能空间
  app.classList.toggle("inspecting", current === "skills" && Boolean(ui.inspect));
}

/** 切换视图，支持时用 View Transitions 做淡入。 */
function go(view) {
  if (view === ui.view) return;
  const apply = () => {
    if (space() === "skills") ui.skillView = ui.view;
    ui.view = view;
    ui.filter = "all";
    ui.selected.clear();
    ui.focus = "";
    render();
    document.getElementById("content").scrollTop = 0;
    if (view === "keys") loadKeys();
  };
  if (document.startViewTransition && !ReducedMotion) document.startViewTransition(apply);
  else apply();
}

/** 切换空间；回到技能空间时停在上次看的视图。 */
function switchSpace(name) {
  if (name === space()) return;
  go(name === "skills" ? ui.skillView || "all" : name);
}

// ============================================================ 渲染：最左边的空间切换栏

/** 最左边一列：技能、密钥、全局规则三个空间，底部是软件设置。 */
function renderRail() {
  const attention = [...items.values()].filter(needsAttention).length;
  const failed = state.keys.failed;
  const current = space();
  // 任务栏标题带上待处理数量，窗口在后台也能看到
  document.title = attention ? `(${attention}) Skillset` : "Skillset";
  const button = (name, icon, label, badge, hint) => `
    <button class="rail-btn ${current === name ? "on" : ""}" data-space="${name}" title="${esc(hint)}" aria-pressed="${current === name}">
      <span class="rail-ico">${icon}${badge ? `<span class="rail-badge">${badge}</span>` : ""}</span><span class="rail-label">${label}</span></button>`;
  document.getElementById("rail").innerHTML = `
    <img class="rail-logo" src="icon.svg" alt="Skillset" title="Skillset ${Version}">
    ${button("skills", I.stack, "技能", attention, "skill、插件和 MCP 工具")}
    ${button("keys", I.key, "密钥", failed, failed ? `密钥 · ${failed} 条有失效的 key` : "API key、token 等密钥")}
    ${button("rules", I.doc, "规则", rulesPending(), "写到所有 AI 的全局规则")}
    <span class="grow"></span>
    ${button("app", I.gear, "设置", state.update?.behind ? "新" : "", "软件更新、快捷键和关于")}`;
}

/** 侧栏顶部：当前空间的名字。 */
function renderSideHead() {
  const head = document.getElementById("sideHead");
  if (space() === "keys") {
    // 正在搜索时不重建，免得丢光标
    if (document.activeElement?.id === "keySearch") return;
    head.innerHTML = `<div class="side-title"><b>密钥</b><span>只存在本机，加密保存</span></div>
      <div class="search side-search">${I.search}<input id="keySearch" class="field" placeholder="搜索名字、变量名、介绍" value="${esc(ui.keySearch)}" spellcheck="false"></div>`;
    return;
  }
  head.innerHTML = `<div class="side-title"><b>技能</b><span>装到所有 AI 的 skill 与工具</span></div>`;
}

// ============================================================ 渲染：侧栏

/** 侧栏导航。 */
function renderNav() {
  if (space() === "keys") return renderKeyNav();
  if (space() === "app" || space() === "rules") return;
  const all = [...items.values()];
  const count = (fn) => all.filter(fn).length;
  const attention = count(needsAttention);
  const navItem = (view, icon, name, n, extra = "") => `
    <div class="nav-item ${ui.view === view ? "active" : ""}" data-view="${esc(view)}" ${extra} role="button" tabindex="0">
      ${icon}<span class="name">${esc(name)}</span><span class="count">${n ?? ""}</span></div>`;

  const groups = state.groups.map((group) => navItem(`group:${group.id}`,
    `<span class="dot" style="background:${groupColor(group.id)}"></span>`, group.name,
    count((item) => item.group === group.id), `data-drop="${esc(group.id)}" data-group="${esc(group.id)}"`)).join("");

  document.getElementById("nav").innerHTML = `
    <div class="nav-title">资料库</div>
    ${navItem("all", `<span class="ico">${I.stack}</span>`, "全部", all.length)}
    ${navItem("plugins", `<span class="ico">${I.puzzle}</span>`, "插件", count((item) => item.kind === "plugin"))}
    ${navItem("mcps", `<span class="ico" style="color:${McpColor}">${I.plug}</span>`, "MCP 工具", count((item) => item.kind === "mcp"))}
    <div class="nav-item ${ui.view === "attention" ? "active" : ""}" data-view="attention" role="button" tabindex="0">
      <span class="ico" style="color:var(--orange)">${I.alert}</span><span class="name">需要处理</span>
      <span class="count ${attention ? "alert" : ""}">${attention || ""}</span></div>
    <div class="nav-title">分组<button class="btn ghost icon" data-act="group-add" title="新建分组">${I.plus}</button></div>
    ${groups}
    ${navItem("ungrouped", `<span class="dot" style="background:var(--fill)"></span>`, "未分组",
      count((item) => !groupName(item.group)), 'data-drop=""')}
    <div class="nav-title">设置</div>
    ${navItem("agents", `<span class="ico" style="color:var(--purple)">${I.sparkle}</span>`, "AI 应用",
      state.agents.filter((agent) => agent.active).length)}
    ${navItem("settings", `<span class="ico" style="color:var(--blue)">${I.cloud}</span>`, "云端同步", "")}`;
}

/** 侧栏底部的状态卡片：技能空间显示云端状态，密钥空间提醒备份。 */
function renderCloudCard() {
  const card = document.getElementById("cloudCard");
  if (space() === "keys") {
    card.className = "cloud local";
    card.title = "导出备份";
    card.innerHTML = `<span class="state-dot"></span><div class="txt"><b>加密存在本机</b><span>换电脑前记得导出备份</span></div>`;
    return;
  }
  card.title = "云端同步";
  const git = state.git;
  let cls = "", head = "已和云端同步", sub = repoName(git.remote);
  if (git.error) {
    cls = "off"; head = "云端状态未知"; sub = git.error;
  } else if (!git.remote) {
    cls = "off"; head = "只在本机"; sub = "点这里连接 GitHub";
  } else if (git.ahead || git.dirty) {
    cls = "warn"; head = git.ahead ? `${git.ahead} 个改动未推送` : "有改动未推送";
  } else if (git.behind) {
    cls = "warn"; head = `云端有 ${git.behind} 个更新`;
  }
  card.className = `cloud ${cls}`;
  card.innerHTML = `<span class="state-dot"></span><div class="txt"><b>${esc(head)}</b><span>${esc(sub)}</span></div>`;
}

/** 从仓库地址取出 owner/repo。 */
function repoName(url) {
  return String(url || "").replace(/^https?:\/\/[^/]+\//, "").replace(/^git@[^:]+:/, "").replace(/\.git$/, "");
}

// ============================================================ 渲染：工具栏

/** 当前视图的标题、副标题和按钮。 */
function renderToolbar() {
  const bar = document.getElementById("toolbar");
  const sub = document.getElementById("subbar");
  const list = viewItems(false);
  const on = list.filter((item) => item.enabled).length;
  let heading = "全部", subtitle = `${list.length} 项 · 已启用 ${on}`, extra = "";

  if (ui.view === "attention") {
    heading = "需要处理";
    subtitle = list.length ? "这些条目没上传、有改动或没装全" : "一切正常";
    const off = [...items.values()].filter((item) => !item.enabled).length;
    extra = `<button class="btn" data-act="deleteDisabled" ${off ? "" : "disabled"} title="彻底删除所有关闭着的 skill、插件和 MCP">${I.trash}删除已关闭的${off ? ` ${off} 项` : ""}</button>`;
  } else if (ui.view === "plugins") {
    heading = "插件";
    subtitle = `Claude Code 插件 · ${list.length} 个`;
    extra = `<button class="btn" data-act="market">${I.bag}浏览市场</button>`;
  } else if (ui.view === "mcps") {
    heading = "MCP 工具";
    subtitle = `${list.length} 个 · 已启用 ${on} · 改动后要重开对应的 AI 才生效`;
  } else if (ui.view === "rules") {
    heading = "全局规则";
    subtitle = "一份规则，写到所有 AI 的全局规则文件（CLAUDE.md、AGENTS.md、GEMINI.md…）";
  } else if (ui.view === "ungrouped") {
    heading = "未分组";
  } else if (ui.view.startsWith("group:")) {
    const group = state.groups.find((g) => `group:${g.id}` === ui.view);
    heading = group?.name || "";
    subtitle = group?.note || subtitle;
    const allOn = list.length > 0 && on === list.length;
    extra = `<label class="switch ${on && !allOn ? "partial" : ""}" title="整组启用或关闭">
        <input type="checkbox" data-act="group-toggle" data-id="${esc(group?.id)}" ${on ? "checked" : ""}>
        <span class="track"></span><span class="thumb"></span></label>
      <button class="btn ghost icon" data-act="group-menu" data-id="${esc(group?.id)}" title="分组操作">${I.more}</button>`;
  } else if (ui.view === "agents") {
    heading = "AI 应用";
    subtitle = "选择要把 skill 装到哪些 AI";
  } else if (ui.view === "settings") {
    heading = "云端同步";
    subtitle = "skill 库存在你自己的 GitHub 仓库里，换电脑也能一键装回";
  } else if (ui.view === "app") {
    heading = "软件设置";
    subtitle = `Skillset ${Version}`;
  } else if (ui.view === "keys") {
    heading = "密钥";
    subtitle = `${state.keys.count} 条` + (state.keys.failed ? ` · ${state.keys.failed} 条有失效的 key` : "");
    extra = `<button class="btn" data-act="key-menu">${I.cloudDown}导入${I.chev}</button>
      <button class="btn" data-act="key-export" title="用密码加密导出，换电脑时导回来">${I.cloudUp}导出备份</button>
      <button class="btn primary" data-act="key-new">${I.plus}新建密钥</button>`;
  }

  const listView = space() === "skills" && !["agents", "settings"].includes(ui.view);
  // 扫描、更新、同步只属于技能空间
  const skillTools = space() !== "skills" ? "" : `
    <button class="btn icon ghost" data-act="rescan" title="重新扫描本机（F5）">${I.rescan}</button>
    <button class="btn" data-act="update" title="从云端拉取最新内容并装到本机">${I.cloudDown}更新</button>
    <button class="btn primary" data-act="sync" title="把启用的 skill 装到所有选中的 AI，关闭的删掉">${I.sync}同步到所有 AI</button>
    <button class="btn icon ghost" data-act="app-menu" title="更多">${I.more}</button>`;
  bar.innerHTML = `
    <div class="grow" style="min-width:0"><h1>${esc(heading)}</h1><div class="subtitle">${esc(subtitle)}</div></div>
    ${extra}
    ${listView ? `<div class="search">${I.search}<input id="search" class="field" placeholder="搜索" value="${esc(ui.search)}" spellcheck="false"><span class="kbd">Ctrl K</span></div>` : ""}
    ${skillTools}`;

  // 搜索和筛选只在列表视图出现
  if (listView && ui.view !== "attention") {
    const base = viewItems(false, true);
    sub.innerHTML = `<div class="segmented" role="tablist">${Object.entries(Filters).map(([key, [name, fn]]) =>
      `<button role="tab" class="${ui.filter === key ? "on" : ""}" data-filter="${key}">${name}<span class="n">${base.filter(fn).length}</span></button>`).join("")}
      <span class="glider"></span></div>`;
    sub.classList.remove("hidden");
    requestAnimationFrame(moveGlider);
  } else {
    sub.innerHTML = "";
    sub.classList.add("hidden");
  }
}

/** 把分段控件的滑块移到选中项下面。 */
function moveGlider() {
  const seg = document.querySelector(".segmented");
  const button = seg?.querySelector("button.on");
  const glider = seg?.querySelector(".glider");
  if (!button || !glider) return;
  glider.style.width = `${button.offsetWidth}px`;
  glider.style.transform = `translateX(${button.offsetLeft - 2}px)`;
}

// ============================================================ 渲染：列表

/**
 * 当前视图要显示的条目。
 * @param {boolean} applyFilter 是否应用分段筛选
 * @param {boolean} applySearch 是否应用搜索（默认跟随 applyFilter）
 */
function viewItems(applyFilter = true, applySearch = applyFilter) {
  let list = [...items.values()];
  const view = ui.view;
  if (view === "plugins") list = list.filter((item) => item.kind === "plugin");
  else if (view === "mcps") list = list.filter((item) => item.kind === "mcp");
  else if (view === "attention") list = list.filter(needsAttention);
  else if (view === "ungrouped") list = list.filter((item) => !groupName(item.group));
  else if (view.startsWith("group:")) list = list.filter((item) => `group:${item.group}` === view);
  if (applySearch && ui.search) {
    const words = ui.search.toLowerCase().split(/\s+/).filter(Boolean);
    list = list.filter((item) => {
      const text = `${item.id} ${item.nameZh} ${item.note} ${item.description}`.toLowerCase();
      return words.every((word) => text.includes(word));
    });
  }
  if (applyFilter && ui.view !== "attention") list = list.filter(Filters[ui.filter][1]);
  return list;
}

/** 列表区。 */
function renderContent() {
  const content = document.getElementById("content");
  if (ui.view === "agents") return swap(content, renderAgents());
  if (ui.view === "settings") return swap(content, renderSettings());
  if (ui.view === "app") return swap(content, renderAppSettings());
  if (ui.view === "keys") return swap(content, renderKeys());
  if (ui.view === "rules") {
    // 正在输入时不重建，避免丢光标
    if (document.activeElement?.id === "rulesText") return;
    return swap(content, renderRules());
  }

  const list = viewItems();
  const animate = ui.renderedView !== ui.view;
  ui.renderedView = ui.view;
  let index = 0;
  const rows = (entries) => entries.map((item) => rowHtml(item, animate ? index++ : -1)).join("");

  if (ui.view === "plugins" && state.plugins.error) {
    return swap(content, emptyHtml(I.puzzle, "读不到插件", state.plugins.error + "\n需要先安装 Claude Code 命令行。"));
  }
  if (ui.view === "mcps" && state.mcps.errors.length && !list.length) {
    return swap(content, emptyHtml(I.plug, "读不到 MCP 配置", state.mcps.errors.join("\n")));
  }
  if (!list.length) {
    if (ui.search) return swap(content, emptyHtml(I.search, "没有找到", `没有和“${ui.search}”匹配的条目`));
    if (ui.view === "attention") return swap(content, emptyHtml(I.ok, "一切正常", "所有条目都已上传，且装到了所有选中的 AI。"));
    if (ui.view.startsWith("group:")) return swap(content, emptyHtml(I.tag, "这个分组还是空的", "把列表里的条目拖到左侧分组上，或在详情里选择分组。"));
    return swap(content, emptyHtml(I.stack, "这里没有内容", "试试换个筛选条件。"));
  }

  // “全部”视图按分组分节，其余视图平铺
  let html = "";
  if (ui.view === "all") {
    const sections = state.groups.map((group) => ({ id: group.id, name: group.name, note: group.note, color: groupColor(group.id),
      entries: list.filter((item) => item.group === group.id) }));
    sections.push({ id: "", name: "未分组", note: "", color: "var(--label-3)", entries: list.filter((item) => !groupName(item.group)) });
    for (const section of sections.filter((s) => s.entries.length)) {
      const collapsed = ui.collapsed.has(section.id || "_none");
      const on = section.entries.filter((item) => item.enabled).length;
      html += `<section class="group ${collapsed ? "collapsed" : ""}">
        <div class="group-head">
          <button class="btn ghost icon" data-act="collapse" data-id="${esc(section.id || "_none")}" aria-label="折叠" style="width:20px;height:20px"><span class="chev" style="display:flex">${I.chev}</span></button>
          <span class="dot" style="background:${section.color}"></span>
          <h2>${esc(section.name)}</h2>
          <span class="meta">${section.entries.length} 项${on < section.entries.length ? ` · 启用 ${on}` : ""}</span>
          ${section.id ? `<button class="note ${section.note ? "" : "empty"}" data-act="group-edit" data-id="${esc(section.id)}"
            title="${section.note ? esc(section.note) + "（点击编辑）" : "添加说明"}">${esc(section.note || "添加说明")}</button>` : ""}
          ${section.id ? `<div class="acts"><label class="switch small ${on && on < section.entries.length ? "partial" : ""}" title="整组启用或关闭">
            <input type="checkbox" data-act="group-toggle" data-id="${esc(section.id)}" ${on ? "checked" : ""}>
            <span class="track"></span><span class="thumb"></span></label>
            <button class="btn ghost icon" data-act="group-menu" data-id="${esc(section.id)}" style="width:24px;height:24px">${I.more}</button></div>` : ""}
        </div>
        <div class="card">${rows(section.entries)}</div></section>`;
    }
  } else {
    html = `<section class="group"><div class="card">${rows(list)}</div></section>`;
  }
  swap(content, html, ui.selected.size ? "selecting" : "");
}

/** 替换内容并保留滚动位置。 */
function swap(element, html, cls = "") {
  const top = element.scrollTop;
  element.innerHTML = html;
  element.className = `content ${cls}`;
  element.scrollTop = top;
}

/** 空状态。 */
function emptyHtml(icon, head, text) {
  return `<div class="empty">${icon}<b>${esc(head)}</b><span style="white-space:pre-wrap">${esc(text)}</span></div>`;
}

/** 状态小标签。 */
function chipsHtml(item) {
  const chips = [];
  if (item.kind === "plugin") {
    if (!item.inCloud) chips.push(`<span class="chip warn" title="还没记录到云端清单">未记录</span>`);
    if (!item.installed && item.inCloud) chips.push(`<span class="chip plain">仅云端</span>`);
    if (item.skills?.length) chips.push(`<span class="chip plain">${item.skills.length} 个 skill</span>`);
    return chips.join("");
  }
  if (!item.inCloud) chips.push(`<span class="chip warn" title="云端没有备份">未上传</span>`);
  if (item.modified) chips.push(`<span class="chip warn" title="本机副本和云端不一样">有改动</span>`);
  if (item.enabled && item.missing.length) chips.push(`<span class="chip warn" title="没装到：${esc(item.missing.join("、"))}">缺 ${item.missing.length} 处</span>`);
  if (item.duplicate) chips.push(`<span class="chip danger" title="旧目录里还有一份，同一个 AI 会读到两次">重复</span>`);
  if (!item.enabled && item.inCloud) chips.push(`<span class="chip plain">仅云端</span>`);
  return chips.join("");
}

/** 一行条目。index ≥ 0 时播放错开的入场动画。 */
function rowHtml(item, index) {
  const key = item.key;
  const busy = ui.busyRows.has(key);
  const avatar = avatarHtml(item);
  const desc = item.note || item.description || "";
  const cls = [!item.enabled && "off", ui.selected.has(key) && "selected", ui.focus === key && "focused", index >= 0 && "enter"].filter(Boolean).join(" ");
  const delay = index >= 0 ? ` style="animation-delay:${Math.min(index, 14) * 16}ms"` : "";
  return `<div class="row ${cls}" data-key="${esc(key)}" draggable="true"${delay}>
    <input type="checkbox" class="check" data-act="check" ${ui.selected.has(key) ? "checked" : ""} aria-label="选择">
    <div class="avatar" style="--c:${avatar.color}">${avatar.html}</div>
    <div class="names"><div class="zh">${esc(title(item))}</div><div class="en">${esc(item.kind === "mcp" ? item.name : item.id)}</div></div>
    <div class="desc" title="${esc(desc)}">${esc(desc)}</div>
    <div class="chips">${chipsHtml(item)}</div>
    <div class="row-end">
      <label class="switch ${busy ? "busy" : ""}" title="${item.enabled ? "关闭" : "启用"}（空格）">
        <input type="checkbox" data-act="toggle" ${item.enabled ? "checked" : ""} ${busy ? "disabled" : ""} aria-label="启用">
        <span class="track"></span><span class="thumb"></span></label>
      <button class="btn ghost icon more" data-act="row-menu" aria-label="更多操作">${I.more}</button>
    </div></div>`;
}

// ============================================================ 渲染：AI 应用页

/** AI 应用页：选择安装目标。 */
function renderAgents() {
  const installed = state.agents.filter((agent) => agent.installed || agent.active);
  const others = state.agents.filter((agent) => !agent.installed && !agent.active);
  const row = (agent) => `<div class="list-row">
      <div class="app-ico" style="background:${agentColor(agent.name)}">${esc(agent.name[0])}</div>
      <div class="lbl"><b>${esc(agent.name)}${agent.readsShared ? ' <span class="chip info plain" style="margin-left:4px">共享目录</span>' : ""}</b>
        <span class="mono" title="${esc(agent.installDir)}">${esc(agent.installDir)}</span></div>
      ${agent.installed ? "" : '<span class="chip plain">未检测到</span>'}
      <label class="switch"><input type="checkbox" data-act="target" data-id="${esc(agent.id)}" ${agent.active ? "checked" : ""}>
        <span class="track"></span><span class="thumb"></span></label></div>`;
  return `<div class="page">
    <div class="section-title">本机已安装</div>
    <div class="card">${installed.map(row).join("") || '<div class="empty-row">没检测到任何 AI 应用</div>'}</div>
    <div class="section-foot">打开的 AI 会在「同步到所有 AI」时装上已启用的 skill。<br>
      <code>~/.agents/skills</code> 是通用目录，Codex、Gemini CLI、Cursor、Copilot 等十几个 AI 都会读它，所以它们共用一份。</div>
    <div class="section-title" style="margin-top:28px">其他支持的 AI（${others.length}）</div>
    <div class="card">${others.map(row).join("")}</div>
    <div class="section-foot">这些 AI 本机没检测到，也可以提前打开，装上后就能直接用。</div></div>`;
}

/** 按名字生成稳定的颜色。 */
function agentColor(name) {
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return GroupColors[hash % GroupColors.length];
}

// ============================================================ 渲染：设置页

/** 技能空间的「云端同步」页：云端仓库和扫描的目录。 */
function renderSettings() {
  const git = state.git;
  const cfg = state.config;
  const connected = Boolean(git.remote);
  const statusText = git.error ? git.error : !connected ? "skill 只保存在这台电脑上。连接 GitHub 私有仓库后，换电脑也能一键装回。"
    : git.ahead ? `有 ${git.ahead} 个改动还没推送` : git.dirty ? "有改动还没推送" : git.behind ? `云端有 ${git.behind} 个更新，点「更新」拉取` : "本机和云端一致";
  const dirs = state.dirs.map((dir) => `<div class="list-row">
      <div class="lbl"><b>${esc(dir.label)}</b><span class="mono" title="${esc(dir.path)}">${esc(dir.path)}</span></div>
      ${dir.install ? '<span class="chip ok">安装目标</span>' : ""}${dir.legacy ? '<span class="chip warn">旧目录</span>' : ""}
      ${dir.exists ? `<button class="btn sm" data-act="open" data-path="${esc(dir.path)}">${I.folder}打开</button>` : '<span class="chip plain">还没创建</span>'}</div>`).join("");
  return `<div class="page">
    <div class="section-title">云端仓库</div>
    <div class="card">
      <div class="cloud-hero">
        <div class="big-ico" style="${connected ? "" : "background:linear-gradient(160deg,#C7C7CC,#8E8E93);box-shadow:none"}">${I.cloud}</div>
        <div class="grow" style="min-width:0"><h3>${connected ? esc(repoName(git.remote)) : "未连接云端"}</h3><p>${esc(statusText)}</p></div>
        ${connected && (git.ahead || git.dirty) ? `<button class="btn" data-act="push">${I.cloudUp}推送</button>` : ""}
        <button class="btn ${connected ? "" : "primary"}" data-act="connect">${connected ? "更换仓库" : "连接 GitHub"}</button>
      </div>
      <div class="list-row"><div class="lbl"><b>改动后自动推送</b><span>上传、删除、改名后马上推送到云端</span></div>
        <label class="switch"><input type="checkbox" data-act="autopush" ${cfg.autoPush ? "checked" : ""}><span class="track"></span><span class="thumb"></span></label></div>
      <div class="list-row"><div class="lbl"><b>本机库位置</b><span class="mono">${esc(cfg.libraryPath)}</span></div>
        <button class="btn sm" data-act="open" data-path="${esc(cfg.libraryPath)}">${I.folder}打开</button></div>
    </div>
    <div class="section-foot">云端就是你自己的 git 仓库（建议私有）。上传前会自动检查 key、token 之类的密钥，发现就拦下。</div>

    <div class="section-title">扫描的目录</div>
    <div class="card">${dirs}</div>
    <div class="section-foot">「重复」指旧目录和新目录里各有一份，同一个 AI 会读到两次，可以在右上角菜单里一键清理。</div>
  </div>`;
}

/** 「软件设置」页：更新、快捷键和关于，不属于技能也不属于密钥。 */
function renderAppSettings() {
  const keys = [["Ctrl K", "命令面板：在当前空间里搜索、执行操作"], ["Ctrl F", "搜索"], ["↑ ↓", "在列表中移动"], ["空格", "启用 / 关闭（技能）"],
    ["Enter", "打开详情（技能）"], ["Delete", "彻底删除（技能）"], ["Ctrl A", "全选当前列表（技能）"], ["Esc", "关闭面板 / 取消选择"], ["F5", "重新读取"]];
  return `<div class="page">
    <div class="section-title">关于</div>
    <div class="card"><div class="cloud-hero">
      <img src="icon.svg" alt="" style="width:52px;height:52px">
      <div class="grow"><h3>Skillset ${Version}</h3><p>开源的 AI skill 与密钥管理器 · MIT 许可</p></div>
      <a class="btn" href="${RepoUrl}" target="_blank" rel="noopener">${I.link}GitHub</a></div>
      ${updateRowHtml()}</div>
    <div class="section-foot">每 6 小时自动查一次有没有新版本。</div>

    <div class="section-title">备份</div>
    <div class="card"><div class="list-row"><div class="lbl"><b>导出本机全部</b>
        <span>skill、插件、MCP 工具、全局规则和密钥，打成一个 zip 存到下载文件夹</span></div>
      <button class="btn sm" data-act="exportAll">${I.cloudUp}导出</button></div></div>
    <div class="section-foot">换电脑或重装前用。密钥用你设的密码加密，怎么恢复写在 zip 里的「清单.md」。</div>

    <div class="section-title">键盘快捷键</div>
    <div class="card">${keys.map(([key, text]) => `<div class="list-row"><div class="lbl">${esc(text)}</div><span class="kbd">${esc(key)}</span></div>`).join("")}</div>
  </div>`;
}

/** 关于里的“软件更新”一行。 */
function updateRowHtml() {
  const update = state.update || {};
  if (!update.git) {
    return `<div class="list-row"><div class="lbl"><b>软件更新</b><span>不是用 git 安装的，没法自动更新，请到 GitHub 下载新版本</span></div></div>`;
  }
  const text = update.error ? `检查失败：${update.error}`
    : update.behind ? `有新版本（${update.behind} 个更新）：${update.latest}`
    : update.checkedAt ? `已是最新 · ${update.current}` : "还没检查过";
  return `<div class="list-row"><div class="lbl"><b>软件更新${update.behind ? ' <span class="chip info">有新版本</span>' : ""}</b>
      <span title="${esc(text)}">${esc(text)}${update.checkedAt ? ` · 检查于 ${esc(update.checkedAt)}` : ""}</span></div>
    <button class="btn sm" data-act="appCheck">检查更新</button>
    ${update.behind ? '<button class="btn sm primary" data-act="appUpdate">更新并重启</button>' : ""}</div>`;
}

// ============================================================ 渲染：全局规则页

/** 全局规则页：编辑正本，查看各 AI 的规则文件是否一致。 */
function renderRules() {
  const rules = state.rules;
  const text = ui.rulesDraft ?? rules.text;
  const dirty = ui.rulesDraft !== null && ui.rulesDraft !== rules.text;
  const stateChip = { same: '<span class="chip ok">一致</span>', different: `<span class="chip warn">${rules.inCloud ? "不一致" : "已有内容"}</span>`,
    missing: '<span class="chip plain">还没有</span>' };
  const rows = rules.targets.map((target) => `<div class="list-row">
      <div class="lbl"><b>${esc(target.label)}</b><span class="mono" title="${esc(target.path)}">${esc(target.path)}</span></div>
      ${target.install ? "" : '<span class="chip plain">未勾选</span>'}${stateChip[target.state] || ""}
      ${target.exists ? `<button class="btn sm" data-act="rules-import" data-path="${esc(target.path)}" title="用这个文件的内容替换上面的正本">导入</button>
        <button class="btn ghost icon sm" data-act="open" data-path="${esc(target.path)}" title="打开所在位置">${I.folder}</button>` : ""}</div>`).join("");
  return `<div class="page rules-page">
    <div class="section-title">规则正本${dirty ? ' <span class="chip warn plain">未保存</span>' : ""}</div>
    <div class="card rules-card">
      <textarea id="rulesText" class="field mono" spellcheck="false" placeholder="${rules.inCloud ? "" : "还没有全局规则。可以直接写，也可以从下面某个 AI 导入现有的一份。"}">${esc(text)}</textarea>
      <div class="rules-actions">
        <span class="faint grow">${rules.inCloud ? "保存在库的 rules/global.md，跟着云端同步" : "还没保存过"}</span>
        <button class="btn" data-act="rules-save" ${dirty ? "" : "disabled"}>保存</button>
        <button class="btn primary" data-act="rules-apply" ${rules.inCloud && !dirty ? "" : "disabled"} title="${dirty ? "先保存" : "写到所有勾选的 AI"}">${I.sync}应用到所有 AI</button>
      </div>
    </div>
    <div class="section-foot">应用时会覆盖各 AI 的规则文件，原文件先备份到 data/backups。「同步到所有 AI」只给还没有规则文件的 AI 写上，不覆盖已有的。</div>
    <div class="section-title">各 AI 的规则文件</div>
    <div class="card">${rows || '<div class="empty-row">没有检测到支持全局规则的 AI</div>'}</div>
    <div class="section-foot">Gemini CLI 和 Antigravity 共用 <code>~/.gemini/GEMINI.md</code>。</div>
  </div>`;
}

/** 保存规则正本。 */
async function saveRules() {
  const text = ui.rulesDraft ?? state.rules.text;
  const ok = await task("保存全局规则", () => api("/api/rules/save", { text }));
  if (ok) ui.rulesDraft = null;
  renderContent();
}

/** 把正本写到所有 AI，有不一致的先确认。 */
async function applyRules() {
  await task("应用全局规则", async () => {
    const first = await api("/api/rules/apply");
    return confirmed("/api/rules/apply", {}, first, { head: "覆盖各 AI 现有的规则文件？", action: "覆盖" });
  });
}

/** 从某个 AI 的规则文件导入正本。 */
async function importRules(path) {
  if (ui.rulesDraft !== null && ui.rulesDraft !== state.rules.text) {
    const choice = await confirmAlert({ head: "放弃没保存的修改？", message: "导入会用这个文件的内容替换正本。", action: "导入" });
    if (choice !== "ok") return;
  }
  const ok = await task("导入全局规则", () => api("/api/rules/import", { path }));
  if (ok) ui.rulesDraft = null;
  renderContent();
}

// ============================================================ 渲染：详情面板

/** 右侧详情面板。正在编辑时不重建，避免打断输入。 */
function renderInspector() {
  const inner = document.getElementById("inspectorInner");
  const item = items.get(ui.inspect);
  if (!item) return;
  if (inner.contains(document.activeElement) && inner.dataset.key === item.key) return;
  inner.dataset.key = item.key;
  const busy = ui.busyRows.has(item.key);
  const groups = `<option value="">未分组</option>` + state.groups.map((group) =>
    `<option value="${esc(group.id)}" ${group.id === item.group ? "selected" : ""}>${esc(group.name)}</option>`).join("");
  const isSkill = item.kind === "skill";
  const isMcp = item.kind === "mcp";
  // skill 和 MCP 都有“正本 + 本机副本”
  const hasCopies = item.kind !== "plugin";
  const toggleText = hasCopies
    ? (item.enabled ? `已启用 · 装在 ${item.copies.length} 处` : item.inCloud ? "已关闭 · 云端保留，随时装回" : "已关闭")
    : (item.enabled ? `已启用 · ${item.version || ""}` : item.installed ? "已安装但未启用" : "未安装 · 云端有记录");

  const stateChip = { same: '<span class="chip ok">一致</span>', modified: '<span class="chip warn">有改动</span>', local: '<span class="chip info">仅本机</span>' };
  const copies = hasCopies && item.copies.length ? `<div class="form-row"><label>${isMcp ? "各 AI 的配置" : "本机副本"}</label><div class="copy-list">${item.copies.map((copy) =>
    `<div class="copy-item"><div class="where"><b>${esc(copy.label)}${isMcp && copy.name !== item.name ? `<span class="faint">（名字 ${esc(copy.name)}）</span>` : ""}</b><span title="${esc(copy.dir)}">${esc(copy.dir)}</span></div>
      ${copy.legacy ? '<span class="chip warn plain">旧目录</span>' : ""}${stateChip[copy.state] || ""}
      <button class="btn ghost icon sm" data-act="open" data-path="${esc(isMcp ? copy.dir : copy.dir + "/" + item.id)}" title="${isMcp ? "打开配置文件所在位置" : "打开文件夹"}">${I.folder}</button></div>`).join("")}</div></div>` : "";
  const missing = hasCopies && item.enabled && item.missing.length
    ? `<div class="form-row"><label>还没装到</label><div class="readonly">${esc(item.missing.join("、"))}<br>点「同步到所有 AI」补齐。</div></div>` : "";
  const subSkills = !isSkill && item.skills?.length ? `<div class="form-row"><label>包含的 skill（随插件一起开关）</label><div class="copy-list">${item.skills.map((skill) =>
    `<div class="sub-skill"><code>${esc(skill.name)}</code><span>${esc(skill.description)}</span></div>`).join("")}</div></div>` : "";
  const canUpload = hasCopies ? (!item.inCloud || item.modified) : !item.inCloud;
  const avatar = avatarHtml(item);
  const spec = item.spec || {};
  const specRows = isMcp ? (spec.type === "stdio"
    ? [["命令", spec.command], ["参数", (spec.args || []).join(" ")], ...Object.entries(spec.env || {}).map(([k, v]) => ["环境变量", `${k}=${v}`])]
    : [["地址", spec.url], ...Object.entries(spec.headers || {}).map(([k, v]) => ["请求头", `${k}: ${v}`])]).filter(([, v]) => v !== "") : [];
  const specBlock = isMcp ? `<div class="form-row"><label>配置（${spec.type === "stdio" ? "本地命令" : spec.type === "sse" ? "远程 SSE" : "远程 HTTP"}）</label>
    <div class="spec">${specRows.map(([k, v]) => `<div><span>${esc(k)}</span><code>${esc(v)}</code></div>`).join("")}</div>
    ${item.secretEnv.length ? `<div class="hint">${I.key}这些像密钥，只把名字存到云端，值留在本机：${esc(item.secretEnv.join("、"))}</div>` : ""}</div>` : "";

  inner.innerHTML = `
    <div class="insp-head">
      <div class="avatar" style="--c:${avatar.color}">${avatar.html}</div>
      <div style="min-width:0"><h3>${esc(title(item))}</h3><div class="en">${esc(isMcp ? item.name : item.id)}</div></div>
      <button class="btn ghost icon close" data-act="inspect-close" title="关闭（Esc）">${I.x}</button>
    </div>
    <div class="insp-toggle">
      <div class="lbl"><b>${item.enabled ? "启用中" : "已关闭"}</b><span>${esc(toggleText)}</span></div>
      <label class="switch ${busy ? "busy" : ""}"><input type="checkbox" data-act="toggle" data-key="${esc(item.key)}" ${item.enabled ? "checked" : ""} ${busy ? "disabled" : ""}>
        <span class="track"></span><span class="thumb"></span></label>
    </div>
    <div class="form-row"><label>中文名<span class="saved-tick" data-tick="nameZh">已保存</span></label>
      <input class="field" data-field="nameZh" value="${esc(item.nameZh)}" placeholder="${esc(isMcp ? item.name : item.id)}"></div>
    <div class="form-row"><label>介绍<span class="saved-tick" data-tick="note">已保存</span></label>
      <textarea class="field" data-field="note" placeholder="一句话说清它是干什么的">${esc(item.note)}</textarea></div>
    <div class="form-row"><label>分组<span class="saved-tick" data-tick="group">已保存</span></label>
      <select class="field" data-field="group">${groups}</select></div>
    ${isSkill && item.description ? `<div class="form-row"><label>原始说明（SKILL.md）</label><div class="readonly">${esc(item.description)}</div></div>` : ""}
    ${specBlock}${missing}${copies}${subSkills}
    <div class="insp-actions">
      ${canUpload ? `<button class="btn primary" data-act="upload" data-key="${esc(item.key)}">${I.cloudUp}${hasCopies ? (item.inCloud ? "上传改动到云端" : "上传到云端") : "记录到云端清单"}</button>` : ""}
      <button class="btn danger" data-act="delete" data-key="${esc(item.key)}">${I.trash}彻底删除…</button>
    </div>`;
}

/** 打开详情面板。 */
function inspect(key) {
  ui.inspect = key;
  ui.focus = key;
  document.getElementById("inspectorInner").dataset.key = "";
  render();
}

/** 关闭详情面板。 */
function closeInspector() {
  ui.inspect = "";
  document.getElementById("app").classList.remove("inspecting");
}

/** 详情里的字段失去焦点或改变时保存。 */
async function saveField(element) {
  const item = items.get(ui.inspect);
  const field = element.dataset.field;
  const value = element.value.trim();
  if (!item || (item[field] || "") === value) return;
  try {
    await api("/api/item/edit", { kind: item.kind, id: item.id, fields: { [field]: value } });
    item[field] = value;
    const tick = document.querySelector(`[data-tick="${field}"]`);
    if (tick) {
      tick.classList.add("show");
      setTimeout(() => tick.classList.remove("show"), 1400);
    }
    renderRail();
    renderNav();
    renderContent();
    if (field === "group") refresh();
  } catch (error) {
    toast(error.message, { type: "err" });
  }
}

// ============================================================ 批量操作条

/** 有勾选时底部出现批量操作条。 */
function renderBatch() {
  const box = document.getElementById("batch");
  const count = ui.selected.size;
  if (!count) {
    box.innerHTML = "";
    return;
  }
  if (box.firstElementChild) {
    box.querySelector(".count").textContent = `已选 ${count} 项`;
    return;
  }
  box.innerHTML = `<div class="batchbar" role="toolbar">
    <span class="count">已选 ${count} 项</span>
    <button class="btn sm" data-act="batch-on">${I.power}启用</button>
    <button class="btn sm" data-act="batch-off">关闭</button>
    <button class="btn sm" data-act="batch-upload">${I.cloudUp}上传</button>
    <button class="btn sm" data-act="batch-group">${I.tag}移到分组</button>
    <button class="btn sm icon" data-act="batch-clear" title="取消选择（Esc）">${I.x}</button></div>`;
}

// ============================================================ 弹窗

/**
 * 打开一个弹窗（原生 dialog），返回用户的选择。
 * 带 data-close 的按钮点击后关闭，值为 data-close；Esc 或点遮罩返回 null。
 * @param {string} html 内容
 * @param {string} cls 额外类名（alert / wide / palette）
 * @param {Function} setup 打开后调用 setup(dialog, close)
 */
function sheet(html, cls = "", setup = null) {
  return new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.className = `sheet ${cls}`;
    dialog.innerHTML = html;
    document.body.appendChild(dialog);
    let done = false;
    const close = (value = null) => {
      if (done) return;
      done = true;
      dialog.classList.add("closing");
      setTimeout(() => {
        dialog.close();
        dialog.remove();
        resolve(value);
      }, ReducedMotion ? 0 : 150);
    };
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      close(null);
    });
    dialog.addEventListener("mousedown", (event) => {
      if (event.target === dialog) close(null);
    });
    dialog.addEventListener("click", (event) => {
      const button = event.target.closest("[data-close]");
      if (button) close(button.dataset.close === "cancel" ? null : button.dataset.close);
    });
    dialog.showModal();
    setup?.(dialog, close);
  });
}

/**
 * Apple 风格的警告框：问句标题、按钮写动作名、默认焦点在「取消」。
 * @returns {Promise<string|null>} "ok" / "upload" / null
 */
function confirmAlert({ head, message, action = "确定", danger = true, canUpload = false, list = "" }) {
  const icon = `<div class="alert-ico"><img src="icon.svg" alt=""></div>`;
  return sheet(`<div class="sheet-body">${icon}<h3>${esc(head)}</h3>
      ${list ? `<div class="alert-list">${esc(list)}</div>` : ""}<p>${esc(message)}</p></div>
    <div class="sheet-foot">
      ${canUpload ? '<button class="btn primary" data-close="upload">先上传到云端，再继续</button>' : ""}
      <button class="btn ${danger ? "danger-fill" : "primary"}" data-close="ok">${esc(action)}</button>
      <button class="btn" data-close="cancel" autofocus>取消</button></div>`, "alert");
}

/**
 * 输入框弹窗。
 * @param {object} options head / text / fields:[{name,label,value,placeholder,type}] / action
 * @returns {Promise<object|null>} 字段值
 */
function promptSheet({ head, text = "", fields, action = "好" }) {
  let values = null;
  return sheet(`<form method="dialog"><div class="sheet-body"><h3>${esc(head)}</h3>${text ? `<p>${esc(text)}</p>` : ""}
      ${fields.map((field, i) => `<div class="form-row"><label>${esc(field.label)}</label>
        <input class="field" name="${esc(field.name)}" type="${esc(field.type || "text")}" value="${esc(field.value || "")}" placeholder="${esc(field.placeholder || "")}" ${i === 0 ? "autofocus" : ""} spellcheck="false" autocomplete="off"></div>`).join("")}</div>
      <div class="sheet-foot"><button type="button" class="btn" data-close="cancel">取消</button>
        <button type="submit" class="btn primary">${esc(action)}</button></div></form>`, "", (dialog, close) => {
    const form = dialog.querySelector("form");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      values = Object.fromEntries(new FormData(form));
      close("ok");
    });
    dialog.querySelector("input")?.select();
  }).then((result) => (result === "ok" ? values : null));
}

/** 显示较长的结果文本。 */
function showDetails(head, text) {
  return sheet(`<div class="sheet-body"><h3>${esc(head)}</h3><div class="alert-list" style="max-height:50vh;font-size:12px;white-space:pre-wrap">${esc(text)}</div></div>
    <div class="sheet-foot"><button class="btn primary" data-close="ok" autofocus>好</button></div>`, "wide");
}

// ============================================================ 提示条和进度

/**
 * 底部提示条，最多叠 3 条。
 * @param {string} message 文本
 * @param {object} options type: ok/warn/err，action/onAction：按钮，duration：毫秒
 */
function toast(message, { type = "ok", action = "", onAction = null, duration = 0 } = {}) {
  const box = document.getElementById("toasts");
  const long = message.length > 90 || message.split("\n").length > 2;
  const shown = long ? message.split("\n")[0].slice(0, 80) + "…" : message;
  const element = document.createElement("div");
  element.className = "toast";
  element.setAttribute("role", type === "err" ? "alert" : "status");
  element.innerHTML = `<span class="t-ico ${type}">${I[type]}</span><span class="t-msg">${esc(shown)}</span>
    ${long ? '<button class="btn sm" data-t="details">详情</button>' : ""}
    ${action ? `<button class="btn sm" data-t="action">${esc(action)}</button>` : ""}
    <button class="btn sm t-close" data-t="close" aria-label="关闭">${I.x}</button>`;
  const remove = () => {
    if (!element.isConnected || element.classList.contains("out")) return;
    element.classList.add("out");
    setTimeout(() => element.remove(), 200);
  };
  element.addEventListener("click", (event) => {
    const which = event.target.closest("[data-t]")?.dataset.t;
    if (which === "details") showDetails("详细结果", message);
    if (which === "action") onAction?.();
    if (which) remove();
  });
  // 鼠标悬停时暂停倒计时
  let timer = 0;
  const start = () => (timer = setTimeout(remove, duration || (type === "err" ? 8000 : action ? 6000 : 3200)));
  element.addEventListener("mouseenter", () => clearTimeout(timer));
  element.addEventListener("mouseleave", start);
  start();
  box.appendChild(element);
  while (box.children.length > 3) box.firstElementChild.remove();
}

/** 显示后端结果：message 正常提示，warning 黄色提示。 */
function showResult(result, options = {}) {
  if (result?.message) toast(result.message, options);
  if (result?.warning) toast(result.warning, { type: "warn" });
}

/**
 * 执行长操作：顶部显示进度胶囊，不挡住界面；同一时间只允许一个。
 * @param {string} label 进度文本
 * @param {Function} fn 异步操作
 * @returns {Promise<boolean>} 成功并有结果返回 true
 */
async function task(label, fn) {
  if (ui.task) {
    toast(`正在${ui.task}，请稍等`, { type: "warn" });
    return;
  }
  ui.task = label;
  const hud = document.getElementById("hud");
  hud.innerHTML = `<div class="progress-hud" role="status"><span class="spin"></span>${esc(label)}…</div>`;
  try {
    const result = await fn();
    if (result) showResult(result);
    return Boolean(result);
  } catch (error) {
    toast(error.message, { type: "err" });
    return false;
  } finally {
    ui.task = "";
    hud.innerHTML = "";
    await refresh();
  }
}

// ============================================================ 条目操作

/** 接口路径前缀。 */
const base = (item) => `/api/${item.kind}`;

/**
 * 处理后端的 needConfirm：弹警告框，确认后带 force 重试，或先上传再重试。
 * @returns {Promise<object|null>} 最终结果，用户取消返回 null
 */
async function confirmed(path, body, result, { head, action }) {
  if (!result?.needConfirm) return result;
  const lines = result.needConfirm.split("\n");
  const choice = await confirmAlert({ head, message: lines.join("\n"), action, canUpload: result.canUpload });
  if (choice === "upload") {
    const prefix = path.startsWith("/api/plugin") ? "p" : path.startsWith("/api/mcp") ? "m" : "s";
    const item = items.get(`${prefix}:${body.id}`);
    if (item) showResult(await api(`${base(item)}/upload`, { id: item.id }));
    return api(path, body);
  }
  if (choice === "ok") return api(path, { ...body, force: true });
  return null;
}

/** 启用或关闭一个条目。先乐观更新开关，失败再回退。 */
async function toggleItem(item, on, { undoable = true } = {}) {
  if (ui.busyRows.has(item.key)) return;
  const path = `${base(item)}/${on ? "enable" : "disable"}`;
  ui.busyRows.add(item.key);
  item.enabled = on;
  renderContent();
  renderInspector();
  try {
    const first = await api(path, { id: item.id });
    const head = on ? "覆盖本机的改动？" : item.inCloud ? `关闭“${title(item)}”会丢掉本机改动` : `彻底删除“${title(item)}”？`;
    const result = await confirmed(path, { id: item.id }, first, { head, action: on ? "覆盖" : "删除" });
    if (!result) {
      item.enabled = !on;
      return;
    }
    // 云端有备份的才能撤销
    const canUndo = undoable && (on || item.inCloud);
    showResult(result, canUndo ? { action: "撤销", onAction: () => toggleItem(items.get(item.key) || item, !on, { undoable: false }) } : {});
  } catch (error) {
    item.enabled = !on;
    toast(error.message, { type: "err" });
  } finally {
    ui.busyRows.delete(item.key);
    await refresh();
  }
}

/** 上传一个条目（插件是记录到云端清单）。 */
async function uploadItem(item) {
  await task(item.kind === "plugin" ? `记录 ${item.id}` : `上传 ${title(item)}`, () => api(`${base(item)}/upload`, { id: item.id }));
}

/** 彻底删除一个条目，一定先确认。 */
async function deleteItem(item) {
  const path = `${base(item)}/delete`;
  try {
    const first = await api(path, { id: item.id });
    const result = await confirmed(path, { id: item.id }, first, { head: `彻底删除“${title(item)}”？`, action: "彻底删除" });
    if (!result) return;
    showResult(result);
    if (ui.inspect === item.key) closeInspector();
  } catch (error) {
    toast(error.message, { type: "err" });
  }
  await refresh();
}

/** 修改多个条目的分组。 */
async function moveToGroup(keys, groupId) {
  const list = keys.map((key) => items.get(key)).filter(Boolean);
  try {
    for (const item of list) await api("/api/item/edit", { kind: item.kind, id: item.id, fields: { group: groupId } });
    toast(`已把 ${list.length} 项移到「${groupName(groupId) || "未分组"}」`);
  } catch (error) {
    toast(error.message, { type: "err" });
  }
  await refresh();
}

/** 批量启用或关闭：先不带 force 试一遍，把需要确认的汇总成一个警告框。 */
async function batchToggle(on) {
  const list = [...ui.selected].map((key) => items.get(key)).filter((item) => item && item.enabled !== on);
  if (!list.length) return toast(on ? "选中的都已经启用了" : "选中的都已经关闭了");
  await task(on ? `启用 ${list.length} 项` : `关闭 ${list.length} 项`, async () => {
    const risky = [];
    let done = 0;
    for (const item of list) {
      const result = await api(`${base(item)}/${on ? "enable" : "disable"}`, { id: item.id });
      if (result.needConfirm) risky.push(item);
      else done++;
    }
    if (risky.length) {
      const choice = await confirmAlert({
        head: on ? `覆盖 ${risky.length} 项的本机改动？` : `${risky.length} 项云端没有备份或有改动`,
        list: risky.map((item) => item.id).join("\n"),
        message: on ? "这些位置的本机改动会被云端版本覆盖。" : "关闭会从电脑上彻底删除它们，无法恢复。",
        action: on ? "覆盖" : "仍然删除",
      });
      if (choice === "ok") {
        for (const item of risky) await api(`${base(item)}/${on ? "enable" : "disable"}`, { id: item.id, force: true });
        done += risky.length;
      }
    }
    ui.selected.clear();
    return { message: `已${on ? "启用" : "关闭"} ${done} 项` };
  });
}

// ============================================================ 全局操作

// 工具栏和菜单里的全局操作
const Actions = {
  sync: () => task("同步到所有 AI", () => api("/api/sync-all")),
  update: () => task("从云端更新", () => api("/api/update")),
  rescan: () => task("重新扫描", async () => {
    await api("/api/reload");
    return { message: "已重新扫描本机" };
  }),
  uploadAll: () => task("上传全部改动", () => api("/api/upload-all")),
  push: () => task("推送到云端", () => api("/api/push")),
  cleanDup: async () => {
    const choice = await confirmAlert({ head: "清理旧目录里的重复？", message: "只删除旧目录里和新目录内容一样的副本，有差异的会保留并提示。", action: "清理", danger: false });
    if (choice === "ok") await task("清理重复", () => api("/api/clean-duplicates"));
  },
  groupAdd: async () => {
    const values = await promptSheet({ head: "新建分组", text: "比如「生图」「Unity 开发」，skill 和插件都可以放进来。",
      fields: [{ name: "name", label: "名称", placeholder: "分组名" }], action: "新建" });
    if (values?.name?.trim()) {
      try {
        showResult(await api("/api/group/add", { name: values.name }));
      } catch (error) {
        toast(error.message, { type: "err" });
      }
      await refresh();
    }
  },
  market: () => openMarket(),
  connect: () => onboarding(1, true),
  palette: () => openPalette(),
  deleteDisabled: () => deleteDisabled(),
  appCheck: () => task("检查更新", () => api("/api/app/check")),
  exportAll: async () => {
    const values = await promptSheet({ head: "导出本机全部",
      text: "把所有 skill、插件和 MCP 清单、全局规则、密钥打成一个 zip，存到下载文件夹。密钥用这个密码加密，忘了就导不回来；其他内容是明文。",
      fields: [{ name: "password", label: "密钥加密密码（至少 8 位）", type: "password" }, { name: "again", label: "再输一次", type: "password" }],
      action: "导出" });
    if (!values) return;
    if (values.password !== values.again) return toast("两次密码不一样", { type: "err" });
    await task("导出本机全部", () => api("/api/export-all", { password: values.password }));
  },
  appUpdate: () => updateApp(),
};

/** 「需要处理」页：彻底删除所有关闭着的条目，先列出来确认一次。 */
async function deleteDisabled() {
  const list = [...items.values()].filter((item) => !item.enabled);
  if (!list.length) return toast("没有关闭着的条目");
  const kindName = { skill: "skill", plugin: "插件", mcp: "MCP" };
  const choice = await confirmAlert({
    head: `彻底删除 ${list.length} 个关闭着的条目？`,
    list: list.map((item) => `${kindName[item.kind]}  ${title(item)}`).join("\n"),
    message: "会从云端库和本机所有 AI 里删掉，无法恢复。",
    action: "全部删除",
  });
  if (choice !== "ok") return;
  await task(`删除 ${list.length} 项`, async () => {
    const failed = [];
    for (const item of list) {
      try {
        await api(`${base(item)}/delete`, { id: item.id, force: true });
      } catch (error) {
        failed.push(`${title(item)}：${error.message}`);
      }
      ui.selected.delete(item.key);
      if (ui.inspect === item.key) ui.inspect = "";
    }
    return { message: `已删除 ${list.length - failed.length} 项`, warning: failed.length ? `${failed.length} 项没删掉：\n${failed.join("\n")}` : "" };
  });
}

/** 后台检查发现新版本时提醒一次。 */
function noticeUpdate(update) {
  if (!update) return;
  const had = state.update?.behind;
  state.update = update;
  if (Boolean(update.behind) !== Boolean(had)) {
    renderRail();
    if (ui.view === "app") renderContent();
  }
  if (!update.behind || ui.updateNotified || document.hidden) return;
  ui.updateNotified = true;
  toast(`Skillset 有新版本（${update.behind} 个更新）`, { type: "warn", action: "更新", duration: 15000, onAction: updateApp });
}

/** 等一会儿。 */
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** 拉取新版本并重启服务，等服务回来后刷新页面。 */
async function updateApp() {
  const choice = await confirmAlert({ head: "更新 Skillset？", message: "从 GitHub 拉取新版本，然后自动重启，几秒钟就好。", action: "更新并重启", danger: false });
  if (choice !== "ok") return;
  if (ui.task) return toast(`正在${ui.task}，请稍等`, { type: "warn" });
  ui.task = "更新 Skillset";
  const hud = document.getElementById("hud");
  const show = (text) => (hud.innerHTML = `<div class="progress-hud" role="status"><span class="spin"></span>${esc(text)}</div>`);
  show("正在更新 Skillset…");
  try {
    const result = await api("/api/app/update");
    if (result.restarting) {
      show("已更新，正在重启…");
      await sleep(1500);
      for (let attempt = 0; attempt < 60; attempt++) {
        try {
          await api("/api/stamp");
          location.reload();
          return;
        } catch {
          await sleep(1000);
        }
      }
      toast("重启后没连上，请关掉窗口重新打开 Skillset", { type: "err" });
      return;
    }
    showResult(result);
  } catch (error) {
    toast(error.message, { type: "err" });
  } finally {
    ui.task = "";
    hud.innerHTML = "";
  }
  await refresh();
}

/** 分组：改名和备注。 */
async function editGroup(groupId) {
  const group = state.groups.find((g) => g.id === groupId);
  if (!group) return;
  const values = await promptSheet({ head: "编辑分组", fields: [{ name: "name", label: "名称", value: group.name },
    { name: "note", label: "备注", value: group.note, placeholder: "这个分组是做什么的" }], action: "保存" });
  if (!values) return;
  try {
    showResult(await api("/api/group/edit", { id: groupId, name: values.name, note: values.note }));
  } catch (error) {
    toast(error.message, { type: "err" });
  }
  await refresh();
}

/** 分组：删除（组里的条目变成未分组）。 */
async function deleteGroup(groupId) {
  const choice = await confirmAlert({ head: `删除分组“${groupName(groupId)}”？`, message: "组里的 skill 和插件不会被删除，只是变成未分组。", action: "删除分组" });
  if (choice !== "ok") return;
  try {
    showResult(await api("/api/group/delete", { id: groupId }));
    if (ui.view === `group:${groupId}`) go("all");
  } catch (error) {
    toast(error.message, { type: "err" });
  }
  await refresh();
}

/** 分组：整组启用或关闭。 */
async function toggleGroup(groupId, on) {
  await task(on ? "整组启用" : "整组关闭", async () => {
    const first = await api("/api/group/toggle", { id: groupId, enabled: on });
    return confirmed("/api/group/toggle", { id: groupId, enabled: on }, first, { head: "整组关闭会丢失一些内容", action: "仍然关闭" });
  });
}

/** 保存安装目标（AI 应用页的开关）。 */
async function saveTargets(changedId, on) {
  const ids = state.agents.filter((agent) => (agent.id === changedId ? on : agent.active)).map((agent) => agent.id);
  try {
    await api("/api/targets", { ids });
    toast(on ? "已加入安装目标，同步后生效" : "已移出安装目标", { action: "立即同步", onAction: Actions.sync });
  } catch (error) {
    toast(error.message, { type: "err" });
  }
  await refresh();
}

// ============================================================ 右键菜单

/**
 * 在指定位置弹出菜单。
 * @param {number} x 横坐标
 * @param {number} y 纵坐标
 * @param {Array} entries [{label, icon, run, danger, hint}] 或 "-" 分隔线，或 {heading}
 */
function showMenu(x, y, entries) {
  closeMenu();
  const menu = document.createElement("div");
  menu.className = "menu";
  menu.setAttribute("role", "menu");
  menu.innerHTML = entries.map((entry, i) => entry === "-" ? '<div class="menu-sep"></div>'
    : entry.heading ? `<div class="menu-label">${esc(entry.heading)}</div>`
    : `<div class="menu-item ${entry.danger ? "danger" : ""}" role="menuitem" data-i="${i}">${entry.icon || '<span style="width:14px"></span>'}${esc(entry.label)}${entry.hint ? `<span class="faint">${esc(entry.hint)}</span>` : ""}</div>`).join("");
  document.body.appendChild(menu);
  // 不超出窗口
  const rect = menu.getBoundingClientRect();
  menu.style.left = `${Math.min(x, innerWidth - rect.width - 8)}px`;
  menu.style.top = `${Math.min(y, innerHeight - rect.height - 8)}px`;
  menu.addEventListener("click", (event) => {
    const index = event.target.closest(".menu-item")?.dataset.i;
    if (index === undefined) return;
    closeMenu();
    entries[index].run();
  });
}

/** 关闭菜单。 */
function closeMenu() {
  document.querySelector(".menu")?.remove();
}

/** 在按钮下方弹出菜单。 */
function menuAt(button, entries) {
  const rect = button.getBoundingClientRect();
  showMenu(rect.left, rect.bottom + 4, entries);
}

/** 条目的菜单项。 */
function itemMenu(item) {
  const isSkill = item.kind === "skill";
  const canUpload = item.kind !== "plugin" ? (!item.inCloud || item.modified) : !item.inCloud;
  const groupEntries = [{ heading: "移到分组" }, ...state.groups.map((group) => ({
    label: group.name, icon: `<span class="dot" style="width:9px;height:9px;border-radius:50%;margin:0 2.5px;background:${groupColor(group.id)}"></span>`,
    hint: group.id === item.group ? "✓" : "", run: () => moveToGroup([item.key], group.id) })),
    { label: "未分组", hint: groupName(item.group) ? "" : "✓", run: () => moveToGroup([item.key], "") }];
  return [
    { label: item.enabled ? "关闭" : "启用", icon: I.power, hint: "空格", run: () => toggleItem(item, !item.enabled) },
    { label: "显示详情", icon: I.pencil, hint: "Enter", run: () => inspect(item.key) },
    ...(canUpload ? [{ label: item.kind === "plugin" ? "记录到云端" : "上传到云端", icon: I.cloudUp, run: () => uploadItem(item) }] : []),
    ...(isSkill && item.copies[0] ? [{ label: "打开文件夹", icon: I.folder, run: () => openPath(`${item.copies[0].dir}/${item.id}`) }] : []),
    ...(item.kind === "mcp" && item.copies[0] ? [{ label: "打开配置文件位置", icon: I.folder, run: () => openPath(item.copies[0].dir) }] : []),
    "-", ...groupEntries, "-",
    { label: "彻底删除…", icon: I.trash, danger: true, hint: "Delete", run: () => deleteItem(item) },
  ];
}

/** 分组的菜单项。 */
function groupMenu(groupId) {
  return [
    { label: "整组启用", icon: I.power, run: () => toggleGroup(groupId, true) },
    { label: "整组关闭", run: () => toggleGroup(groupId, false) },
    "-",
    { label: "改名和备注…", icon: I.pencil, run: () => editGroup(groupId) },
    { label: "删除分组…", icon: I.trash, danger: true, run: () => deleteGroup(groupId) },
  ];
}

/** 右上角「更多」菜单。 */
function appMenu() {
  return [
    { label: "命令面板", icon: I.search, hint: "Ctrl K", run: Actions.palette },
    "-",
    { label: "上传全部改动", icon: I.cloudUp, run: Actions.uploadAll },
    { label: "推送到云端", icon: I.cloud, run: Actions.push },
    { label: "清理旧目录重复…", icon: I.sync, run: Actions.cleanDup },
    "-",
    { label: "新建分组…", icon: I.plus, run: Actions.groupAdd },
    { label: "浏览插件市场", icon: I.bag, run: Actions.market },
  ];
}

/** 打开文件夹。 */
async function openPath(path) {
  try {
    await api("/api/open", { path });
  } catch (error) {
    toast(error.message, { type: "err" });
  }
}

// ============================================================ 命令面板

/** Ctrl+K 命令面板：搜索条目、分组和操作。 */
function openPalette() {
  if (document.querySelector("dialog.palette")) return;
  // 两个空间互不相干：在密钥空间只搜密钥
  const commands = space() === "keys" ? keyCommands() : [
    { section: "操作", label: "同步到所有 AI", icon: I.sync, run: Actions.sync },
    { section: "操作", label: "从云端更新", icon: I.cloudDown, run: Actions.update },
    { section: "操作", label: "上传全部改动", icon: I.cloudUp, run: Actions.uploadAll },
    { section: "操作", label: "推送到云端", icon: I.cloud, run: Actions.push },
    { section: "操作", label: "重新扫描本机", icon: I.rescan, run: Actions.rescan },
    { section: "操作", label: "新建分组", icon: I.plus, run: Actions.groupAdd },
    { section: "操作", label: "浏览插件市场", icon: I.bag, run: Actions.market },
    { section: "前往", label: "全部", icon: I.stack, run: () => go("all") },
    { section: "前往", label: "插件", icon: I.puzzle, run: () => go("plugins") },
    { section: "前往", label: "MCP 工具", icon: I.plug, run: () => go("mcps") },
    { section: "前往", label: "全局规则", icon: I.doc, run: () => switchSpace("rules") },
    { section: "前往", label: "需要处理", icon: I.alert, run: () => go("attention") },
    { section: "前往", label: "AI 应用", icon: I.sparkle, run: () => go("agents") },
    { section: "前往", label: "云端同步", icon: I.cloud, run: () => go("settings") },
    { section: "前往", label: "密钥", icon: I.key, run: () => switchSpace("keys") },
    { section: "前往", label: "软件设置", icon: I.gear, run: () => switchSpace("app") },
    { section: "操作", label: "导出本机全部（skill、插件、规则、密钥）", icon: I.cloudUp, run: Actions.exportAll },
    ...state.groups.map((group) => ({ section: "分组", label: group.name, icon: `<span class="dot" style="width:9px;height:9px;border-radius:50%;background:${groupColor(group.id)}"></span>`, run: () => go(`group:${group.id}`) })),
    ...[...items.values()].map((item) => ({ section: { skill: "Skill", plugin: "插件", mcp: "MCP" }[item.kind], label: title(item), hint: item.id,
      text: `${item.note} ${item.description}`, icon: avatarHtml(item).html,
      run: () => reveal(item.key) })),
  ];
  let list = [], active = 0;
  sheet(`<div class="palette-input">${I.search}<input placeholder="${space() === "keys" ? "搜索密钥或操作…" : "搜索 skill、分组或操作…"}" spellcheck="false" autofocus></div>
    <div class="palette-list" role="listbox"></div>
    <div class="palette-foot"><span><span class="kbd">↑↓</span> 选择</span><span><span class="kbd">Enter</span> 打开</span><span><span class="kbd">Esc</span> 关闭</span></div>`,
  "palette", (dialog, close) => {
    const input = dialog.querySelector("input");
    const box = dialog.querySelector(".palette-list");
    const draw = () => {
      const words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
      list = commands.filter((c) => words.every((w) => `${c.label} ${c.hint || ""} ${c.text || ""} ${c.section}`.toLowerCase().includes(w)));
      if (!words.length) list = list.filter((c) => !["Skill", "插件", "MCP"].includes(c.section));
      list = list.slice(0, 60);
      active = Math.min(active, Math.max(list.length - 1, 0));
      let last = "";
      box.innerHTML = list.map((c, i) => {
        const head = c.section !== last ? `<div class="palette-sec">${esc((last = c.section))}</div>` : "";
        return `${head}<div class="palette-item ${i === active ? "on" : ""}" data-i="${i}" role="option"><span class="p-ico">${c.icon}</span>
          <span class="grow">${esc(c.label)}</span>${c.hint ? `<span class="faint mono">${esc(c.hint)}</span>` : ""}</div>`;
      }).join("") || '<div class="empty-row">没有匹配的结果</div>';
      box.querySelector(".on")?.scrollIntoView({ block: "nearest" });
    };
    const runAt = (i) => {
      const command = list[i];
      if (!command) return;
      close();
      setTimeout(command.run, ReducedMotion ? 0 : 160);
    };
    input.addEventListener("input", () => {
      active = 0;
      draw();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        active = (active + (event.key === "ArrowDown" ? 1 : -1) + list.length) % Math.max(list.length, 1);
        draw();
      } else if (event.key === "Enter") {
        event.preventDefault();
        runAt(active);
      }
    });
    box.addEventListener("mousemove", (event) => {
      const i = event.target.closest(".palette-item")?.dataset.i;
      if (i !== undefined && Number(i) !== active) {
        active = Number(i);
        box.querySelectorAll(".palette-item").forEach((el) => el.classList.toggle("on", el.dataset.i === i));
      }
    });
    box.addEventListener("click", (event) => {
      const i = event.target.closest(".palette-item")?.dataset.i;
      if (i !== undefined) runAt(Number(i));
    });
    draw();
  });
}

/** 跳到某个条目：切到「全部」、展开它所在分组、打开详情并滚动到它。 */
function reveal(key) {
  const item = items.get(key);
  if (!item) return;
  ui.search = "";
  ui.filter = "all";
  if (item.kind === "plugin") ui.view = "plugins";
  else if (item.kind === "mcp") ui.view = "mcps";
  else if (ui.view !== "all") ui.view = "all";
  ui.collapsed.delete(item.group || "_none");
  inspect(key);
  document.querySelector(`.row[data-key="${CSS.escape(key)}"]`)?.scrollIntoView({ block: "center", behavior: ReducedMotion ? "auto" : "smooth" });
}

// ============================================================ 插件市场

/** 浏览已添加市场里的插件，可安装，也可添加新市场。 */
function openMarket() {
  sheet(`<div class="sheet-body"><h3>插件市场</h3><p>来自已添加的市场。安装后出现在「插件」里，记录到云端后换电脑可一键装回。</p>
      <div class="search" style="margin-bottom:10px">${I.search}<input class="field" id="marketSearch" placeholder="搜索插件" spellcheck="false"></div>
      <div class="card" id="marketList" style="max-height:44vh;overflow-y:auto;box-shadow:inset 0 0 0 .5px var(--separator)">
        <div class="empty-row"><span class="spin" style="display:inline-block;vertical-align:-3px;margin-right:6px"></span>正在读取市场…</div></div>
      <div class="form-row" style="margin-top:16px"><label>添加市场</label>
        <div style="display:flex;gap:8px"><input class="field" id="marketSource" placeholder="owner/repo、git 地址或本地路径" spellcheck="false">
        <button class="btn" id="marketAdd">添加</button></div></div></div>
    <div class="sheet-foot"><button class="btn primary" data-close="ok">完成</button></div>`, "wide", async (dialog) => {
    const box = dialog.querySelector("#marketList");
    const installed = new Set(state.plugins.items.filter((p) => p.installed).map((p) => p.id));
    let all = [];
    const draw = () => {
      const word = dialog.querySelector("#marketSearch").value.toLowerCase();
      const shown = all.filter((p) => `${p.pluginId} ${p.description}`.toLowerCase().includes(word));
      box.innerHTML = shown.map((p) => `<div class="list-row"><div class="app-ico" style="background:#7C7C86">${I.puzzle.replace("<svg", '<svg width="15" height="15"')}</div>
        <div class="lbl"><b>${esc(p.name || p.pluginId)}</b><span title="${esc(p.description)}">${esc(p.description || p.pluginId)}</span></div>
        ${installed.has(p.pluginId) ? '<span class="chip ok">已安装</span>' : `<button class="btn sm primary" data-install="${esc(p.pluginId)}">安装</button>`}</div>`).join("")
        || '<div class="empty-row">没有可安装的插件，先添加一个市场</div>';
    };
    const load = async () => {
      try {
        all = (await api("/api/plugin/available")).items;
        draw();
      } catch (error) {
        box.innerHTML = `<div class="empty-row">${esc(error.message)}</div>`;
      }
    };
    dialog.querySelector("#marketSearch").addEventListener("input", draw);
    box.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-install]");
      if (!button) return;
      button.disabled = true;
      button.innerHTML = '<span class="spin"></span>';
      try {
        showResult(await api("/api/plugin/enable", { id: button.dataset.install }));
        installed.add(button.dataset.install);
        draw();
        refresh();
      } catch (error) {
        toast(error.message, { type: "err" });
        draw();
      }
    });
    dialog.querySelector("#marketAdd").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const source = dialog.querySelector("#marketSource").value.trim();
      if (!source) return dialog.querySelector("#marketSource").focus();
      button.disabled = true;
      try {
        showResult(await api("/api/plugin/add-market", { source }));
        dialog.querySelector("#marketSource").value = "";
        await load();
      } catch (error) {
        toast(error.message, { type: "err" });
      }
      button.disabled = false;
    });
    load();
  });
}

// ============================================================ 首次引导

/**
 * 首次引导：欢迎 → 连接云端 → 选择 AI。设置页「连接 GitHub」也复用第 2 步。
 * @param {number} startStep 从第几步开始（0 起）
 * @param {boolean} cloudOnly 只做连接云端这一步
 */
function onboarding(startStep = 0, cloudOnly = false) {
  let step = startStep;
  sheet(`<div class="sheet-body onboard" id="obBody"></div><div class="sheet-foot" id="obFoot"></div>`, "", (dialog, close) => {
    const body = dialog.querySelector("#obBody");
    const foot = dialog.querySelector("#obFoot");
    const dots = () => cloudOnly ? "" : `<div class="steps">${[0, 1, 2].map((i) => `<i class="${i === step ? "on" : ""}"></i>`).join("")}</div>`;
    const finishCloud = async () => {
      await refresh();
      if (cloudOnly) close();
      else {
        step = 2;
        draw();
      }
    };

    // 执行一个按钮动作：按钮转圈，结果用提示条显示
    const busyRun = async (button, fn) => {
      const old = button.innerHTML;
      button.disabled = true;
      button.innerHTML = '<span class="spin"></span>';
      try {
        return await fn();
      } catch (error) {
        toast(error.message, { type: "err" });
        dialog.querySelector(".sheet-body").classList.add("shake");
        setTimeout(() => dialog.querySelector(".sheet-body")?.classList.remove("shake"), 400);
        return null;
      } finally {
        if (button.isConnected) {
          button.disabled = false;
          button.innerHTML = old;
        }
      }
    };

    const drawWelcome = () => {
      body.innerHTML = `<img class="hero-ico" src="icon.svg" alt=""><h3>欢迎使用 Skillset</h3>${dots()}
        <p>在一个地方管理所有 AI 的 skill 和插件：<br>改中文名、分组、一键开关，并装到 Claude Code、Codex、Gemini、Cursor 等所有 AI。<br>
        云端用你自己的 GitHub 私有仓库保存，换电脑一键装回。</p>`;
      foot.innerHTML = `<button class="btn lg primary" style="width:100%" data-next>开始</button>`;
      foot.querySelector("[data-next]").onclick = () => {
        step = 1;
        draw();
      };
    };

    const drawCloud = async () => {
      body.innerHTML = `<div class="step-pane"><h3 style="text-align:center">连接云端</h3>${dots()}
        <p style="text-align:center">skill 会备份到你 GitHub 账号下的私有仓库，别人看不到。</p>
        <div id="ghStatus" class="status-line"><span class="spin"></span>正在检查 git 和 GitHub 登录…</div><div id="choices"></div></div>`;
      foot.innerHTML = `<button class="btn ghost" data-close="cancel">${cloudOnly ? "取消" : "先跳过，只在本机用"}</button>`;
      if (!cloudOnly) {
        foot.querySelector("button").removeAttribute("data-close");
        foot.querySelector("button").onclick = async () => {
          await api("/api/onboarding/skip").catch(() => null);
          step = 2;
          draw();
        };
      }
      let gh;
      try {
        gh = await api("/api/github/status");
      } catch (error) {
        gh = { git: false, gh: false };
      }
      if (!body.querySelector("#choices")) return;
      const status = body.querySelector("#ghStatus");
      status.innerHTML = !gh.git ? '<span class="no-dot"></span>没找到 git，请先安装 Git（git-scm.com）'
        : gh.ghUser ? `<span class="ok-dot"></span>已登录 GitHub：<b>${esc(gh.ghUser)}</b>`
        : gh.gh ? '<span class="no-dot"></span>GitHub CLI 已安装，还没登录' : '<span class="no-dot"></span>没装 GitHub CLI，可以直接粘贴仓库地址';
      const choice = (id, color, icon, head, text, recommended) => `<button class="choice ${recommended ? "recommended" : ""}" data-choice="${id}">
        <span class="c-ico" style="background:${color}">${icon}</span><span><b>${esc(head)}</b><span>${esc(text)}</span></span></button>`;
      const box = body.querySelector("#choices");
      box.innerHTML = [
        gh.ghUser ? choice("create", "var(--blue)", I.plus, "新建私有仓库", `在 ${gh.ghUser} 名下创建 my-skills 并上传`, true)
          : gh.gh ? choice("login", "#24292F", I.key, "登录 GitHub", "在浏览器里授权，完成后回来", true) : "",
        choice("url", "var(--indigo)", I.link, "使用已有仓库", "换新电脑时填之前的仓库地址，自动下载", !gh.gh),
      ].join("") + '<div id="choiceForm"></div>';

      box.addEventListener("click", async (event) => {
        const which = event.target.closest("[data-choice]")?.dataset.choice;
        const form = box.querySelector("#choiceForm");
        if (which === "login") {
          await busyRun(event.target.closest("button"), () => api("/api/github/login"));
          form.innerHTML = `<div class="step-pane"><p>浏览器里授权完成后点下面的按钮。</p><button class="btn primary" data-recheck style="width:100%">我已登录，重新检查</button></div>`;
          form.querySelector("[data-recheck]").onclick = drawCloud;
        } else if (which === "create" || which === "url") {
          const create = which === "create";
          form.innerHTML = `<form class="step-pane" style="display:flex;gap:8px;margin-top:4px">
            <input class="field" name="v" value="${create ? "my-skills" : ""}" placeholder="${create ? "仓库名" : "https://github.com/你/my-skills.git"}" spellcheck="false" required>
            <button class="btn primary">${create ? "创建" : "连接"}</button></form>`;
          const input = form.querySelector("input");
          input.focus();
          input.select();
          form.querySelector("form").addEventListener("submit", async (submit) => {
            submit.preventDefault();
            const result = await busyRun(form.querySelector("button"), () =>
              create ? api("/api/github/create", { name: input.value }) : api("/api/connect", { remoteUrl: input.value }));
            if (result) {
              showResult(result);
              finishCloud();
            }
          });
        }
      });
    };

    const drawAgents = () => {
      const list = state.agents.filter((agent) => agent.installed);
      body.innerHTML = `<div class="step-pane"><h3 style="text-align:center">装到哪些 AI？</h3>${dots()}
        <p style="text-align:center">检测到本机装了这些 AI。打开的会自动装上已启用的 skill。</p>
        <div class="card" style="box-shadow:inset 0 0 0 .5px var(--separator)">${list.map((agent) => `<div class="list-row">
          <div class="app-ico" style="background:${agentColor(agent.name)}">${esc(agent.name[0])}</div>
          <div class="lbl"><b>${esc(agent.name)}</b></div>
          <label class="switch small"><input type="checkbox" data-agent="${esc(agent.id)}" ${agent.active ? "checked" : ""}><span class="track"></span><span class="thumb"></span></label></div>`).join("")
          || '<div class="empty-row">没检测到 AI 应用，可以稍后在「AI 应用」里选择</div>'}</div></div>`;
      foot.innerHTML = `<button class="btn lg primary" style="width:100%" data-done>完成</button>`;
      foot.querySelector("[data-done]").onclick = async (event) => {
        const chosen = new Set([...body.querySelectorAll("[data-agent]:checked")].map((input) => input.dataset.agent));
        const ids = state.agents.filter((agent) => agent.installed ? chosen.has(agent.id) : agent.active).map((agent) => agent.id);
        await busyRun(event.currentTarget, () => api("/api/targets", { ids }));
        close();
        await refresh();
        toast("准备好了。点「同步到所有 AI」把 skill 装到选中的 AI。", { action: "立即同步", onAction: Actions.sync, duration: 10000 });
      };
    };

    const draw = () => [drawWelcome, drawCloud, drawAgents][step]();
    draw();
  }).then(() => refresh());
}

// ============================================================ 事件：点击

document.addEventListener("click", (event) => {
  const target = event.target;
  if (!target.closest(".menu")) closeMenu();

  // 空间切换、侧栏导航
  const spaceButton = target.closest("[data-space]");
  if (spaceButton) return switchSpace(spaceButton.dataset.space);
  const nav = target.closest("[data-view]");
  if (nav && !target.closest("[data-act]")) return go(nav.dataset.view);
  const keyItem = target.closest("[data-key-sel]");
  if (keyItem) return selectKey(keyItem.dataset.keySel);
  const keyFilter = target.closest("[data-key-filter]");
  if (keyFilter) {
    ui.keyFilter = keyFilter.dataset.keyFilter;
    return renderKeyNav();
  }
  if (target.closest("#cloudCard")) return space() === "keys" ? KeyActions["key-export"]() : go("settings");

  // 分段筛选
  const filter = target.closest("[data-filter]");
  if (filter) {
    ui.filter = filter.dataset.filter;
    renderToolbar();
    renderContent();
    return;
  }

  const actor = target.closest("[data-act]");
  const act = actor?.dataset.act;
  // 开关和勾选框由 change 事件处理
  if (act === "toggle" || act === "check" || act === "target" || act === "autopush" || act === "group-toggle" || target.closest(".switch")) return;
  const row = target.closest(".row[data-key]");
  const item = items.get(actor?.dataset.key || row?.dataset.key);

  switch (act) {
    case "row-menu": return menuAt(actor, itemMenu(item));
    case "group-menu": return menuAt(actor, groupMenu(actor.dataset.id));
    case "group-edit": return editGroup(actor.dataset.id);
    case "app-menu": return menuAt(actor, appMenu());
    case "group-add": return Actions.groupAdd();
    case "collapse": {
      const id = actor.dataset.id;
      ui.collapsed.has(id) ? ui.collapsed.delete(id) : ui.collapsed.add(id);
      localStorage.setItem("collapsed", JSON.stringify([...ui.collapsed]));
      actor.closest(".group").classList.toggle("collapsed");
      return;
    }
    case "inspect-close": closeInspector(); return;
    case "upload": return uploadItem(item);
    case "delete": return deleteItem(item);
    case "open": return openPath(actor.dataset.path);
    case "rules-save": return saveRules();
    case "rules-apply": return applyRules();
    case "rules-import": return importRules(actor.dataset.path);
    case "batch-on": return batchToggle(true);
    case "batch-off": return batchToggle(false);
    case "batch-upload": return task(`上传 ${ui.selected.size} 项`, async () => {
      for (const key of ui.selected) {
        const selectedItem = items.get(key);
        if (selectedItem) await api(`${base(selectedItem)}/upload`, { id: selectedItem.id });
      }
      ui.selected.clear();
      return { message: "已上传选中的条目" };
    });
    case "batch-group": return menuAt(actor, [{ heading: "移到分组" }, ...state.groups.map((group) => ({ label: group.name,
      run: () => moveToGroup([...ui.selected], group.id).then(() => { ui.selected.clear(); render(); }) })),
      { label: "未分组", run: () => moveToGroup([...ui.selected], "") }]);
    case "batch-clear": ui.selected.clear(); render(); return;
    case "key-menu": return menuAt(actor, [
      { label: "从 KeePassXC 导出的 CSV…", icon: I.doc, run: KeyActions["key-import-csv"] },
      { label: "从备份文件…", icon: I.cloudDown, run: KeyActions["key-import-backup"] }]);
    default:
      if (act && KeyActions[act]) return KeyActions[act](actor);
      if (act && Actions[act]) return Actions[act]();
  }

  // 点行：Ctrl/Shift 多选，否则打开详情
  if (row && item) {
    if (event.ctrlKey || event.metaKey) toggleSelect(item.key);
    else if (event.shiftKey && ui.focus) selectRange(ui.focus, item.key);
    else inspect(item.key);
  }
});

/** 勾选或取消一个条目。 */
function toggleSelect(key) {
  ui.selected.has(key) ? ui.selected.delete(key) : ui.selected.add(key);
  ui.focus = key;
  renderContent();
  renderBatch();
}

/** Shift 点击：选中两行之间的所有条目。 */
function selectRange(fromKey, toKey) {
  const keys = visibleKeys();
  const [a, b] = [keys.indexOf(fromKey), keys.indexOf(toKey)].sort((x, y) => x - y);
  if (a < 0) return toggleSelect(toKey);
  keys.slice(a, b + 1).forEach((key) => ui.selected.add(key));
  ui.focus = toKey;
  renderContent();
  renderBatch();
}

/** 当前屏幕上可见的条目 key（按显示顺序，不含折叠的分组）。 */
function visibleKeys() {
  return [...document.querySelectorAll(".group:not(.collapsed) .row[data-key]")].map((row) => row.dataset.key);
}

// ============================================================ 事件：开关、输入

document.addEventListener("change", (event) => {
  const target = event.target;
  const act = target.dataset.act;
  if (target.dataset.field) {
    if (target.tagName === "SELECT") saveField(target);
    return;
  }
  if (act === "toggle") {
    const item = items.get(target.dataset.key || target.closest(".row")?.dataset.key);
    if (item) toggleItem(item, target.checked);
  } else if (act === "check") {
    toggleSelect(target.closest(".row").dataset.key);
  } else if (act === "target") {
    saveTargets(target.dataset.id, target.checked);
  } else if (act === "group-toggle") {
    toggleGroup(target.dataset.id, target.checked);
  } else if (act === "autopush") {
    api("/api/settings", { remoteUrl: state.git.remote || "", autoPush: target.checked })
      .then(() => toast(target.checked ? "已开启自动推送" : "已关闭自动推送"), (error) => toast(error.message, { type: "err" }))
      .then(refresh);
  }
});

document.addEventListener("input", (event) => {
  if (event.target.id === "rulesText") {
    ui.rulesDraft = event.target.value;
    const dirty = ui.rulesDraft !== state.rules.text;
    document.querySelector('[data-act="rules-save"]')?.toggleAttribute("disabled", !dirty);
    document.querySelector('[data-act="rules-apply"]')?.toggleAttribute("disabled", dirty || !state.rules.inCloud);
    return;
  }
  if (event.target.id === "keySearch") {
    ui.keySearch = event.target.value;
    return renderKeyNav();
  }
  if (event.target.id !== "search") return;
  ui.search = event.target.value;
  // 搜索时不播放入场动画，保证输入跟手
  ui.renderedView = ui.view;
  renderContent();
  const sub = document.querySelector(".segmented");
  if (sub) {
    const base = viewItems(false, true);
    sub.querySelectorAll("button").forEach((button) => {
      button.querySelector(".n").textContent = base.filter(Filters[button.dataset.filter][1]).length;
    });
  }
});

// 中文名、介绍在失去焦点时保存（textarea 不会触发 change 之外的提交）
document.addEventListener("keydown", (event) => {
  if (event.target.dataset?.field === "nameZh" && event.key === "Enter") event.target.blur();
});

// ============================================================ 事件：右键

document.addEventListener("contextmenu", (event) => {
  const row = event.target.closest(".row[data-key]");
  const groupNav = event.target.closest("[data-group]");
  if (!row && !groupNav) return;
  event.preventDefault();
  if (row) {
    ui.focus = row.dataset.key;
    document.querySelectorAll(".row.focused").forEach((el) => el.classList.remove("focused"));
    row.classList.add("focused");
    showMenu(event.clientX, event.clientY, itemMenu(items.get(row.dataset.key)));
  } else {
    showMenu(event.clientX, event.clientY, groupMenu(groupNav.dataset.group));
  }
});

// ============================================================ 事件：拖到分组

document.addEventListener("dragstart", (event) => {
  const row = event.target.closest?.(".row[data-key]");
  if (!row) return;
  const key = row.dataset.key;
  const keys = ui.selected.has(key) ? [...ui.selected] : [key];
  event.dataTransfer.setData("text/skillset", JSON.stringify(keys));
  event.dataTransfer.effectAllowed = "move";
  // 多个时用一个计数胶囊做拖动图像
  const ghost = document.createElement("div");
  ghost.className = "drag-ghost";
  ghost.textContent = keys.length > 1 ? `${keys.length} 项` : title(items.get(key));
  document.body.appendChild(ghost);
  event.dataTransfer.setDragImage(ghost, 16, 14);
  setTimeout(() => ghost.remove(), 0);
  row.classList.add("dragging");
});

document.addEventListener("dragend", () => {
  document.querySelectorAll(".dragging, .drop-target").forEach((el) => el.classList.remove("dragging", "drop-target"));
});

document.addEventListener("dragover", (event) => {
  const target = event.target.closest?.("[data-drop]");
  if (!target || !event.dataTransfer.types.includes("text/skillset")) return;
  event.preventDefault();
  document.querySelectorAll(".drop-target").forEach((el) => el !== target && el.classList.remove("drop-target"));
  target.classList.add("drop-target");
});

document.addEventListener("dragleave", (event) => {
  const target = event.target.closest?.("[data-drop]");
  if (target && !target.contains(event.relatedTarget)) target.classList.remove("drop-target");
});

document.addEventListener("drop", (event) => {
  const target = event.target.closest?.("[data-drop]");
  const data = event.dataTransfer.getData("text/skillset");
  if (!target || !data) return;
  event.preventDefault();
  target.classList.remove("drop-target");
  ui.selected.clear();
  moveToGroup(JSON.parse(data), target.dataset.drop);
});

// ============================================================ 事件：键盘

document.addEventListener("keydown", (event) => {
  const ctrl = event.ctrlKey || event.metaKey;
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) && document.activeElement.type !== "checkbox";
  const inDialog = Boolean(document.querySelector("dialog[open]"));

  if (ctrl && event.key.toLowerCase() === "k") {
    event.preventDefault();
    return openPalette();
  }
  if (inDialog) return;
  if (event.key === "F5" || (ctrl && event.key.toLowerCase() === "r")) {
    event.preventDefault();
    return space() === "keys" ? loadKeys() : space() === "skills" ? Actions.rescan() : refresh();
  }
  if ((ctrl && event.key.toLowerCase() === "f") || (event.key === "/" && !typing)) {
    const search = document.getElementById(space() === "keys" ? "keySearch" : "search");
    if (search) {
      event.preventDefault();
      search.focus();
      search.select();
    }
    return;
  }
  if (event.key === "Escape") {
    if (document.querySelector(".menu")) return closeMenu();
    if (typing) {
      if (document.activeElement.id === "search" && ui.search) {
        document.activeElement.value = "";
        ui.search = "";
        renderContent();
      } else if (document.activeElement.id === "keySearch" && ui.keySearch) {
        document.activeElement.value = "";
        ui.keySearch = "";
        renderKeyNav();
      } else document.activeElement.blur();
      return;
    }
    if (ui.selected.size) {
      ui.selected.clear();
      return render();
    }
    if (ui.inspect) return closeInspector();
    return;
  }
  // 密钥空间只有上下切换选中的那条，技能的快捷键不在这里生效
  if (space() === "keys") {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (typing && document.activeElement.id !== "keySearch") return;
      event.preventDefault();
      moveKeySel(event.key === "ArrowDown" ? 1 : -1);
    }
    return;
  }
  if (space() === "app" || space() === "rules") return;
  if (typing) {
    // 搜索框里按 ↓ 进入列表
    if (document.activeElement.id === "search" && event.key === "ArrowDown") {
      event.preventDefault();
      document.activeElement.blur();
      moveFocus(1);
    }
    return;
  }
  if (ctrl && event.key.toLowerCase() === "a") {
    event.preventDefault();
    visibleKeys().forEach((key) => ui.selected.add(key));
    renderContent();
    return renderBatch();
  }
  const item = items.get(ui.focus);
  switch (event.key) {
    case "ArrowDown": event.preventDefault(); return moveFocus(1);
    case "ArrowUp": event.preventDefault(); return moveFocus(-1);
    case " ": if (item) { event.preventDefault(); toggleItem(item, !item.enabled); } return;
    case "Enter": if (item) inspect(item.key); return;
    case "Delete": case "Backspace": if (item) deleteItem(item); return;
  }
});

/** 键盘上下移动焦点；详情面板打开时跟着切换。 */
function moveFocus(step) {
  const keys = visibleKeys();
  if (!keys.length) return;
  const index = keys.indexOf(ui.focus);
  const next = keys[index < 0 ? (step > 0 ? 0 : keys.length - 1) : Math.max(0, Math.min(keys.length - 1, index + step))];
  ui.focus = next;
  document.querySelectorAll(".row.focused").forEach((el) => el.classList.remove("focused"));
  const row = document.querySelector(`.row[data-key="${CSS.escape(next)}"]`);
  row?.classList.add("focused");
  row?.scrollIntoView({ block: "nearest" });
  if (ui.inspect) {
    ui.inspect = next;
    renderInspector();
  }
}

// 侧栏条目支持键盘回车
document.getElementById("nav").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  if (event.target.dataset.view) go(event.target.dataset.view);
  if (event.target.dataset.keySel) selectKey(event.target.dataset.keySel);
});
document.getElementById("cloudCard").addEventListener("keydown", (event) => {
  if (event.key === "Enter") event.target.click();
});
// 滚动或窗口变化时关闭菜单
document.getElementById("content").addEventListener("scroll", closeMenu, { passive: true });
addEventListener("resize", () => {
  closeMenu();
  moveGlider();
});
// 详情的文本框失去焦点时保存
document.getElementById("inspector").addEventListener("focusout", (event) => {
  if (event.target.dataset.field && event.target.tagName !== "SELECT") saveField(event.target);
});

// ============================================================ 启动

// 切回窗口时马上检查一次，平时每 5 秒问一次
addEventListener("focus", watchChanges);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  watchChanges();
  showNotice();
});
setInterval(watchChanges, 5000);

renderSkeleton();
refresh().then(() => {
  if (state && !state.config.onboarded) onboarding(0);
});
