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
 * 渲染成卡片列表 + 自由打分（0-10）；提交 POST 回服务端 /api/v1/rating，
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
  /** 每日筛选 Top-K 的 K 值（digest 篇数）；服务端分区配置优先 */
  digestSize: number;
  /** 过了该小时且今日笔记不存在时自动生成（0-23） */
  autoRefreshHour: number;
  /** 当前分区 id（服务端 /api/v1/zones 的 id）；"default" 为默认区 */
  activeZone: string;
  /** 已知分区列表缓存（id -> 展示名），用于设置面板下拉与卡片徽标 */
  knownZones: Record<string, string>;
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

/** 服务端返回的最近一次评分（跨库/跨端同步用） */
interface RatedInfo {
  score: number | null;
  action: string;
  comment?: string | null;
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
  /** 文章所属分区 id（服务端返回；老数据可能缺失） */
  zone?: string;
  /** 当前用户最近一次评分；未评过则缺省 */
  rated?: RatedInfo | null;
}

interface Digest {
  date: string;
  generated?: string;
  /** 该 digest 所属分区 id 与展示名（服务端返回） */
  zone?: string;
  zoneDisplay?: string;
  items: DigestItem[];
}

/** 服务端 /api/v1/zones 返回的一个分区 */
interface ZoneInfo {
  id: string;
  display: string;
  feed_count: number;
  keyword_count: number;
  created_at: string;
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
  digestSize: 8,
  autoRefreshHour: 6,
  activeZone: "default",
  knownZones: {},
};

/** 非打分快捷动作：稍后读 / 跳过（打分改为自由输入 0-10） */
const RATED_ACTIONS: RatedAction[] = [
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
      (source, el, ctx) => {
        let digest: Digest;
        try {
          digest = JSON.parse(source);
        } catch {
          el.createEl("p", { text: "❌ digest 数据解析失败，请用「刷新今日智读」重试。" });
          return;
        }
        new DigestRenderer(this, digest, ctx.sourcePath).renderInto(el);
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

    // 拉取一次分区列表（best-effort：服务端未起时静默，用缓存/默认值继续）
    void this.refreshZones().catch(() => {
      /* 离线或未配置好时忽略，设置面板里可手动重试 */
    });

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

  /** 当前生效的分区 id（空则回退 default）。 */
  activeZone(): string {
    return this.settings.activeZone?.trim() || "default";
  }

  /** 分区 api 前缀：/api/v1/zones/{zone}/... */
  zonePath(suffix: string): string {
    const z = encodeURIComponent(this.activeZone());
    return `/api/v1/zones/${z}${suffix}`;
  }

  /**
   * 每日笔记路径。default 区沿用原来的 `{digestDir}/{date}.md`（不改老行为），
   * 其他分区落到 `{digestDir}/{zone}/{date}.md`，避免不同分区互相覆盖。
   */
  getTodayPath(): string {
    const zone = this.activeZone();
    const dir = zone === "default"
      ? this.settings.digestDir
      : `${this.settings.digestDir}/${zone}`;
    return `${dir}/${todayStr()}.md`;
  }

  /** 笔记目录（按分区），用于创建目录。 */
  getDigestDir(): string {
    const zone = this.activeZone();
    return zone === "default"
      ? this.settings.digestDir
      : `${this.settings.digestDir}/${zone}`;
  }

  /** 拉取服务端分区列表并缓存 id -> display。失败保持旧缓存，不阻塞主流程。 */
  async refreshZones(): Promise<ZoneInfo[]> {
    const res = await this.api("/api/v1/zones");
    if (res.status !== 200) throw new Error(`HTTP ${res.status}`);
    const zones = ((res.json as ZoneInfo[]) ?? []);
    const map: Record<string, string> = {};
    for (const z of zones) map[z.id] = z.display || z.id;
    this.settings.knownZones = map;
    if (!map[this.activeZone()] && zones.length > 0) {
      // 当前分区在服务端不存在（被删/改名）→ 回退 default 或第一个可用区
      this.settings.activeZone = map["default"] ? "default" : zones[0].id;
    }
    await this.saveAll();
    return zones;
  }

  async todayExists(): Promise<boolean> {
    return this.app.vault.adapter.exists(this.getTodayPath());
  }

  async fetchDigest(): Promise<Digest> {
    // 走分区端点：K 由服务端该分区的 digest_size 决定，插件的 digestSize 作为
    // 顶层覆盖（?top=）保留手动微调能力。
    const res = await this.api(
      this.zonePath(`/digest?top=${this.settings.digestSize}`)
    );
    if (res.status !== 200) throw new Error(`HTTP ${res.status}`);
    const digest = res.json as Digest;
    digest.zone = this.activeZone();
    digest.zoneDisplay =
      this.settings.knownZones[this.activeZone()] || this.activeZone();
    return digest;
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

    const zoneLabel = digest.zoneDisplay || this.activeZone();
    const content = [
      "---",
      `date: ${digest.date}`,
      `generated: ${digest.generated}`,
      `zone: ${digest.zone ?? this.activeZone()}`,
      "---",
      "",
      `# 📰 今日智读 · ${zoneLabel} · ${digest.date}`,
      "",
      `> 分区 ${zoneLabel} · 生成于 ${new Date(digest.generated).toTimeString().slice(0, 5)} · 对文章打分/跳过，反馈进入该分区的长期记忆，影响明天排序。`,
      "",
      "```fluxiars",
      JSON.stringify(digest),
      "```",
      "",
    ].join("\n");

    await this.app.vault.createFolder(this.getDigestDir()).catch(() => {
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
      const res = await this.api(this.zonePath("/collect"), {
        method: "POST",
        body: {},
        timeout: 120000,
      });
      const body = res.json as
        | { zone?: string; fetched?: number; new_added?: number }
        | null;
      new Notice(
        `[${body?.zone ?? this.activeZone()}] 采集完成：拉取 ${body?.fetched ?? "?"}，新增 ${body?.new_added ?? "?"}`
      );
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
    const res = await this.api(this.zonePath("/rating"), {
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
    const res = await this.api(this.zonePath("/rating"), {
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
  private rootEl: HTMLElement | null = null;

  constructor(
    private plugin: FluxiaRSSPlugin,
    private digest: Digest,
    /** 当前笔记路径：刷新后把最新 digest JSON 写回其 fluxiars 代码块 */
    private sourcePath: string
  ) {}

  renderInto(el: HTMLElement): void {
    el.addClass("fluxiars-digest");
    el.empty();
    this.rootEl = el;

    if (!this.digest.items || this.digest.items.length === 0) {
      el.createEl("p", {
        text: "今日暂无内容（服务端凌晨采集后刷新即可）。",
      });
      return;
    }

    this.mergeServerRatings();

    const header = el.createDiv({ cls: "fluxiars-header" });
    const gen = this.digest.generated
      ? new Date(this.digest.generated).toTimeString().slice(0, 5)
      : "—";
    const zoneId = this.digest.zone ?? this.plugin.activeZone();
    const zoneLabel =
      this.digest.zoneDisplay ?? this.plugin.settings.knownZones[zoneId] ?? zoneId;
    header.createEl("span", { cls: "fluxiars-zone-badge", text: `分区 · ${zoneLabel}` });
    header.createEl("span", {
      text: ` · 📰 ${this.digest.date} · ${this.digest.items.length} 篇 · 生成 ${gen}`,
    });

    // 显示刷新：重拉当日状态（rank/评分/评论），跨端手动同步入口
    const refreshBtn = header.createEl("button", {
      cls: "fluxiars-refresh",
      text: "🔄 刷新",
    });
    refreshBtn.addEventListener("click", () => void this.onRefresh(refreshBtn));

    for (const item of this.digest.items) {
      this.renderItem(el, item);
    }
  }

  /**
   * 手动刷新：从服务端重拉 digest（rank/reason/rated/comment 全量刷新），
   * 就地重渲染，并把最新 JSON 写回当前笔记的 fluxiars 代码块（只替换该块）。
   */
  private async onRefresh(btn: HTMLButtonElement): Promise<void> {
    btn.disabled = true;
    btn.setText("刷新中…");
    try {
      const fresh = await this.plugin.fetchDigest();
      fresh.generated = new Date().toISOString();
      this.digest = fresh;
      if (this.rootEl) this.renderInto(this.rootEl);
      await this.persistToNote();
      new Notice("已刷新状态 ✔");
    } catch (e) {
      new Notice(`刷新失败：${(e as Error).message}`);
    } finally {
      btn.disabled = false;
      btn.setText("🔄 刷新");
    }
  }

  /** 把当前 digest JSON 写回笔记的 fluxiars 代码块；不触碰其他内容。 */
  private async persistToNote(): Promise<void> {
    try {
      const file = this.plugin.app.vault.getAbstractFileByPath(this.sourcePath);
      if (!(file instanceof TFile)) return;
      const json = JSON.stringify(this.digest);
      await this.plugin.app.vault.process(file, (raw) => {
        const updated = raw.replace(
          /```fluxiars\n[\s\S]*?\n```/,
          () => `\`\`\`fluxiars\n${json}\n\`\`\``
        );
        return updated === raw ? raw : updated;
      });
    } catch (e) {
      new Notice(`状态落盘失败（不影响显示）：${(e as Error).message}`);
    }
  }

  /**
   * 把服务端返回的最近一次评分合并进本地 ratings：本库已评过则以本地为准
   * （更新鲜），未评过的标记为「已评」，实现跨库/跨端状态同步。
   */
  private mergeServerRatings(): void {
    for (const item of this.digest.items) {
      const rated = item.rated;
      if (!rated) continue;
      if (this.plugin.ratings[item.article_id]) continue; // 本地优先
      this.plugin.ratings[item.article_id] = {
        score: rated.score ?? null,
        action: rated.action,
        comment: rated.comment ?? undefined,
      };
    }
    void this.plugin.saveAll();
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

    // 自由打分：0-10 数字输入（Enter 或「打分」按钮提交）
    const scoreInput = row.createEl("input", {
      cls: "fluxiars-score-input",
      type: "number",
      placeholder: "0-10",
      attr: { min: "0", max: "10", step: "1" },
    });
    const scoreBtn = row.createEl("button", { cls: "fluxiars-btn", text: "✓ 打分" });
    const doScore = (): void => {
      const raw = scoreInput.value.trim();
      const v = Number(raw);
      if (raw === "" || !Number.isInteger(v) || v < 0 || v > 10) {
        new Notice("请输入 0-10 的整数分数");
        return;
      }
      void this.complete(scoreBtn, item, row, card, v, "read", "✓ 打分");
    };
    scoreInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") doScore();
    });
    scoreBtn.addEventListener("click", doScore);

    // 非打分快捷动作：稍后读 / 跳过
    for (const ra of RATED_ACTIONS) {
      const btn = row.createEl("button", { cls: "fluxiars-btn", text: ra.label });
      btn.addEventListener("click", () =>
        void this.complete(btn, item, row, card, null, ra.action, ra.label)
      );
    }
  }

  /** 提交评分/动作并刷新卡片状态；失败则恢复按钮，不静默。 */
  private async complete(
    btn: HTMLButtonElement,
    item: DigestItem,
    row: HTMLElement,
    card: HTMLElement,
    score: number | null,
    action: string,
    label: string
  ): Promise<void> {
    btn.disabled = true;
    btn.setText("…");
    try {
      await this.plugin.submitRating(item.article_id, score, action);
      row.empty();
      const ra: RatedAction = { score, action, label: "" };
      row.createEl("span", { cls: "fluxiars-rated", text: ratedText(ra) });
      this.renderCommentArea(card, item, this.plugin.ratings[item.article_id]);
      new Notice("已记录反馈 ✔");
    } catch (e) {
      btn.disabled = false;
      btn.setText(label);
      new Notice(`评分失败：${(e as Error).message}`);
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

    this.renderZoneSection(containerEl);

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
      .setName("每日篇数（Top-K）")
      .setDesc("每日智读精选的条数（拉取 digest 时传给服务端 ?top=）")
      .addSlider((s) =>
        s
          .setLimits(1, 30, 1)
          .setValue(this.plugin.settings.digestSize)
          .setDynamicTooltip()
          .onChange(async (v) => {
            this.plugin.settings.digestSize = v;
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

  // ---- 分区（服务端 /api/v1/zones） ----

  /**
   * 分区选择器：下拉列出服务端所有分区，「刷新分区列表」重新拉取。
   * 切换分区后，本次会话的 digest 路径、评分、源管理都会指向新分区。
   */
  private renderZoneSection(containerEl: HTMLElement): void {
    containerEl.createEl("h3", { text: "分区" });
    containerEl.createEl("p", {
      cls: "setting-item-description",
      text: "每个分区有独立的 RSS 源、每日篇数与长期记忆（Honcho 画像）。切换分区后，笔记目录与打分都会指向该分区。",
    });

    const setting = new Setting(containerEl)
      .setName("当前分区")
      .setDesc("选择要阅读的分区；服务端未启动时先用「刷新分区列表」拉取。");

    setting.addDropdown((dd) => {
      const zones = this.plugin.settings.knownZones;
      const ids = Object.keys(zones);
      if (ids.length === 0) {
        dd.addOption(this.plugin.activeZone(), this.plugin.activeZone());
      } else {
        for (const id of ids) dd.addOption(id, `${zones[id]}（${id}）`);
      }
      dd.setValue(this.plugin.activeZone());
      dd.onChange(async (v) => {
        this.plugin.settings.activeZone = v;
        await this.plugin.saveAll();
        this.display(); // 重渲染：源列表标题/内容切到新分区
        new Notice(`已切换到分区：${this.plugin.settings.knownZones[v] ?? v}`);
      });
    });

    new Setting(containerEl)
      .setName("刷新分区列表")
      .setDesc("从服务端 /api/v1/zones 重新拉取分区，更新上面的下拉选项。")
      .addButton((btn) =>
        btn.setButtonText("🔄 拉取分区").onClick(async () => {
          btn.setDisabled(true);
          btn.setButtonText("拉取中…");
          try {
            await this.plugin.refreshZones();
            this.display();
            new Notice("分区列表已更新 ✔");
          } catch (e) {
            new Notice(`拉取分区失败：${(e as Error).message}`);
          } finally {
            btn.setDisabled(false);
            btn.setButtonText("🔄 拉取分区");
          }
        })
      );
  }

  // ---- RSS 源管理（服务端 DB 持久化） ----

  private renderSourcesSection(containerEl: HTMLElement): void {
    const zoneLabel =
      this.plugin.settings.knownZones[this.plugin.activeZone()] ||
      this.plugin.activeZone();
    containerEl.createEl("h3", { text: `RSS 源（分区：${zoneLabel}）` });
    containerEl.createEl("p", {
      cls: "setting-item-description",
      text: "增删的是「当前分区」的 RSS 源；每个分区独立维护自己的源列表。改动后运行「立即采集并刷新」拉取新源。",
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
        const res = await this.plugin.api(this.plugin.zonePath("/sources"), {
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

  /** 拉取当前分区的源列表并渲染；失败时在列表位显示错误而非抛错。 */
  private async refreshSources(listEl: HTMLElement): Promise<void> {
    listEl.empty();
    listEl.createEl("p", { cls: "fluxiars-source-muted", text: "加载中…" });
    try {
      const res = await this.plugin.api(this.plugin.zonePath("/sources"));
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
              `${this.plugin.zonePath("/sources")}?url=${encodeURIComponent(s.url)}`,
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
