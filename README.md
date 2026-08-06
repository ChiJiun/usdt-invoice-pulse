# 一塊日常：每日 USDT 成交與發票 Dashboard

這是一個以安全為預設的 GitHub Actions 自動化專案。它每天檢查 BitoPro 與 MAX 的官方門檻，符合規則時才允許下單，並把去識別化結果發布到 GitHub Pages。

> 重要：Dashboard 只納入具有官方私人下單 API、可由程式安全執行的交易所。沒有官方下單 API 的平台不會執行，也不會顯示。

## 2026-08 可行性

| 交易所 | 1 USDT | 官方 API | 發票現實 |
| --- | --- | --- | --- |
| BitoPro | 可用限價單 | 有 | 發票依每日手續費彙總；1 USDT 手續費通常未滿 1 元，可能是零元發票 |
| MAX | 不可，最低 8 USDT 且須達 NT$250 | 有 | 依每日實收手續費彙總，四捨五入滿 1 元才開立 |

交易所可能隨時調整費率、限額與 API。BitoPro 與 MAX 的門檻會在每次執行時重新從官方公開 API 讀取。MaiCoin、HOYA BIT、XREX、ZONE Wallet、TWEX、Chainss／Atrix、KryptoGO 等未提供一般會員官方私人下單 API 的平台均排除，不使用帳密、瀏覽器模擬登入或未公開端點。

## 安全設計

- 預設 `dry-run`，新部署不會下真實訂單。
- `validate` 模式只呼叫帳戶讀取 API，可在不下單的情況下先驗證 Key、Secret 與簽章。
- 真實下單需同時設定 `LIVE_TRADING=true` 與固定確認鎖。
- 每家交易所每天至多一筆正式成交；重跑 Actions 不會重複購買。
- BitoPro 使用限價吃單並限制滑價；未成交餘額會送出取消。
- API Key 只存 GitHub Secrets，請只授予「讀取＋現貨交易」，**不要授予提領權限**。
- Pages 只發布金額、狀態與原因，不發布 Email、API Key、完整訂單 ID 或完整發票號碼。
- 任一交易所失敗不會阻止其他交易所完成檢查。

## 本機執行

需求：Python 3.12、Node.js 22。

```bash
npm ci
python -m bot.runner --dry-run
npm run bot:verify
npm run dev
```

測試與正式建置：

```bash
npm test
```

## 免費部署到 GitHub

GitHub Free 的 Pages 需使用公開 repository，因此 dashboard 也會公開。請不要把私人資料寫進 `public/` 或 `data/`。

1. 建立公開 GitHub repository，將本專案推到 `main`。
2. 進入 **Settings → Pages → Build and deployment**，選擇 **GitHub Actions**。
3. 在 **Settings → Secrets and variables → Actions → Variables** 新增：
   - `ORDER_USDT` = `1`
   - `LIVE_TRADING` = `false`
   - `BITOPRO_ENABLED` = `true`
   - `MAX_ENABLED` = `true`
4. 先到 **Actions → Daily purchase and dashboard → Run workflow**，選擇 `dry-run` 執行一次。
5. 確認 dashboard 與 Actions 紀錄無誤後，再決定是否啟用真實 BitoPro 下單。

排程為每日台北時間 09:17。GitHub Actions 的排程可能因尖峰延遲，不適合需要秒級準時的交易策略。

## 串接 API 與啟用真實 BitoPro 下單

請按這個順序上線，先驗證、再手動首單，最後才交給每日排程。

### 1. 建立 BitoPro API Key

1. 登入 BitoPro 網頁版，由右上角帳號選單進入 **API → API Management → Create new API key**。
2. 取名例如 `github-daily-usdt`，只開啟「讀取帳戶」與「現貨交易」所需權限，**不要開啟提領／出金權限**。
3. 當場保存 API Key 和 API Secret；BitoPro 官方文件說明 Secret 只顯示一次。
4. GitHub 托管 runner 使用動態共用 IP，所以這個免費方案不適合綁定單一 IP。BitoPro 未綁 IP 的 Key 目前會在 365 天後到期，請設行事曆在到期前更換。如果你必須使用 IP 白名單，請改用固定出站 IP 的 self-hosted runner。

### 2. 把憑證放入 GitHub Secrets

進入 repository 的 **Settings → Secrets and variables → Actions → Secrets → New repository secret**，逐筆新增：

- `BITOPRO_EMAIL` = 你的 BitoPro 登入 Email
- `BITOPRO_API_KEY` = BitoPro 顯示的 API Key
- `BITOPRO_API_SECRET` = BitoPro 只顯示一次的 API Secret
- `CONFIRM_LIVE_TRADING` = `I_UNDERSTAND_THIS_PLACES_REAL_ORDERS`

請直接在 GitHub 頁面輸入，不要將這些值貼到 Issue、README、Actions variable、commit 或本對話。

### 3. 執行無下單驗證

1. 保持 variable `LIVE_TRADING=false`。
2. 進入 **Actions → Daily purchase and dashboard → Run workflow**。
3. 將 `mode` 選為 `validate` 後執行。
4. 只有在 log 看到 `BitoPro：API 簽章與帳戶讀取權限正常` 才繼續；這個模式不會建立訂單。

### 4. 手動執行第一筆真實單

1. 先確認 BitoPro 現貨錢包至少有約 NT$40 可用餘額，並再次確認 `ORDER_USDT=1`。
2. 把 Actions variable `LIVE_TRADING` 改為 `true`。
3. 進入 **Run workflow**，將 `mode` 選為 `live`，執行一次。
4. 在 BitoPro 訂單紀錄確認數量為 1 USDT，再回 dashboard 確認成交狀態。

首單成功後，每日台北時間 09:17 的排程會自動以真實模式執行。若要立即停止，將 `LIVE_TRADING` 改回 `false`；不需要刪除程式。

## MAX

- `ORDER_USDT=1` 時可保持 `MAX_ENABLED=true` 以在 dashboard 顯示門檻狀態；程式會在驗證憑證或送單前先略過 MAX。MAX 目前官方門檻是至少 8 USDT 且至少 NT$250，所以無法執行 1 USDT 真實單。
- 現行程式的 `ORDER_USDT` 會同時套用到所有啟用的交易所；若改成 `8`並啟用 MAX，BitoPro 也會買 8 USDT。若你要不同交易所不同金額，應先把設定拆分後再上線。

## 發票狀態的限制

交易 API 不會回傳電子發票號碼。程式會依實際或估算手續費標記「預估零元／預估可開立」，但只有交易所通知、綁定載具或財政部平台能確認真正開立。

若要在 dashboard 顯示已確認發票，可把**遮罩後**資料加入 `data/confirmed-invoices.json`：

```json
[
  {
    "id": "2026-07-bitopro-01",
    "exchange": "BitoPro",
    "issued_date": "2026-07-12",
    "amount_twd": "1",
    "masked_number": "AB••••••12",
    "status": "issued"
  }
]
```

不要提交完整發票號碼、隨機碼、手機條碼或會員 Email。若需要全自動核對，建議下一階段串接個人載具的合法授權流程，並把完整資料留在私有儲存，不放 GitHub Pages。

## 免責

本專案是個人紀錄工具，不構成投資、稅務、法律或中獎建議。自動交易可能因價格波動、API 變更、餘額不足、系統延遲或交易所規則而失敗；啟用真實模式前請自行確認最新條款與風險。
