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
  /** 每日笔记目录（vault 内相对路径） */
  digestDir: string;
  /** 过了该小时且今日笔记不存在时自动生成（0-23） */
  autoRefreshHour: number;
}

interface PluginData {
  settings: FluxiaSettings;
  /** article_id → 已评状态（用于渲染 ✓ 与跨笔记/重启保持） */
  ratings: Record<string, { score: number | null; action: string }>;
}

interface DigestItem {
  article_id: string;
  rank: number;
  title: string;
  summary: string;
  url: string;
  reason: string;
}

interface Digest {
  date: string;
  generated?: string;
  items: DigestItem[];
}

interface RatedAction {
  score: number | null;
  action: string;
  label: string;
}

const DEFAULT_SETTINGS: FluxiaSettings = {
  apiBase: "http://localhost:8000",
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
  ratings: Record<string, { score: number | null; action: string }> = {};

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

  // ---- 拉取与笔记 ----

  getTodayPath(): string {
    return `${this.settings.digestDir}/${todayStr()}.md`;
  }

  async todayExists(): Promise<boolean> {
    return this.app.vault.adapter.exists(this.getTodayPath());
  }

  async fetchDigest(): Promise<Digest> {
    const res = await fetchWithTimeout(
      {
        url: `${this.settings.apiBase}/api/v1/digest`,
        method: "GET",
      },
      10000
    );
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
      const res = await fetchWithTimeout(
        {
          url: `${this.settings.apiBase}/api/v1/collect`,
          method: "POST",
          contentType: "application/json",
          body: JSON.stringify({}),
        },
        120000
      );
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
    const res = await fetchWithTimeout(
      {
        url: `${this.settings.apiBase}/api/v1/rating`,
        method: "POST",
        contentType: "application/json",
        body: JSON.stringify({ article_id: articleId, score, action }),
      },
      10000
    );
    if (res.status !== 200 && res.status !== 201) {
      throw new Error(`HTTP ${res.status}`);
    }
    this.ratings[articleId] = { score, action };
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

    if (item.reason) {
      card.createEl("div", { cls: "fluxiars-reason", text: `排序：${item.reason}` });
    }
    if (item.summary) {
      card.createEl("div", { cls: "fluxiars-summary", text: item.summary });
    }

    const row = card.createDiv({ cls: "fluxiars-actions" });
    this.renderActions(row, item);
  }

  private renderActions(row: HTMLElement, item: DigestItem): void {
    const rated = this.plugin.ratings[item.article_id];
    if (rated) {
      const r: RatedAction = { score: rated.score, action: rated.action, label: "" };
      row.createEl("span", { cls: "fluxiars-rated", text: ratedText(r) });
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
          new Notice("已记录反馈 ✔");
        } catch (e) {
          btn.disabled = false;
          btn.setText(ra.label);
          new Notice(`评分失败：${(e as Error).message}`);
        }
      });
    }
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
  }
}
