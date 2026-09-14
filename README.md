# 一塊日常：USDT/TWD 手續費目標與發票紀錄

GitHub Actions 每日檢查啟用交易所的成交紀錄，必要時才下單，並將去識別摘要發布至 GitHub Pages。下單金額依「目標手續費 ÷ 設定有效費率」計算，不再固定交易 1 USDT。

- Repository：[ChiJiun/usdt-invoice-pulse](https://github.com/ChiJiun/usdt-invoice-pulse)
- Dashboard：[一塊日常](https://chijiun.github.io/usdt-invoice-pulse/)
- 預設 `LIVE_TRADING=false`，不送真實訂單；MAX 預設維持停用。

## 最新交易策略

| 交易所 | 預估費用目標 | 未含價格緩衝的成交額 | 買賣規則 |
| --- | --- | --- | --- |
| BitoPro | NT$0.5 | 費率 0.2% 時約 NT$250 | TWD 足夠就買 USDT；否則 USDT 足夠就賣；兩者不足略過 |
| MAX | NT$1 | 費率 0.16% 時約 NT$625 | 只在 TWD 足夠時買 USDT；不足就略過，絕不自動賣出或閃兌 |

只交易 `USDT/TWD`，每次執行每家最多送一張新單，同日已有成交或既有自動訂單就不再新增；不在同一次執行中反覆買賣。BitoPro 的「來回」是不同日依餘額買或賣，不是強制逐日交替，也不對敲自己的訂單。

HOYA BIT 尚未找到可供會員使用的官方私人下單 API 文件；官方舊 FAQ 表示正在規劃 API，因此目前不串接、不顯示於 Dashboard。若取得正式文件與會員 API Key 申請方式，才能安全加入；不使用帳密模擬登入或未公開端點。[HOYA BIT API 說明](https://support.hoyabit.com/activity/%E6%9C%89%E6%87%89%E7%94%A8%E7%A8%8B%E5%BC%8F%E4%BB%8B%E9%9D%A2-api-%E5%97%8E%EF%BC%9F/)

### 金額如何換算

1. 讀取官方 USDT/TWD 行情、最低量與下單精度。
2. 計算成交額目標：`目標費用 TWD ÷ 有效費率`。
3. 以保守參考價格換算 USDT，再套用官方最低量／最低成交額，數量向上取整至合法精度。
4. 買入餘額檢查使用「計畫量 × 買入價格上限 × (1 + 費率)」，包含價格及費用緩衝。
5. BitoPro 使用限價單，短暫等待後取消未成交部分；MAX 使用 `ioc_limit` 買單，未成交部分立即取消。
6. 保存成交量、均價、預估費用與可取得的實收費用。部分成交不補單，費用不足也不降額重試。

BitoPro 保守參考價為買一價扣除價格緩衝後的賣出限價；MAX 為賣一價扣除價格緩衝。這只能提高預估費用達標的機會，不能保證買入成交價、實收費用或發票金額。

因此 MAX 有 NT$625 不一定足夠：還要支付數量向上取整、價格及費用緩衝；不足時 Dashboard 會顯示實際可用 TWD 與計畫需求。

### 費率與發票不能混為一談

- 預設採一般 taker 費率：BitoPro 0.2%、MAX 0.16%。VIP、推薦／代幣折扣可能改變費率，請設定符合自己帳戶的有效費率。
- 有效費率由環境變數提供，程式不會自動偵測會員折扣；費率填高會讓成交額不足，填低則會交易較多本金。
- 若有代幣抵扣，原幣手續費不一定可算進 TWD 發票。不要只調整費率就認定可開票，需向交易所確認或關閉抵扣。
- NT$0.5 是費用目標，不會被程式擅自改成 1 元發票。是否四捨五入、彙總及何時開立，以交易所實際發票為準。
- Dashboard 的「實收」僅採 API 原始費用與幣種；USDT／BITO 費用不冒充 TWD。缺資料就標示待核對，歷史紀錄不回填猜測費用。
- 成交不等於開票，開票也不等於一定可兌獎。0 元發票不可兌獎；不合常規交易取得大量小額發票可能不予給獎或追回獎金。[統一發票給獎辦法第 11、15 條](https://law-out.mof.gov.tw/LawContent.aspx?id=FL006085)

官方費率：[BitoPro](https://www.bitopro.com/ns/en-US/fees)、[MAX](https://support.maicoin.com/en/support/solutions/articles/32000026028-what-are-the-trading-fees-on-max-)。

## GitHub Actions 與 Pages 完整部署

### 1. Pages 設定

Repository → **Settings → Pages → Build and deployment → Source** 選 **GitHub Actions**。GitHub Free 使用 Pages 請保持 repository public；不需建立 `gh-pages` branch，也不提交 `dist/`。

### 2. Actions Variables

到 **Settings → Secrets and variables → Actions → Variables** 設定：

| Variable | 預設值 | 用途 |
| --- | --- | --- |
| `BITOPRO_ENABLED` | `true` | BitoPro 每日交易開關 |
| `MAX_ENABLED` | `false` | MAX 每日交易開關；需自行明確啟用，停用保留歷史 |
| `BITOPRO_FEE_TWD_TARGET` | `0.5` | BitoPro 每次預估 TWD 手續費目標 |
| `BITOPRO_TAKER_FEE_RATE` | `0.002` | BitoPro 有效費率；0.2% 填 0.002 |
| `MAX_FEE_TWD_TARGET` | `1` | MAX 每次預估 TWD 手續費目標 |
| `MAX_TAKER_FEE_RATE` | `0.0016` | MAX 有效費率；0.16% 填 0.0016 |
| `ORDER_PRICE_SLIPPAGE` | `0.005` | 0.5% 價格緩衝；用於限價與保守估算，不是承諾實際滑價 |
| `LIVE_TRADING` | `false` | 真實交易總開關，通過驗證後才能改 true |

以上費用目標須大於 0，費率與緩衝須介於 0 與 1，拒絕 NaN／無限值。Workflow 已逐項將 Variables 傳入 Python；未設定時採預設值。

舊的 `ORDER_USDT`、`USDT_RESERVE`、`MAX_INVOICE_TWD_TARGET`、`MAX_CONVERT_ENABLED`、`MAX_TEST_DATE` 及 `max-test-625` 模式已移除；舊 Variables 可刪除，不再影響下單。既有 625 元測試與歷史閃兌紀錄仍保留，MAX 只讀閃兌歷史以查重，不會新增閃兌。

### 3. API Key 與 Actions Secrets

API Key 只授予**讀取帳戶＋現貨交易**，不要開啟提領、出金或新增提領地址權限。

- BitoPro：登入網頁版 → API Management，保存會員 Email、API Key、API Secret。
- MAX：登入網頁版 → API Key 管理，保存 Access Key、Secret Key。

到同頁面的 **Secrets** 建立：

| Secret | 用途 |
| --- | --- |
| `BITOPRO_EMAIL` | BitoPro 簽章所需會員 Email |
| `BITOPRO_API_KEY` | BitoPro API Key |
| `BITOPRO_API_SECRET` | BitoPro API Secret |
| `MAX_API_KEY` | MAX Access Key |
| `MAX_API_SECRET` | MAX Secret Key |
| `CONFIRM_LIVE_TRADING` | 必須完全等於 `I_UNDERSTAND_THIS_PLACES_REAL_ORDERS` |

只需提供啟用平台的憑證。Secret 只貼原始值，不帶名稱、引號；載入時會移除前後空白與複製貼上的 UTF-8 BOM。API Key／Secret 不可放在 Variables、repository 或 Pages。

GitHub-hosted runner 沒有固定出站 IP；需要固定白名單時，改用有固定 IP 的 self-hosted runner。

### 4. 先模擬，再驗證

前往 **Actions → Daily USDT trade and dashboard → Run workflow**，Branch 選 `main`：

1. 保持 `LIVE_TRADING=false`，mode 選 `dry-run`：只讀公開行情，不讀私人餘額、不下單，檢查計畫量及預估費用。
2. mode 選 `validate`：唯讀驗證啟用平台的 API。BitoPro 讀餘額與當日成交，MAX 讀餘額；不建立或取消訂單。
3. 確認 build／deploy 成功並開啟 Dashboard。驗證成功只代表讀取權限正常，不保證交易權限或發票。

### 5. 正式啟用

1. 核對費率、折扣、目標費用與可用餘額，建議一次只啟用一家。
2. 設定確認鎖，將 `LIVE_TRADING=true`。
3. 手動 mode 選 `live`，執行一次，再至官方成交紀錄核對。
4. MAX 若要恢復每日買入，還須將 `MAX_ENABLED=true`；新程式不會自行解除既有停用。
5. 不符預期時立即關閉 `LIVE_TRADING`、取消執行中的 Action，必要時撤銷 API Key。

### 6. 每日排程與推送部署

- 排程每日 `01:17 UTC`，即台北時間 **09:17**；GitHub 可能延遲或漏跑，不保證準點。
- 排程只執行啟用平台；`LIVE_TRADING=true` 才會真實下單，否則只模擬。
- 今日 repository 或官方 API 已有成交，即使低於本次費用目標也不補單。
- 直接 push `main` 自動部署，無須 merge。push 只執行 `--refresh`，不呼叫交易所 API、不下單。
- 成功判斷：build／deploy 綠勾，Pages 可開啟且更新時間正確。新策略尚未查行情時會顯示等待換算，不沿用舊 1-USDT 計畫。
- public repository 久無活動，GitHub 可能停用排程，需到 Actions 重新啟用。

Workflow：`.github/workflows/dashboard.yml`。`contents: write` 用於保存去識別資料；`pages: write`、`id-token: write` 用於 Pages 部署。Branch protection 若限制 bot push，需由管理員調整。

## 查重與異常反饋

查重依序使用 `data/state.json`、Dashboard 當日正式成交、官方當日 USDT/TWD 成交／歷史閃兌。每日自動訂單有穩定 client ID；查詢格式未知時停止下單，不猜測無紀錄。

| 情況 | 行為 |
| --- | --- |
| BitoPro 的 TWD 足夠 | 買入完整計畫量 |
| BitoPro 的 TWD 不足、USDT 足夠 | 賣出完整計畫量 |
| BitoPro 兩種資產都不足 | 略過，不縮小成未達費用目標的交易 |
| MAX 的 TWD 不足 | 略過，顯示需求與可用額；USDT 再多也不自動賣 |
| 今日已有成交 | 沿用紀錄，不補足費用 |
| 部分成交 | 保存實際成交；取消未成交部分，不補單 |
| 實收費用讀取失敗 | 保留已成交狀態，費用標示待核對，不因費用讀取失敗重下 |
| 維護／API／拒單錯誤 | 顯示略過或失敗原因；另一家可繼續執行 |

「每日最多一次」限制的是自動新增訂單；已知成交或未完整成交不會重下。若前次明確未成交且尚無既有自動訂單，人工重跑仍會先查重，請勿為湊發票反覆執行。

## 今日成交與昨日發票

交易 API 不提供台灣發票明細，目前**不連接 Gmail、不讀信箱、不需要 Email OAuth token**。請由交易所通知／載具／財政部平台確認後，更新 `data/invoice-records.json`：

```json
[
  {
    "id": "2026-09-14-max",
    "exchange": "max",
    "trade_date": "2026-09-14",
    "status": "confirmed",
    "checked_at": "2026-09-16T10:30:00+08:00",
    "issued_date": "2026-09-16",
    "amount_twd": "1",
    "masked_number": "AB••••••12",
    "detail_url": "https://www.einvoice.nat.gov.tw/APCONSUMER/BTC601W/",
    "note": "範例，請改填實際核對結果"
  }
]
```

`trade_date` 指對應成交日。`status` 可用 `pending_confirmation`、`confirmed`、`not_found`、`manual_check`；`confirmed` 只代表已開立，0 元仍不可兌獎。推送紀錄會重建 Pages：

```bash
git add data/invoice-records.json
git commit -m "chore: update invoice records"
git push origin main
```

不可保存完整發票號碼、隨機碼、手機載具、Email 或查詢 token。完整號碼輸出會遮罩；含帳密、query string 或 fragment 的明細網址不會發布。

官方查詢：[BitoPro](https://support.bitopro.com/hc/zh-tw/articles/360018704812)、[MAX](https://support.maicoin.com/zh-TW/support/solutions/articles/32000026066)。開票可能延遲，昨日未查到不代表最終不開票。

## 本機驗證

需求 Python 3.12、Node.js 22。`.env.example` 只是設定參考，不會被 Python 自動載入；本機請在 shell 設定環境變數，正式部署使用 Actions Variables／Secrets。

```bash
npm ci
python -m bot.runner --dry-run
npm run bot:verify
npm test
```

`npm test` 包含 Python 防護測試、TypeScript 檢查與 Vite 正式建置。不要拿 live 下單當作測試指令。

## 官方文件與風險

- [BitoPro API](https://github.com/bitoex/bitopro-official-api-docs)
- [MAX API v3](https://max-api.maicoin.com/doc/v3.html)
- [GitHub Pages Actions 部署](https://docs.github.com/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)

本工具不構成投資、稅務、法律或中獎建議。真實交易會產生手續費、價差、滑價及 USDT/TWD 價格風險；費用達標、四捨五入開票及兌獎資格均不保證。
