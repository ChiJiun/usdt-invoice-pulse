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
  | "not_applicable"
  | "manual_check";

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

export interface ConfirmedInvoice {
  id: string;
  exchange: string;
  issued_date: string;
  amount_twd: string;
  masked_number: string;
  status: "issued" | "won" | "not_won";
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
  };
  exchanges: ExchangeStatus[];
  events: RunEvent[];
  confirmed_invoices: ConfirmedInvoice[];
}
