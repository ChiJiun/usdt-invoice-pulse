import { useEffect, useMemo, useState } from "react";
import type {
  DashboardData,
  ExchangeStatus,
  InvoiceStatus,
  RunEvent,
  RunStatus,
} from "./types";

const SUPPORTED_EXCHANGE_IDS = new Set(["bitopro", "max"]);
const SUPPORTED_INVOICE_NAMES = new Set(["bitopro", "max", "max exchange"]);
const DEPLOYMENT_GUIDE_URL =
  "https://github.com/ChiJiun/usdt-invoice-pulse#github-%E5%85%8D%E8%B2%BB%E9%83%A8%E7%BD%B2";

const fallbackData: DashboardData = {
  generated_at: "",
  local_date: "—",
  timezone: "Asia/Taipei",
  mode: "dry_run",
  target_usdt: "1",
  summary: {
    exchanges_total: 2,
    target_eligible: 1,
    filled_runs: 0,
    skipped_runs: 0,
    confirmed_invoices: 0,
    eligible_invoice_estimates: 0,
    total_filled_usdt: "0",
    total_spend_twd: "0",
  },
  exchanges: [],
  events: [],
  confirmed_invoices: [],
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
  estimated_zero: "預估零元",
  estimated_eligible: "預估可開立",
  confirmed: "已確認",
  not_applicable: "不適用",
  manual_check: "待確認",
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

function sanitizeDashboard(payload: DashboardData): DashboardData {
  const exchanges = payload.exchanges.filter((exchange) =>
    SUPPORTED_EXCHANGE_IDS.has(exchange.id),
  );
  const events = payload.events.filter((event) =>
    SUPPORTED_EXCHANGE_IDS.has(event.exchange),
  );
  const confirmedInvoices = payload.confirmed_invoices.filter((invoice) =>
    SUPPORTED_INVOICE_NAMES.has(invoice.exchange.toLowerCase()),
  );
  const countedEvents = events.filter((event) =>
    ["filled", "partial", "simulated"].includes(event.status),
  );
  const totalFilled = countedEvents.reduce(
    (sum, event) => sum + Number(event.filled_usdt || 0),
    0,
  );
  const totalSpend = countedEvents.reduce(
    (sum, event) =>
      sum + Number(event.filled_usdt || 0) * Number(event.avg_price_twd || 0),
    0,
  );

  return {
    ...payload,
    exchanges,
    events,
    confirmed_invoices: confirmedInvoices,
    summary: {
      ...payload.summary,
      exchanges_total: exchanges.length,
      target_eligible: exchanges.filter((exchange) => exchange.target_eligible).length,
      filled_runs: events.filter((event) => ["filled", "partial"].includes(event.status))
        .length,
      skipped_runs: events.filter((event) => event.status === "skipped").length,
      confirmed_invoices: confirmedInvoices.length,
      eligible_invoice_estimates: events.filter(
        (event) => event.invoice_status === "estimated_eligible",
      ).length,
      total_filled_usdt: String(totalFilled),
      total_spend_twd: String(totalSpend),
    },
  };
}

function ExchangeCard({
  exchange,
  targetUsdt,
}: {
  exchange: ExchangeStatus;
  targetUsdt: string;
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
        <span>最低下單</span>
        <strong>{formatNumber(exchange.minimum_usdt, 2)} USDT</strong>
      </div>
      <p className="exchange-note">{exchange.note}</p>
      <div className="eligibility-line">
        <span className={exchange.target_eligible ? "tick tick--yes" : "tick"} aria-hidden="true" />
        {formatNumber(targetUsdt, 4)} USDT
        {exchange.target_eligible ? " 符合門檻" : " 不符合門檻"}
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
      <td className="numeric">
        <div className="table-primary">{formatNumber(event.filled_usdt, 4)} U</div>
        <div className="table-secondary">
          {event.avg_price_twd ? `@ NT$ ${formatNumber(event.avg_price_twd, 3)}` : "—"}
        </div>
      </td>
      <td className="numeric">
        <div className="table-primary">
          {event.fee_twd ? `NT$ ${formatNumber(event.fee_twd, 4)}` : "—"}
        </div>
        <div className="table-secondary">{invoiceLabels[event.invoice_status]}</div>
      </td>
      <td className="message-cell">{event.message}</td>
    </tr>
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
    ? `${eligibleNames.join("、")} 符合目前目標`
    : "目前沒有平台符合下單門檻";

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
            <h1>每日 USDT 自動化，<br /><em>結果要算清楚。</em></h1>
            <p className="hero-lead">
              成交不等於有效發票。這裡把下單門檻、交易結果與發票資格放在同一張日報裡。
            </p>
          </div>

          <aside className="eligibility-card" aria-label="今日可行性摘要">
            <div className="ring" style={{ "--progress": `${successRate * 3.6}deg` } as React.CSSProperties}>
              <div className="ring__inside">
                <strong>{data.summary.target_eligible}/{data.summary.exchanges_total}</strong>
                <span>符合門檻</span>
              </div>
            </div>
            <div>
              <p className="eyebrow">TODAY'S READINESS</p>
              <h2>{readinessTitle}</h2>
              <p>目前目標為 {formatNumber(data.target_usdt, 4)} USDT；只計入具有官方私人下單 API 的平台。</p>
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
            <p>今日目標</p>
            <strong>{formatNumber(data.target_usdt, 2)}<span> USDT</span></strong>
            <small>每家交易所，各一次</small>
          </article>
          <article className="metric-card">
            <p>{data.mode === "live" ? "累計成交" : "模擬成交"}</p>
            <strong>{formatNumber(data.summary.total_filled_usdt, 4)}<span> U</span></strong>
            <small>正式與模擬結果分開標示</small>
          </article>
          <article className="metric-card">
            <p>預估支出</p>
            <strong><span>NT$ </span>{formatNumber(data.summary.total_spend_twd, 2)}</strong>
            <small>依成交均價計算</small>
          </article>
          <article className="metric-card metric-card--warning">
            <p>已確認發票</p>
            <strong>{data.summary.confirmed_invoices}<span> 張</span></strong>
            <small>不把零元預估算進來</small>
          </article>
        </div>

        <section className="section-block">
          <div className="section-heading">
            <div>
              <p className="eyebrow">EXCHANGE CHECK</p>
              <h2>兩家可程式交易所，逐一過門檻</h2>
            </div>
            <p>Dashboard 只顯示具備官方私人下單 API 的平台。</p>
          </div>
          <div className="exchange-grid">
            {data.exchanges.map((exchange) => (
              <ExchangeCard
                key={exchange.id}
                exchange={exchange}
                targetUsdt={data.target_usdt}
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
              <span className="big-zero">{data.summary.eligible_invoice_estimates}<span> 筆預估</span></span>
            </div>
            <p className="panel-copy">
              系統依目前目標、成交價與設定費率估算發票資格。真正開立結果仍須以交易所通知、Email 或手機載具為準。
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
              <div><span className="health-icon health-icon--ok">✓</span><p><strong>重複防護</strong><small>同交易所每日最多一次</small></p></div>
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
                <tr><th>交易所</th><th>結果</th><th className="numeric">成交</th><th className="numeric">費用／發票</th><th>說明</th></tr>
              </thead>
              <tbody>
                {visibleEvents.length ? (
                  visibleEvents.slice(0, 12).map((event) => <EventRow key={event.id} event={event} />)
                ) : (
                  <tr><td colSpan={5} className="empty-state">這個篩選還沒有紀錄。</td></tr>
                )}
              </tbody>
            </table>
          </div>
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
