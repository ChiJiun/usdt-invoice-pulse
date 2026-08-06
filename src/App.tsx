import { useEffect, useMemo, useState } from "react";
import type {
  DailyExchangeStatus,
  DashboardData,
  ExecutionType,
  ExchangeStatus,
  InvoiceRecord,
  InvoiceStatus,
  RunEvent,
  RunStatus,
  TradeSide,
} from "./types";

const SUPPORTED_EXCHANGE_IDS = new Set(["bitopro", "max"]);
const DEPLOYMENT_GUIDE_URL =
  "https://github.com/ChiJiun/usdt-invoice-pulse#github-actions-%E8%88%87-pages-%E5%AE%8C%E6%95%B4%E9%83%A8%E7%BD%B2";

const fallbackData: DashboardData = {
  generated_at: "",
  local_date: "—",
  timezone: "Asia/Taipei",
  mode: "dry_run",
  target_usdt: "1",
  summary: {
    exchanges_total: 2,
    target_eligible: 2,
    pending_invoice_checks: 0,
    total_filled_usdt: "0",
    today_trades: 0,
    yesterday_invoices_issued: 0,
  },
  exchanges: [],
  events: [],
  daily_status: {
    today_date: "—",
    yesterday_date: "—",
    exchanges: [],
  },
  invoice_records: [],
  invoice_sync: {
    source: "gmail",
    status: "disabled",
    checked_at: null,
    messages_scanned: 0,
    records_updated: 0,
    unmatched_records: 0,
    note: "尚未啟用 Gmail 唯讀發票核對",
  },
};

const statusLabels: Record<RunStatus | "waiting", string> = {
  simulated: "模擬完成",
  filled: "成交",
  partial: "部分成交",
  skipped: "已略過",
  failed: "失敗",
  waiting: "等待排程",
};

const invoiceLabels: Record<InvoiceStatus, string> = {
  pending_confirmation: "成交待確認",
  confirmed: "已確認",
  not_found: "尚未查到",
  not_applicable: "不適用",
  manual_check: "待確認",
};

const dailyTradeLabels = {
  ...statusLabels,
  no_record: "尚無紀錄",
};

const executionLabels: Record<ExecutionType, string> = {
  spot: "現貨",
  convert: "閃兌",
  none: "未執行",
};

const sideLabels: Record<TradeSide, string> = {
  buy: "買入",
  sell: "賣出",
  none: "未下單",
};

const invoiceSyncLabels = {
  disabled: "尚未啟用",
  success: "核對完成",
  partial: "需要部分人工核對",
  failed: "核對失敗",
};

function formatNumber(value: string | number, digits = 2) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "—";
  return new Intl.NumberFormat("zh-TW", {
    maximumFractionDigits: digits,
  }).format(parsed);
}

function formatTime(value: string) {
  if (!value) return "尚未更新";
  return new Intl.DateTimeFormat("zh-TW", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Taipei",
  }).format(new Date(value));
}

function formatDate(value: string) {
  if (!value || value === "—") return "—";
  return new Intl.DateTimeFormat("zh-TW", {
    month: "2-digit",
    day: "2-digit",
    timeZone: "Asia/Taipei",
  }).format(new Date(`${value}T12:00:00+08:00`));
}

function sanitizeDashboard(payload: DashboardData): DashboardData {
  const exchanges = payload.exchanges
    .filter((exchange) => SUPPORTED_EXCHANGE_IDS.has(exchange.id))
    .map((exchange) => ({
      ...exchange,
      minimum_twd: exchange.minimum_twd ?? null,
      planned_usdt: exchange.planned_usdt ?? exchange.minimum_usdt,
      convert_supported: exchange.convert_supported ?? false,
    }));
  const events = payload.events
    .filter((event) => SUPPORTED_EXCHANGE_IDS.has(event.exchange))
    .map((event) => ({
      ...event,
      side: event.side ?? (event.mode === "dry_run" ? "buy" : "none"),
      execution_type:
        event.execution_type ?? (event.status === "skipped" ? "none" : "spot"),
    }));
  const invoiceRecords = (payload.invoice_records ?? []).filter((invoice) =>
    SUPPORTED_EXCHANGE_IDS.has(invoice.exchange.toLowerCase()),
  );
  const dailyStatus = payload.daily_status ?? fallbackData.daily_status;
  const dailyExchanges = dailyStatus.exchanges.filter((row) =>
    SUPPORTED_EXCHANGE_IDS.has(row.exchange),
  );
  const countedEvents = events.filter((event) =>
    ["filled", "partial", "simulated"].includes(event.status),
  );
  const totalFilled = countedEvents.reduce(
    (sum, event) => sum + Number(event.filled_usdt || 0),
    0,
  );
  return {
    ...payload,
    exchanges,
    events,
    daily_status: { ...dailyStatus, exchanges: dailyExchanges },
    invoice_records: invoiceRecords,
    invoice_sync: payload.invoice_sync ?? fallbackData.invoice_sync,
    summary: {
      ...payload.summary,
      exchanges_total: exchanges.length,
      target_eligible: exchanges.filter((exchange) => exchange.target_eligible).length,
      pending_invoice_checks: events.filter(
        (event) => event.invoice_status === "pending_confirmation",
      ).length,
      total_filled_usdt: String(totalFilled),
      today_trades: dailyExchanges.filter((row) =>
        ["filled", "partial"].includes(row.today_trade.status),
      ).length,
      yesterday_invoices_issued: dailyExchanges.filter(
        (row) => row.yesterday_invoice.status === "confirmed",
      ).length,
    },
  };
}

function ExchangeCard({
  exchange,
}: {
  exchange: ExchangeStatus;
}) {
  return (
    <article className="exchange-card" style={{ "--accent": exchange.accent } as React.CSSProperties}>
      <div className="exchange-card__top">
        <span className="exchange-mark">{exchange.short_name}</span>
        <span className={`status-dot status-dot--${exchange.today_status}`}>
          {statusLabels[exchange.today_status]}
        </span>
      </div>
      <div>
        <p className="eyebrow">官方私人交易 API</p>
        <h3>{exchange.name}</h3>
      </div>
      <div className="limit-row">
        <span>官方最低</span>
        <strong>
          {formatNumber(exchange.minimum_usdt, 4)} USDT
          {exchange.minimum_twd ? ` · NT$ ${formatNumber(exchange.minimum_twd, 0)}` : ""}
        </strong>
      </div>
      <p className="exchange-note">{exchange.note}</p>
      <div className="capability-line">
        <span>{exchange.convert_supported ? "現貨＋閃兌 fallback" : "現貨交易"}</span>
        <small>{exchange.convert_supported ? "官方 API 支援" : "不使用非公開閃兌端點"}</small>
      </div>
      <div className="eligibility-line">
        <span className={exchange.target_eligible ? "tick tick--yes" : "tick"} aria-hidden="true" />
        本次計畫 {formatNumber(exchange.planned_usdt, 4)} USDT
        {exchange.target_eligible ? "，已符合門檻" : "，目前不可執行"}
      </div>
    </article>
  );
}

function EventRow({ event }: { event: RunEvent }) {
  return (
    <tr>
      <td>
        <div className="table-primary">{event.exchange_name}</div>
        <div className="table-secondary">{event.date}</div>
      </td>
      <td>
        <span className={`status-pill status-pill--${event.status}`}>
          {statusLabels[event.status]}
        </span>
      </td>
      <td>
        <span className={`side-pill side-pill--${event.side}`}>
          {sideLabels[event.side]}
        </span>
      </td>
      <td className="numeric">
        <div className="table-primary">{formatNumber(event.filled_usdt, 4)} U</div>
        <div className="table-secondary">
          {event.avg_price_twd ? `@ NT$ ${formatNumber(event.avg_price_twd, 3)}` : "—"}
        </div>
      </td>
      <td>
        <div className="table-primary">{executionLabels[event.execution_type]}</div>
        <div className="table-secondary">{invoiceLabels[event.invoice_status]}</div>
      </td>
      <td className="message-cell">{event.message}</td>
    </tr>
  );
}

function DailyPulseRow({ row }: { row: DailyExchangeStatus }) {
  const trade = row.today_trade;
  const invoice = row.yesterday_invoice;
  const tradeIsReal = trade.source === "live_record";
  const invoiceDetail = [
    invoice.issued_date ? `開立 ${invoice.issued_date}` : null,
    invoice.amount_twd ? `NT$ ${formatNumber(invoice.amount_twd, 2)}` : null,
    invoice.masked_number,
  ].filter(Boolean).join(" · ");

  return (
    <article className="daily-row" style={{ "--accent": row.accent } as React.CSSProperties}>
      <header className="daily-row__exchange">
        <span className="exchange-mark">{row.short_name}</span>
        <div><strong>{row.exchange_name}</strong><small>USDT / TWD</small></div>
      </header>

      <div className="daily-cell">
        <div className="daily-cell__top">
          <span>今日成交</span>
          <span className={`status-pill status-pill--${trade.status}`}>
            {dailyTradeLabels[trade.status]}
          </span>
        </div>
        <strong className="daily-value">
          {tradeIsReal && ["filled", "partial"].includes(trade.status)
            ? `${formatNumber(trade.filled_usdt, 4)} U`
            : trade.source === "dry_run"
              ? "僅模擬，未成交"
              : dailyTradeLabels[trade.status]}
        </strong>
        <p>
          {sideLabels[trade.side]} · {executionLabels[trade.execution_type]}
          {trade.source === "live_record" ? " · 已通過防重複檢查" : ""}
        </p>
        <small>{trade.message}</small>
      </div>

      <div className="daily-cell daily-cell--invoice">
        <div className="daily-cell__top">
          <span>昨日發票</span>
          <span className={`invoice-badge invoice-badge--${invoice.status}`}>
            {invoiceLabels[invoice.status]}
          </span>
        </div>
        <strong className="daily-value">
          {invoice.status === "confirmed" ? invoiceDetail || "已確認開立" : invoiceLabels[invoice.status]}
        </strong>
        <p>{invoice.note}</p>
        <div className="daily-links">
          {invoice.detail_url && (
            <a href={invoice.detail_url} target="_blank" rel="noreferrer">查看明細 ↗</a>
          )}
          <a href={invoice.lookup_url} target="_blank" rel="noreferrer">
            {invoice.detail_url ? "官方查詢方式 ↗" : "前往官方查詢說明 ↗"}
          </a>
        </div>
      </div>
    </article>
  );
}

function InvoiceRecordCard({ record }: { record: InvoiceRecord }) {
  const exchangeName = record.exchange === "bitopro" ? "BitoPro" : "MAX Exchange";
  return (
    <article className="invoice-record-card">
      <div className="invoice-record-card__top">
        <div>
          <strong>{exchangeName}</strong>
          <small>成交日 {record.trade_date ?? "未指定"}</small>
        </div>
        <span className={`invoice-badge invoice-badge--${record.status}`}>
          {invoiceLabels[record.status]}
        </span>
      </div>
      <p>
        {record.masked_number ?? "未保存號碼"}
        {record.amount_twd ? ` · NT$ ${formatNumber(record.amount_twd, 2)}` : ""}
      </p>
      <small>{record.note ?? (record.checked_at ? `最後確認 ${formatTime(record.checked_at)}` : "尚無備註")}</small>
      {record.detail_url && (
        <a href={record.detail_url} target="_blank" rel="noreferrer">查看安全明細 ↗</a>
      )}
    </article>
  );
}

function App() {
  const [data, setData] = useState<DashboardData>(fallbackData);
  const [filter, setFilter] = useState<"all" | "executed" | "skipped">("all");
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    fetch("./data/dashboard.json", { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error("dashboard data unavailable");
        return response.json();
      })
      .then((payload: DashboardData) => setData(sanitizeDashboard(payload)))
      .catch(() => setLoadError(true));
  }, []);

  const visibleEvents = useMemo(() => {
    if (filter === "executed") {
      return data.events.filter((event) =>
        ["filled", "partial", "simulated"].includes(event.status),
      );
    }
    if (filter === "skipped") {
      return data.events.filter((event) => ["skipped", "failed"].includes(event.status));
    }
    return data.events;
  }, [data.events, filter]);

  const successRate = data.summary.exchanges_total
    ? Math.round((data.summary.target_eligible / data.summary.exchanges_total) * 100)
    : 0;
  const eligibleNames = data.exchanges
    .filter((exchange) => exchange.target_eligible)
    .map((exchange) => exchange.name);
  const readinessTitle = eligibleNames.length
    ? `${eligibleNames.join("、")} 已完成數量調整`
    : "目前沒有平台可建立交易計畫";

  return (
    <main>
      <section className="hero-shell">
        <nav className="topbar" aria-label="主要導覽">
          <a className="brand" href="#top" aria-label="一塊日常首頁">
            <span className="brand-mark">API</span>
            <span>一塊日常</span>
          </a>
          <div className="topbar-meta">
            <a
              className="docs-link"
              href={DEPLOYMENT_GUIDE_URL}
              target="_blank"
              rel="noreferrer"
            >
              部署說明 ↗
            </a>
            <span className={`mode-badge mode-badge--${data.mode}`}>
              <span className="mode-badge__light" />
              {data.mode === "live" ? "正式執行" : "安全模擬"}
            </span>
            <span className="updated">更新 {formatTime(data.generated_at)}</span>
          </div>
        </nav>

        <div className="hero" id="top">
          <div className="hero-copy">
            <p className="kicker"><span>DAILY</span> · USDT RECEIPT PULSE</p>
            <h1>今天有沒有成交，<br /><em>昨天有沒有開票。</em></h1>
            <p className="hero-lead">
              每天只做 USDT/TWD：TWD 足夠就買，否則賣出可用 USDT；MAX 現貨資金不足時再試低額閃兌。執行前會查官方成交紀錄，發票則保存後續確認結果。
            </p>
          </div>

          <aside className="eligibility-card" aria-label="今日可行性摘要">
            <div className="ring" style={{ "--progress": `${successRate * 3.6}deg` } as React.CSSProperties}>
              <div className="ring__inside">
                <strong>{data.summary.target_eligible}/{data.summary.exchanges_total}</strong>
                <span>計畫就緒</span>
              </div>
            </div>
            <div>
              <p className="eyebrow">TODAY'S READINESS</p>
              <h2>{readinessTitle}</h2>
              <p>設定下限為 {formatNumber(data.target_usdt, 4)} USDT；各平台會自動提高到官方最低可成交量。</p>
            </div>
          </aside>
        </div>
      </section>

      <section className="dashboard-shell" aria-label="交易儀表板">
        {loadError && (
          <div className="data-alert" role="status">
            即時資料尚未產生，目前顯示空白安全狀態。
          </div>
        )}

        <div className="metric-grid">
          <article className="metric-card metric-card--primary">
            <p>今日正式成交</p>
            <strong>{data.summary.today_trades}<span> / {data.summary.exchanges_total} 家</span></strong>
            <small>模擬執行不計入成交</small>
          </article>
          <article className="metric-card">
            <p>昨日發票已開立</p>
            <strong>{data.summary.yesterday_invoices_issued}<span> / {data.summary.exchanges_total} 家</span></strong>
            <small>依 Gmail 或安全紀錄檔的確認結果</small>
          </article>
          <article className="metric-card">
            <p>紀錄成交量</p>
            <strong>{formatNumber(data.summary.total_filled_usdt, 4)}<span> U</span></strong>
            <small>正式與模擬結果分開標示</small>
          </article>
          <article className="metric-card metric-card--warning">
            <p>發票待確認</p>
            <strong>{data.summary.pending_invoice_checks}<span> 筆</span></strong>
            <small>官方交易 API 不會回傳發票</small>
          </article>
        </div>

        <section className="section-block daily-pulse">
          <div className="section-heading">
            <div>
              <p className="eyebrow">DAILY PULSE</p>
              <h2>今日成交 × 昨日發票</h2>
            </div>
            <p>
              今日 {formatDate(data.daily_status.today_date)} 先查重再決定是否下單；昨日 {formatDate(data.daily_status.yesterday_date)} 的發票狀態由安全紀錄補上。
            </p>
          </div>
          <div className="daily-list">
            {data.daily_status.exchanges.length ? (
              data.daily_status.exchanges.map((row) => (
                <DailyPulseRow key={row.exchange} row={row} />
              ))
            ) : (
              <div className="empty-state">尚未產生每日狀態。</div>
            )}
          </div>
          <p className="daily-disclaimer">
            發票明細若沒有安全直連，會顯示官方查詢說明；含查詢 token、完整號碼或隨機碼的網址不會發布到 Pages。
          </p>
        </section>

        <section className="section-block">
          <div className="section-heading">
            <div>
              <p className="eyebrow">EXCHANGE CHECK</p>
              <h2>兩家可程式交易所，各自套用最低量</h2>
            </div>
            <p>Dashboard 只顯示具備官方私人下單 API 的平台。</p>
          </div>
          <div className="exchange-grid">
            {data.exchanges.map((exchange) => (
              <ExchangeCard
                key={exchange.id}
                exchange={exchange}
              />
            ))}
          </div>
        </section>

        <section className="split-grid">
          <article className="invoice-panel">
            <div className="section-heading section-heading--compact">
              <div>
                <p className="eyebrow">INVOICE REALITY</p>
                <h2>成交 ≠ 有效發票</h2>
              </div>
              <span className="big-zero">{data.summary.pending_invoice_checks}<span> 筆待確認</span></span>
            </div>
            <p className="panel-copy">
              現貨或閃兌只要回報成交，就先列為「成交待確認」。啟用 Gmail 唯讀 OAuth 後，排程會自動擷取兩家發票通知並更新遮罩紀錄；無法唯一配對時才保留人工確認。
            </p>
            <div className="invoice-flow" aria-label="發票狀態流程">
              <div className="flow-step flow-step--active"><span>01</span><strong>成交</strong><small>API 回報</small></div>
              <div className="flow-line" />
              <div className="flow-step"><span>02</span><strong>待開立</strong><small>1–3 工作天</small></div>
              <div className="flow-line" />
              <div className="flow-step"><span>03</span><strong>確認</strong><small>載具／郵件</small></div>
            </div>
            <div className="rule-note">
              <span className="rule-note__mark">!</span>
              <p><strong>公開頁只放去識別摘要。</strong> API 金鑰、信箱與完整發票號碼永遠不會送到瀏覽器。</p>
            </div>
          </article>

          <article className="status-panel">
            <div className="section-heading section-heading--compact">
              <div>
                <p className="eyebrow">SYSTEM STATUS</p>
                <h2>排程健康度</h2>
              </div>
              <span className="date-stamp">{data.local_date}</span>
            </div>
            <div className="health-list">
              <div><span className="health-icon health-icon--ok">✓</span><p><strong>安全鎖</strong><small>預設不會真實下單</small></p></div>
              <div><span className="health-icon health-icon--ok">✓</span><p><strong>雙層重複防護</strong><small>repository 紀錄＋官方當日成交 API</small></p></div>
              <div><span className="health-icon health-icon--ok">✓</span><p><strong>低額成交策略</strong><small>現貨優先；MAX 資金不足時嘗試閃兌</small></p></div>
              <div><span className="health-icon health-icon--ok">✓</span><p><strong>公開資料最小化</strong><small>不含憑證與訂單編號</small></p></div>
              <div><span className="health-icon">i</span><p><strong>排程時間</strong><small>每日 09:17（台北時間）</small></p></div>
              <div><span className="health-icon">↗</span><p><strong><a href={DEPLOYMENT_GUIDE_URL} target="_blank" rel="noreferrer">完整部署手冊</a></strong><small>Pages、Secrets、驗證與首單</small></p></div>
            </div>
          </article>
        </section>

        <section className="section-block events-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">RUN LEDGER</p>
              <h2>每日執行紀錄</h2>
            </div>
            <div className="filter-tabs" role="group" aria-label="篩選執行紀錄">
              {(["all", "executed", "skipped"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={filter === value ? "active" : ""}
                  onClick={() => setFilter(value)}
                >
                  {value === "all" ? "全部" : value === "executed" ? "已執行" : "已略過"}
                </button>
              ))}
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>交易所</th><th>結果</th><th>方向</th><th className="numeric">成交</th><th>管道／發票</th><th>說明</th></tr>
              </thead>
              <tbody>
                {visibleEvents.length ? (
                  visibleEvents.slice(0, 12).map((event) => <EventRow key={event.id} event={event} />)
                ) : (
                  <tr><td colSpan={6} className="empty-state">這個篩選還沒有紀錄。</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="section-block invoice-records-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">INVOICE LEDGER</p>
              <h2>發票確認紀錄</h2>
            </div>
            <p>Gmail 僅使用唯讀 OAuth；Pages 不會收到信件內文、完整發票號碼、隨機碼或 token。</p>
          </div>
          <div className={`invoice-sync invoice-sync--${data.invoice_sync.status}`}>
            <div>
              <span>GMAIL READ-ONLY</span>
              <strong>{invoiceSyncLabels[data.invoice_sync.status]}</strong>
            </div>
            <p>{data.invoice_sync.note}</p>
            <small>
              {data.invoice_sync.checked_at ? `上次核對 ${formatTime(data.invoice_sync.checked_at)}` : "尚無核對時間"}
              {` · 掃描 ${data.invoice_sync.messages_scanned} 封 · 更新 ${data.invoice_sync.records_updated} 筆`}
            </small>
          </div>
          {data.invoice_records.length ? (
            <div className="invoice-record-grid">
              {[...data.invoice_records]
                .sort((a, b) => String(b.trade_date ?? b.issued_date ?? "").localeCompare(String(a.trade_date ?? a.issued_date ?? "")))
                .slice(0, 8)
                .map((record) => <InvoiceRecordCard key={record.id} record={record} />)}
            </div>
          ) : (
            <div className="empty-state invoice-empty">
              尚未找到發票確認紀錄；啟用 Gmail 後會每日自動回查最近 14 天通知。
            </div>
          )}
        </section>
      </section>

      <footer>
        <div className="brand"><span className="brand-mark">API</span><span>一塊日常</span></div>
        <p>只呈現紀錄，不構成投資、稅務或中獎保證。</p>
        <p>Asia/Taipei · {data.mode === "live" ? "LIVE" : "DRY RUN"}</p>
      </footer>
    </main>
  );
}

export default App;
