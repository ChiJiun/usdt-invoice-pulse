export type RunStatus =
  | "simulated"
  | "filled"
  | "partial"
  | "skipped"
  | "failed";

export type TradeSide = "buy" | "sell" | "none";
export type ExecutionType = "spot" | "convert" | "none";

export type InvoiceStatus =
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
  minimum_usdt: string;
  minimum_twd: string | null;
  planned_usdt: string;
  convert_supported: boolean;
  target_eligible: boolean;
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

export interface InvoiceSyncStatus {
  source: "gmail";
  status: "disabled" | "success" | "partial" | "failed";
  checked_at: string | null;
  messages_scanned: number;
  records_updated: number;
  unmatched_records: number;
  note: string;
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
    pending_invoice_checks: number;
    total_filled_usdt: string;
    today_trades: number;
    yesterday_invoices_issued: number;
  };
  exchanges: ExchangeStatus[];
  events: RunEvent[];
  daily_status: DailyStatus;
  invoice_records: InvoiceRecord[];
  invoice_sync: InvoiceSyncStatus;
}
