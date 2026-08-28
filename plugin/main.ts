import {
  App,
  Notice,
  Plugin,
  PluginSettingTab,
  RequestUrlParam,
  RequestUrlResponse,
  Setting,
  TFile,
  requestUrl,
} from "obsidian";

/**
 * fluxiaRSS 今日智读（Obsidian）
 *
 * 形态：每天在 vault 指定目录生成一篇 `YYYY-MM-DD.md`，内含一个
 * `fluxiars` 代码块（JSON 为 digest 数据）。阅读视图中由本插件把该代码块
 * 渲染成卡片列表 + 五档打分按钮；点击按钮 POST 回服务端 /api/v1/rating，
 * 反馈进入 SQLite + Honcho 记忆，反哺下一轮排序与采集筛选。
 *
 * 约束（README 对齐）：纯拉取 + 后台异步；请求超时 ~10s；失败结构化报错，
 * 不白屏不静默。
 */

interface FluxiaSettings {
  /** 服务端地址，如 http://192.168.1.5:8000 */
  apiBase: string;
  /** 服务端 API 令牌（FLUXIARSS_API_TOKEN，随 X-Fluxia-Token 头发送） */
  apiToken: string;
  /** 每日笔记目录（vault 内相对路径） */
  digestDir: string;
  /** 过了该小时且今日笔记不存在时自动生成（0-23） */
  autoRefreshHour: number;
}

interface RatedState {
  score: number | null;
  action: string;
  /** 可选评论（评分后补写，随 X-Fluxia-Token 一并提交） */
  comment?: string;
}

interface PluginData {
  settings: FluxiaSettings;
  /** article_id → 已评状态（用于渲染 ✓ 与跨笔记/重启保持） */
  ratings: Record<string, RatedState>;
}

interface DigestItem {
  article_id: string;
  rank: number;
  title: string;
  summary: string;
  url: string;
  reason: string;
  /** 来源（RSS 源名）；老数据可能缺失 */
  source?: string;
}

interface Digest {
  date: string;
  generated?: string;
  items: DigestItem[];
}

/** 服务端 /api/v1/sources 返回的一个 RSS 源 */
interface SourceInfo {
  name: string;
  url: string;
  topic: string;
  /** true=用户自定义，false=内置默认 */
  custom: boolean;
}

interface RatedAction {
  score: number | null;
  action: string;
  label: string;
}

const DEFAULT_SETTINGS: FluxiaSettings = {
  apiBase: "http://localhost:8000",
  apiToken: "",
  digestDir: "FluxiaRSS",
  autoRefreshHour: 6,
};

/** 五档快捷：👍高 / ⭐中 / 👎低 / 🕒稍后读 / ⏭跳过 */
const RATED_ACTIONS: RatedAction[] = [
  { score: 9, action: "read", label: "👍 高" },
  { score: 5, action: "read", label: "⭐ 中" },
  { score: 1, action: "read", label: "👎 低" },
  { score: null, action: "later", label: "🕒 稍后读" },
  { score: null, action: "skip", label: "⏭ 跳过" },
];

/** 带超时的 requestUrl：超时抛错；主请求迟到失败被吞掉，避免 unhandled rejection。 */
function fetchWithTimeout(
  opts: RequestUrlParam,
  timeoutMs: number
): Promise<RequestUrlResponse> {
  const main = requestUrl(opts);
  main.catch(() => {
    /* 超时后不再关心主请求结果 */
  });
  let timer: number | undefined;
  const to = new Promise<never>((_, reject) => {
    timer = window.setTimeout(
      () => reject(new Error(`请求超时（${timeoutMs / 1000}s）`)),
      timeoutMs
    );
  });
  return Promise.race([main, to]).finally(() => window.clearTimeout(timer));
}

function todayStr(): string {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

function ratedText(r: RatedAction): string {
  if (r.action === "later") return "✓ 已评（稍后读）";
  if (r.action === "skip") return "✓ 已评（跳过）";
  return `✓ 已评 ${r.score}/10`;
}

export default class FluxiaRSSPlugin extends Plugin {
  settings: FluxiaSettings;
  ratings: Record<string, RatedState> = {};

  async onload(): Promise<void> {
    await this.loadSettings();

    this.registerMarkdownCodeBlockProcessor(
      "fluxiars",
      (source, el) => {
        let digest: Digest;
        try {
          digest = JSON.parse(source);
        } catch {
          el.createEl("p", { text: "❌ digest 数据解析失败，请用「刷新今日智读」重试。" });
          return;
        }
        new DigestRenderer(this, digest).renderInto(el);
      }
    );

    this.addCommand({
      id: "open-today-digest",
      name: "打开今日智读",
      callback: () => this.openToday(false),
    });
    this.addCommand({
      id: "refresh-today-digest",
      name: "刷新今日智读",
      callback: () => this.openToday(true),
    });
    this.addCommand({
      id: "collect-and-refresh",
      name: "立即采集并刷新今日智读",
      callback: () => this.collectAndRefresh(),
    });

    this.addSettingTab(new FluxiaSettingTab(this.app, this));

    // 每小时检查一次：过了 autoRefreshHour 且今日笔记不存在 → 自动生成
    this.registerInterval(
      window.setInterval(() => this.maybeAutoRefresh(), 60 * 60 * 1000)
    );

    // 启动时：今日笔记缺失则生成（静默，失败仅 Notice）
    if (!(await this.todayExists())) {
      await this.generateTodayNote().catch((e) =>
        new Notice(`📰 fluxiaRSS 生成今日智读失败：${e.message}`)
      );
    }
  }

  // ---- 设置 ----

  async loadSettings(): Promise<void> {
    const data = (await this.loadData()) as Partial<PluginData> | null;
    this.settings = Object.assign({}, DEFAULT_SETTINGS, data?.settings ?? {});
    this.ratings = data?.ratings ?? {};
  }

  async saveAll(): Promise<void> {
    await this.saveData({ settings: this.settings, ratings: this.ratings });
  }

  /** 带鉴权头的 API 调用：拼 base、附 X-Fluxia-Token（未配置 token 则不附带）。 */
  async api(
    urlPath: string,
    opts: { method?: string; body?: unknown; timeout?: number } = {}
  ): Promise<RequestUrlResponse> {
    return fetchWithTimeout(
      {
        url: `${this.settings.apiBase}${urlPath}`,
        method: opts.method ?? "GET",
        contentType: "application/json",
        headers: this.settings.apiToken
          ? { "X-Fluxia-Token": this.settings.apiToken }
          : undefined,
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      },
      opts.timeout ?? 10000
    );
  }

  // ---- 拉取与笔记 ----

  getTodayPath(): string {
    return `${this.settings.digestDir}/${todayStr()}.md`;
  }

  async todayExists(): Promise<boolean> {
    return this.app.vault.adapter.exists(this.getTodayPath());
  }

  async fetchDigest(): Promise<Digest> {
    const res = await this.api("/api/v1/digest");
    if (res.status !== 200) throw new Error(`HTTP ${res.status}`);
    return res.json as Digest;
  }

  /**
   * 生成今日笔记。force=true 时覆盖重写（刷新）；否则已存在则跳过。
   * 返回文件（可能为 null：已存在且未强制刷新）。
   */
  async generateTodayNote(force = false): Promise<TFile | null> {
    const path = this.getTodayPath();
    if (!force && (await this.app.vault.adapter.exists(path))) return null;

    const digest = await this.fetchDigest();
    digest.generated = new Date().toISOString();

    const content = [
      "---",
      `date: ${digest.date}`,
      `generated: ${digest.generated}`,
      "---",
      "",
      `# 📰 今日智读 · ${digest.date}`,
      "",
      `> 生成于 ${new Date(digest.generated).toTimeString().slice(0, 5)} · 对文章打分/跳过，反馈进入长期记忆，影响明天排序。`,
      "",
      "```fluxiars",
      JSON.stringify(digest),
      "```",
      "",
    ].join("\n");

    await this.app.vault.createFolder(this.settings.digestDir).catch(() => {
      /* 目录已存在时忽略 */
    });
    await this.app.vault.adapter.write(path, content);
    const f = this.app.vault.getAbstractFileByPath(path);
    return f instanceof TFile ? f : null;
  }

  async openToday(refresh: boolean): Promise<void> {
    try {
      const file = await this.generateTodayNote(refresh);
      const target = file ?? this.app.vault.getAbstractFileByPath(this.getTodayPath());
      if (target instanceof TFile) {
        const leaf = this.app.workspace.getLeaf(false);
        await leaf.openFile(target);
      } else {
        new Notice("今日智读笔记不存在，拉取失败");
      }
    } catch (e) {
      new Notice(`📰 fluxiaRSS 拉取失败：${(e as Error).message}`);
    }
  }

  async maybeAutoRefresh(): Promise<void> {
    const hour = new Date().getHours();
    if (hour < this.settings.autoRefreshHour) return;
    if (await this.todayExists()) return; // 已有今日笔记不覆盖，避免破坏用户标注
    try {
      const file = await this.generateTodayNote();
      if (file) new Notice("📰 今日智读已自动生成");
    } catch (e) {
      new Notice(`📰 fluxiaRSS 自动生成失败：${(e as Error).message}`);
    }
  }

  async collectAndRefresh(): Promise<void> {
    try {
      const res = await this.api("/api/v1/collect", {
        method: "POST",
        body: {},
        timeout: 120000,
      });
      const body = res.json as { fetched?: number; new_added?: number } | null;
      new Notice(`采集完成：拉取 ${body?.fetched ?? "?"}，新增 ${body?.new_added ?? "?"}`);
    } catch (e) {
      new Notice(`采集失败：${(e as Error).message}`);
    }
    await this.openToday(true);
  }

  // ---- 评分 ----

  async submitRating(
    articleId: string,
    score: number | null,
    action: string
  ): Promise<void> {
    const res = await this.api("/api/v1/rating", {
      method: "POST",
      body: { article_id: articleId, score, action },
    });
    if (res.status !== 200 && res.status !== 201) {
      throw new Error(`HTTP ${res.status}`);
    }
    this.ratings[articleId] = { score, action };
    await this.saveAll();
  }

  /** 给已评分文章补写评论（action=comment，服务端更新最近一条评分行的 comment）。 */
  async submitComment(articleId: string, comment: string): Promise<void> {
    const res = await this.api("/api/v1/rating", {
      method: "POST",
      body: { article_id: articleId, comment, action: "comment" },
    });
    if (res.status !== 200 && res.status !== 201) {
      throw new Error(`HTTP ${res.status}`);
    }
    const prev = this.ratings[articleId] ?? { score: null, action: "read" };
    this.ratings[articleId] = { ...prev, comment };
    await this.saveAll();
  }
}

// ---- 渲染 ----

class DigestRenderer {
  constructor(private plugin: FluxiaRSSPlugin, private digest: Digest) {}

  renderInto(el: HTMLElement): void {
    el.addClass("fluxiars-digest");
    el.empty();

    if (!this.digest.items || this.digest.items.length === 0) {
      el.createEl("p", {
        text: "今日暂无内容（服务端凌晨采集后刷新即可）。",
      });
      return;
    }

    const header = el.createDiv({ cls: "fluxiars-header" });
    const gen = this.digest.generated
      ? new Date(this.digest.generated).toTimeString().slice(0, 5)
      : "—";
    header.createEl("span", {
      text: `📰 ${this.digest.date} · ${this.digest.items.length} 篇 · 生成 ${gen}`,
    });

    for (const item of this.digest.items) {
      this.renderItem(el, item);
    }
  }

  private renderItem(el: HTMLElement, item: DigestItem): void {
    const card = el.createDiv({ cls: "fluxiars-card" });

    const top = card.createDiv({ cls: "fluxiars-card-top" });
    top.createEl("span", { cls: "fluxiars-rank", text: `${item.rank}` });
    top.createEl("a", { cls: "fluxiars-title", text: item.title, href: item.url })
      .addEventListener("click", (ev) => {
        ev.preventDefault();
        window.open(item.url, "_blank");
      });

    if (item.source) {
      card.createEl("div", { cls: "fluxiars-source", text: `来源：${item.source}` });
    }
    if (item.reason) {
      card.createEl("div", { cls: "fluxiars-reason", text: `排序：${item.reason}` });
    }
    if (item.summary) {
      card.createEl("div", { cls: "fluxiars-summary", text: item.summary });
    }

    const row = card.createDiv({ cls: "fluxiars-actions" });
    this.renderActions(row, card, item);
  }

  private renderActions(row: HTMLElement, card: HTMLElement, item: DigestItem): void {
    const rated = this.plugin.ratings[item.article_id];
    if (rated) {
      const r: RatedAction = { score: rated.score, action: rated.action, label: "" };
      row.createEl("span", { cls: "fluxiars-rated", text: ratedText(r) });
      this.renderCommentArea(card, item, rated);
      return;
    }

    for (const ra of RATED_ACTIONS) {
      const btn = row.createEl("button", { cls: "fluxiars-btn", text: ra.label });
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        btn.setText("…");
        try {
          await this.plugin.submitRating(item.article_id, ra.score, ra.action);
          row.empty();
          row.createEl("span", { cls: "fluxiars-rated", text: ratedText(ra) });
          this.renderCommentArea(card, item, this.plugin.ratings[item.article_id]);
          new Notice("已记录反馈 ✔");
        } catch (e) {
          btn.disabled = false;
          btn.setText(ra.label);
          new Notice(`评分失败：${(e as Error).message}`);
        }
      });
    }
  }

  /** 卡片评论区：已有评论则显示，否则放一个可选的评论输入框（Enter 提交）。 */
  private renderCommentArea(
    card: HTMLElement,
    item: DigestItem,
    rated: RatedState | undefined
  ): void {
    card.querySelector(".fluxiars-comment")?.remove();
    if (!rated) return;

    const area = card.createDiv({ cls: "fluxiars-comment" });
    if (rated.comment) {
      area.createEl("span", { cls: "fluxiars-comment-text", text: `💬 ${rated.comment}` });
      return;
    }

    const input = area.createEl("input", {
      cls: "fluxiars-comment-input",
      type: "text",
      placeholder: "✍️ 想记一句？（Enter 提交，可跳过）",
    });
    input.addEventListener("keydown", async (ev) => {
      if (ev.key !== "Enter") return;
      const text = input.value.trim();
      if (!text) return;
      input.disabled = true;
      try {
        await this.plugin.submitComment(item.article_id, text);
        area.empty();
        area.createEl("span", { cls: "fluxiars-comment-text", text: `💬 ${text}` });
        new Notice("评论已记录 ✔");
      } catch (e) {
        input.disabled = false;
        new Notice(`评论失败：${(e as Error).message}`);
      }
    });
  }
}

// ---- 设置面板 ----

class FluxiaSettingTab extends PluginSettingTab {
  constructor(app: App, private plugin: FluxiaRSSPlugin) {
    super(app, plugin);
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "fluxiaRSS 今日智读" });

    new Setting(containerEl)
      .setName("API 地址")
      .setDesc("fluxiaRSS 服务端地址，如 http://192.168.1.5:8000")
      .addText((t) =>
        t
          .setValue(this.plugin.settings.apiBase)
          .onChange(async (v) => {
            this.plugin.settings.apiBase = v.trim() || DEFAULT_SETTINGS.apiBase;
            await this.plugin.saveAll();
          })
      );

    new Setting(containerEl)
      .setName("API 令牌")
      .setDesc("服务端 .env 里的 FLUXIARSS_API_TOKEN（留空则不做鉴权，仅限本机调试）")
      .addText((t) => {
        t.setValue(this.plugin.settings.apiToken).onChange(async (v) => {
          this.plugin.settings.apiToken = v.trim();
          await this.plugin.saveAll();
        });
        t.inputEl.type = "password";
        t.inputEl.placeholder = "FLUXIARSS_API_TOKEN 的值";
      });

    new Setting(containerEl)
      .setName("digest 目录")
      .setDesc("每日笔记存放目录（vault 内相对路径）")
      .addText((t) =>
        t
          .setValue(this.plugin.settings.digestDir)
          .onChange(async (v) => {
            this.plugin.settings.digestDir = v.trim() || DEFAULT_SETTINGS.digestDir;
            await this.plugin.saveAll();
          })
      );

    new Setting(containerEl)
      .setName("自动生成时间")
      .setDesc("过了该小时且今日笔记不存在时自动生成（0-23）")
      .addSlider((s) =>
        s
          .setLimits(0, 23, 1)
          .setValue(this.plugin.settings.autoRefreshHour)
          .setDynamicTooltip()
          .onChange(async (v) => {
            this.plugin.settings.autoRefreshHour = v;
            await this.plugin.saveAll();
          })
      );

    this.renderSourcesSection(containerEl);
  }

  // ---- RSS 源管理（服务端 DB 持久化） ----

  private renderSourcesSection(containerEl: HTMLElement): void {
    containerEl.createEl("h3", { text: "RSS 源" });
    containerEl.createEl("p", {
      cls: "setting-item-description",
      text: "增删自定义 RSS 源（内置源也列于此）。改动后运行「立即采集并刷新」拉取新源。",
    });

    const bar = containerEl.createDiv({ cls: "fluxiars-source-bar" });
    bar.createEl("button", { text: "🔄 刷新列表", cls: "fluxiars-source-refresh" })
      .addEventListener("click", () => this.refreshSources(listEl));

    const listEl = containerEl.createDiv({ cls: "fluxiars-source-list" });
    listEl.createEl("p", { cls: "fluxiars-source-muted", text: "加载中…" });

    const nameInput = containerEl.createEl("input", {
      type: "text",
      placeholder: "名称（可选，默认用域名）",
      cls: "fluxiars-source-input",
    });
    const urlInput = containerEl.createEl("input", {
      type: "text",
      placeholder: "RSS URL（必填，如 https://example.com/feed.xml）",
      cls: "fluxiars-source-input",
    });
    const addBtn = containerEl.createEl("button", {
      text: "＋ 添加源",
      cls: "fluxiars-source-addbtn",
    });

    urlInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") addBtn.click();
    });
    addBtn.addEventListener("click", async () => {
      const url = urlInput.value.trim();
      if (!url) {
        new Notice("请先填写 RSS URL");
        return;
      }
      addBtn.disabled = true;
      addBtn.setText("添加中…");
      try {
        const res = await this.plugin.api("/api/v1/sources", {
          method: "POST",
          body: { url, name: nameInput.value.trim() || undefined },
        });
        if (res.status !== 200 && res.status !== 201) {
          throw new Error(`HTTP ${res.status}`);
        }
        nameInput.value = "";
        urlInput.value = "";
        await this.refreshSources(listEl);
        new Notice("源已添加 ✔");
      } catch (e) {
        new Notice(`添加失败：${(e as Error).message}`);
      } finally {
        addBtn.disabled = false;
        addBtn.setText("＋ 添加源");
      }
    });

    void this.refreshSources(listEl);
  }

  /** 拉取 /api/v1/sources 并渲染列表；失败时在列表位显示错误而非抛错。 */
  private async refreshSources(listEl: HTMLElement): Promise<void> {
    listEl.empty();
    listEl.createEl("p", { cls: "fluxiars-source-muted", text: "加载中…" });
    try {
      const res = await this.plugin.api("/api/v1/sources");
      if (res.status !== 200) throw new Error(`HTTP ${res.status}`);
      const sources = ((res.json as SourceInfo[]) ?? []);
      listEl.empty();
      if (sources.length === 0) {
        listEl.createEl("p", { cls: "fluxiars-source-muted", text: "（暂无源，请添加）" });
        return;
      }
      for (const s of sources) {
        const row = listEl.createDiv({ cls: "fluxiars-source-row" });
        const info = row.createDiv({ cls: "fluxiars-source-info" });
        info.createEl("span", { cls: "fluxiars-source-name", text: s.name });
        info.createEl("span", { cls: "fluxiars-source-url", text: s.url });
        info.createEl("span", {
          cls: s.custom
            ? "fluxiars-source-badge fluxiars-source-badge-custom"
            : "fluxiars-source-badge",
          text: s.custom ? "自定义" : "内置",
        });
        const del = row.createEl("button", { cls: "fluxiars-source-del", text: "删除" });
        del.addEventListener("click", async () => {
          del.disabled = true;
          del.setText("…");
          try {
            const r = await this.plugin.api(
              `/api/v1/sources?url=${encodeURIComponent(s.url)}`,
              { method: "DELETE" }
            );
            if (r.status !== 200) throw new Error(`HTTP ${r.status}`);
            await this.refreshSources(listEl);
            new Notice("源已删除 ✔");
          } catch (e) {
            del.disabled = false;
            del.setText("删除");
            new Notice(`删除失败：${(e as Error).message}`);
          }
        });
      }
    } catch (e) {
      listEl.empty();
      listEl.createEl("p", {
        cls: "fluxiars-source-muted",
        text: `❌ 获取源列表失败：${(e as Error).message}`,
      });
    }
  }
}
