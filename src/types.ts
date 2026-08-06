export type RunStatus =
  | "simulated"
  | "filled"
  | "partial"
  | "skipped"
  | "failed";

export type TradeSide = "buy" | "sell" | "none";
export type ExecutionType = "spot" | "convert" | "none";

export type InvoiceStatus =
  | "estimated_zero"
  | "estimated_eligible"
  | "pending_confirmation"
  | "confirmed"
  | "not_found"
  | "not_applicable"
  | "manual_check";

export type DailyTradeStatus = RunStatus | "no_record";

export interface ExchangeStatus {
  id: string;
  name: string;
  short_name: string;
  accent: string;
  api_status: "available";
  minimum_usdt: string;
  minimum_twd: string | null;
  planned_usdt: string;
  convert_supported: boolean;
  target_eligible: boolean;
  invoice_rule: string;
  today_status: RunStatus | "waiting";
  note: string;
}

export interface RunEvent {
  id: string;
  date: string;
  occurred_at: string;
  exchange: string;
  exchange_name: string;
  status: RunStatus;
  side: TradeSide;
  execution_type: ExecutionType;
  requested_usdt: string;
  filled_usdt: string;
  avg_price_twd: string | null;
  invoice_status: InvoiceStatus;
  message: string;
  mode: "dry_run" | "live";
}

export interface InvoiceRecord {
  id: string;
  exchange: string;
  trade_date: string | null;
  status: InvoiceStatus;
  checked_at: string | null;
  issued_date: string | null;
  amount_twd: string | null;
  masked_number: string | null;
  detail_url: string | null;
  note: string | null;
}

export interface DailyExchangeStatus {
  exchange: string;
  exchange_name: string;
  short_name: string;
  accent: string;
  today_trade: {
    status: DailyTradeStatus;
    side: TradeSide;
    execution_type: ExecutionType;
    filled_usdt: string;
    message: string;
    source: "live_record" | "dry_run" | "none";
  };
  yesterday_invoice: {
    status: InvoiceStatus;
    checked_at: string | null;
    issued_date: string | null;
    amount_twd: string | null;
    masked_number: string | null;
    detail_url: string | null;
    lookup_url: string;
    note: string;
  };
}

export interface DailyStatus {
  today_date: string;
  yesterday_date: string;
  exchanges: DailyExchangeStatus[];
}

export interface DashboardData {
  generated_at: string;
  local_date: string;
  timezone: string;
  mode: "dry_run" | "live";
  target_usdt: string;
  summary: {
    exchanges_total: number;
    target_eligible: number;
    filled_runs: number;
    skipped_runs: number;
    confirmed_invoices: number;
    pending_invoice_checks: number;
    total_filled_usdt: string;
    total_notional_twd: string;
    today_trades: number;
    yesterday_invoices_issued: number;
  };
  exchanges: ExchangeStatus[];
  events: RunEvent[];
  daily_status: DailyStatus;
  invoice_records: InvoiceRecord[];
  confirmed_invoices: InvoiceRecord[];
}
