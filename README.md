# 一塊日常：每日 USDT 成交與發票 Dashboard

這是一個以安全為預設的 GitHub Actions 自動化專案。它每天檢查 BitoPro、MAX、HOYA BIT 的官方門檻，符合規則時才允許下單，並把去識別化結果發布到 GitHub Pages。

> 重要：目前「每天每家買 1 USDT」並不能在三家交易所都執行，也不能保證取得可兌獎發票。程式不會為了通過門檻而擅自把 1 USDT 放大。

## 2026-08 可行性

| 交易所 | 1 USDT | 官方 API | 發票現實 |
| --- | --- | --- | --- |
| BitoPro | 可用限價單 | 有 | 發票依每日手續費彙總；1 USDT 手續費通常未滿 1 元，可能是零元發票 |
| MAX | 不可，最低 8 USDT 且須達 NT$250 | 有 | 依每日實收手續費彙總，四捨五入滿 1 元才開立 |
| HOYA BIT | 不可，最低 10 USDT／NT$300 | 未查到官方公開交易 API | 不使用帳密與瀏覽器模擬登入 |

交易所可能隨時調整費率、限額與 API。BitoPro 與 MAX 的門檻會在每次執行時重新從官方公開 API 讀取；HOYA BIT 目前採保守停用。

## 安全設計

- 預設 `dry-run`，新部署不會下真實訂單。
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
   - `HOYABIT_ENABLED` = `true`
4. 先到 **Actions → Daily purchase and dashboard → Run workflow**，保持 `live=false` 執行一次。
5. 確認 dashboard 與 Actions 紀錄無誤後，再決定是否啟用真實 BitoPro 下單。

排程為每日台北時間 09:17。GitHub Actions 的排程可能因尖峰延遲，不適合需要秒級準時的交易策略。

## 啟用真實 BitoPro 下單

先在 BitoPro 建立沒有提領權限的專用 API Key，再新增以下 GitHub Secrets：

- `BITOPRO_EMAIL`
- `BITOPRO_API_KEY`
- `BITOPRO_API_SECRET`
- `CONFIRM_LIVE_TRADING` = `I_UNDERSTAND_THIS_PLACES_REAL_ORDERS`

最後把 repository variable `LIVE_TRADING` 改成 `true`。排程會開始嘗試 BitoPro 1 USDT 限價買單；MAX 與 HOYA BIT 仍會因門檻自動略過。

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

