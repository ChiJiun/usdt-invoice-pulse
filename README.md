# 一塊日常：每日 USDT/TWD 成交與發票 Dashboard

使用 GitHub Actions 每日執行 BitoPro 與 MAX 的低額 `USDT/TWD` 交易，並把去識別化的成交與發票確認狀態發布到 GitHub Pages。

- Repository：<https://github.com/ChiJiun/usdt-invoice-pulse>
- Dashboard：<https://chijiun.github.io/usdt-invoice-pulse/>
- 預設安全狀態：`LIVE_TRADING=false`，只模擬、不會下真實訂單。

> 只納入具有官方私人下單 API、可以程式安全執行的平台。目前為 BitoPro 與 MAX；不使用帳密模擬登入、瀏覽器腳本或未公開下單端點。

## 支援範圍

| 交易所 | `ORDER_USDT=1` 時 | 資金不足時 | 發票狀態 |
| --- | --- | --- | --- |
| BitoPro | 最低約 1 USDT 限價現貨 | TWD 不足則改賣 USDT；兩者都不足就略過 | 成交後約兩天內通知，API 不含發票明細 |
| MAX | 最低 8 USDT，且成交額至少 NT$250 | TWD 不足則改賣 USDT；現貨兩邊都不足時可試低額閃兌 | 約 1–3 個工作天開立，API 不含發票明細 |

門檻會在每次交易執行時從官方公開 API 重新讀取；`ORDER_USDT` 是設定下限，不是固定 1 USDT，也不是上限。

## GitHub Actions 與 Pages 完整部署

照以下順序即可完成部署。GitHub Free 使用 Pages 時，repository 應保持 public。

### 1. 開啟 GitHub Pages

1. Repository → **Settings → Pages**。
2. **Build and deployment → Source** 選擇 **GitHub Actions**。
3. 不需要建立 `gh-pages` branch，也不要 commit `dist/`。

### 2. 建立 Actions Variables

前往 **Settings → Secrets and variables → Actions → Variables**：

| Variable | 建議初始值 | 用途 |
| --- | --- | --- |
| `ORDER_USDT` | `1` | 每家希望至少交易的 USDT；MAX 會自動提高到最低 8 USDT／NT$250 |
| `USDT_RESERVE` | `0` | 賣出安全緩衝；`0` 代表不保留，`20` 代表現貨與 MAX 閃兌都不動用最後 20 USDT |
| `MAX_CONVERT_ENABLED` | `true` | MAX 現貨資金不足時，是否允許嘗試官方閃兌 |
| `MAX_CONVERT_TWD_AMOUNT` | `10` | TWD → USDT 閃兌單次上限 |
| `MAX_CONVERT_USDT_AMOUNT` | `1` | USDT → TWD 閃兌單次上限；仍會扣除 `USDT_RESERVE` |
| `BITOPRO_ENABLED` | `true` | 是否執行 BitoPro |
| `MAX_ENABLED` | `true` | 是否執行 MAX |
| `LIVE_TRADING` | `false` | 真實交易總開關；完成 dry-run 與驗證前不要改成 `true` |
| `GMAIL_INVOICE_ENABLED` | `false` | Gmail 唯讀發票核對總開關；完成 OAuth 設定後才改成 `true` |

Variables 不是保密儲存，不能放 API Key、Secret、Email 或確認鎖。

### 3. 建立交易所 API Key 與 Actions Secrets

API Key 只授予「讀取帳戶＋現貨交易」，**不要授予提領、出金或新增提領地址權限**。

- BitoPro：登入網頁版 → API Management，保存 Email、API Key、API Secret。
- MAX：登入網頁版 → API Key 管理，保存 Access Key、Secret Key。

接著到 **Settings → Secrets and variables → Actions → Secrets** 建立：

| Secret | 用途 |
| --- | --- |
| `BITOPRO_EMAIL` | BitoPro API 簽章中的會員 Email |
| `BITOPRO_API_KEY` | BitoPro API Key |
| `BITOPRO_API_SECRET` | BitoPro API Secret |
| `MAX_API_KEY` | MAX Access Key |
| `MAX_API_SECRET` | MAX Secret Key |
| `CONFIRM_LIVE_TRADING` | 必須完全等於 `I_UNDERSTAND_THIS_PLACES_REAL_ORDERS` |
| `GMAIL_CLIENT_ID` | Google Cloud OAuth Web client ID |
| `GMAIL_CLIENT_SECRET` | Google Cloud OAuth client secret |
| `GMAIL_REFRESH_TOKEN` | 只授權 `gmail.readonly` 的離線 refresh token |

只需設定已啟用交易所的憑證。若 Key 曾出現在對話、log、Variable、Issue 或 commit，先到交易所撤銷並重建，再啟用 live。

GitHub-hosted runner 沒有固定出站 IP；如果交易所帳戶強制固定 IP 白名單，請改用具固定 IP 的 self-hosted runner。

### 3A. 啟用 Gmail 唯讀發票核對

1. 到 [Google Cloud Console](https://console.cloud.google.com/) 建立專案，啟用 **Gmail API**。
2. 到 **Google Auth Platform → Audience**，選 External 並把收取 BitoPro／MAX 發票的 Gmail 加為 test user。若長期保持 Testing，Google 對含 Gmail scope 的 refresh token 只提供 7 天效期；長期排程需改為 Production，或由 Google Workspace 管理員設成 Internal。
3. 到 **Clients → Create client → Web application**，Authorized redirect URI 加入 `https://developers.google.com/oauthplayground`，保存 client ID 與 client secret。
4. 開啟 [OAuth 2.0 Playground](https://developers.google.com/oauthplayground/)，右上齒輪勾選 **Use your own OAuth credentials**，填入自己的 client ID／secret，Access type 選 **Offline**。
5. 在 Step 1 輸入唯一 scope：`https://www.googleapis.com/auth/gmail.readonly`，按 **Authorize APIs**，確認登入的是收發票的 Gmail。
6. 在 Step 2 按 **Exchange authorization code for tokens**，複製 refresh token。不要複製到 Issue、Variable、commit、對話或 log。
7. 將三個值存入上述 GitHub Actions Secrets，再把 Variable `GMAIL_INVOICE_ENABLED` 改成 `true`。
8. 手動執行 workflow，mode 選 `validate`。Dashboard 的 **GMAIL READ-ONLY** 應顯示「核對完成」與檢查時間。

> `gmail.readonly` 不能寄信、刪信或修改信件，但授權範圍仍涵蓋整個信箱；Gmail 沒有「只允許某寄件者」的 OAuth scope。本程式會以 query 限制 BitoPro／MAX，但若要把風險降到最低，建議使用專門收發票的 Gmail，或把兩家發票通知自動轉寄到專用帳號。任何能修改 `main` workflow 的協作者都可能影響 Secrets 的使用方式，因此 repository 寫入權限也應只留給可信任的人。

OAuth consent 若維持 Testing，7 天後看到 `invalid_grant` 是預期現象，需重新授權或調整發布狀態。`gmail.readonly` 屬 restricted scope；Google 說明個人用途且少於 100 位已知使用者可不做完整驗證，但登入時仍會看到 unverified app 警告，因此不要把這個 OAuth app 提供給他人。OAuth Playground 必須勾選自己的 credentials；否則 Playground 代管 token 會在 24 小時後撤銷。

預設 Gmail query 如下，可直接貼到 Gmail 搜尋框先確認是否能找到既有發票信：

- BitoPro：`newer_than:14d "幣託科技" {發票 電子發票}`
- MAX：`newer_than:14d "電子發票開立通知" {MAX MaiCoin "現代財富科技"}`

若實際通知格式不同，可建立非機密 Variables `GMAIL_BITOPRO_QUERY`、`GMAIL_MAX_QUERY` 覆寫；不要在 query 放 Email、發票號碼或其他私人資料。

### 4. 第一次 dry-run

1. 確認 `LIVE_TRADING=false`。
2. 前往 **Actions → Daily USDT trade and dashboard → Run workflow**。
3. Branch 選 `main`，mode 選 `dry-run`。
4. 等待 `build` 與 `deploy` 都出現綠色勾勾。
5. 打開 Dashboard，應看到「安全模擬」、BitoPro 約 1 USDT、MAX 至少 8 USDT，且沒有真實訂單。

`dry-run` 只讀公開行情與交易門檻，不讀私人餘額、不會送單，也不會把模擬結果算成真實成交。

### 5. 無下單驗證 API

再次按 **Run workflow**，mode 選 `validate`：

- BitoPro 只讀 `/accounts/balance`。
- MAX 只讀 `/api/v3/wallet/spot/accounts`。
- 成功代表 Key、Secret、Email、簽章與帳戶讀取權限正常。
- `validate` 不建立、取消或成交訂單。

### 6. 第一次真實下單

建議一次只測一家：

1. 先將另一家的 `*_ENABLED` 設為 `false`。
2. 核對 `ORDER_USDT`、`USDT_RESERVE` 與帳戶可用餘額。
3. 確認 API Key 沒有提領權限，且 `CONFIRM_LIVE_TRADING` 已正確設定。
4. 將 `LIVE_TRADING` 改成 `true`。
5. 手動執行 workflow，mode 選 `live`，只執行一次。
6. 到交易所官方訂單／成交紀錄核對，再檢查 Dashboard。
7. 若結果不符預期，立刻將 `LIVE_TRADING` 改回 `false` 並撤銷 API Key。

確認第一家正常後，再啟用第二家。MAX 第一次 live 建議先設 `MAX_CONVERT_ENABLED=false` 驗證現貨，確認後才開啟閃兌 fallback。

### 7. 每日排程

- 排程：每日 `01:17 UTC`，即台北時間 `09:17`。
- `LIVE_TRADING=true`：執行成交查重，必要時才下真實訂單。
- `LIVE_TRADING=false`：只執行 dry-run。
- GitHub 排程可能延遲或偶爾漏跑，不保證準點成交。
- public repository 長期無活動時，GitHub 可能停用 scheduled workflow，需到 Actions 重新啟用。

### 部署成功的判斷方式

- 最新 Actions run 顯示 `completed / success`。
- `Build, update data, and upload Pages artifact` 成功。
- `Deploy dashboard to GitHub Pages` 成功。
- <https://chijiun.github.io/usdt-invoice-pulse/> 可開啟，更新時間與 `public/data/dashboard.json` 相符。

直接 push 到 `main` 即會部署，不需要手動 merge。push 只執行 `python -m bot.runner --refresh`：不呼叫交易所 API、不會下單，只重建 repository 內的成交／發票公開資料與 Pages artifact。

## 交易與防重複邏輯

程式只處理 `USDT/TWD`，不會碰其他幣種或交易對。

```mermaid
flowchart TD
  A[讀取 USDT/TWD 行情與官方門檻] --> B[依最低 USDT、最低 TWD 與 ORDER_USDT 計算計畫量]
  B --> C{正式模式?}
  C -->|否| D[只模擬並更新 Dashboard]
  C -->|是| E{repository 已有今日正式成交?}
  E -->|是| F[沿用紀錄，不呼叫下單 API]
  E -->|否| G{官方 API 已有今日 USDT/TWD 成交?}
  G -->|是| F
  G -->|否| H{TWD 足夠?}
  H -->|是| I[買入 USDT]
  H -->|否| J{扣除保留量後 USDT 足夠?}
  J -->|是| K[賣出 USDT]
  J -->|否| L{MAX 閃兌已啟用且仍有小額餘額?}
  L -->|是| M[嘗試設定的低額閃兌]
  L -->|否| N[資金不足，本日略過]
  I --> O[保存去識別成交與待確認發票狀態]
  K --> O
  M --> O
```

防重複共有三層：

1. `data/state.json` 的當日正式成交。
2. Dashboard 已保存的當日 live 成交。
3. 官方成交歷史：BitoPro 查現貨 trades；MAX 查現貨 trades 與 converts。

因此當天若已手動完成一筆 USDT/TWD 成交，也會被視為今日已成交，不再新增訂單。

### 餘額與失敗反饋

| 情況 | 行為與 Dashboard 反饋 |
| --- | --- |
| TWD 足夠 | 買入計畫量 USDT，顯示買入、現貨、成交量與均價 |
| TWD 不足、USDT 足夠 | 賣出計畫量 USDT，並保留 `USDT_RESERVE` |
| MAX 現貨不足但仍有小額餘額 | 最多嘗試設定的 NT$10 或 1 USDT 閃兌；被拒絕時不自動加碼 |
| 兩種資產都不足 | 略過，不把單純零餘額當成程式錯誤 |
| 今日已有成交 | 沿用既有結果，不再次呼叫下單 API |
| 市場維護 | 略過並顯示市場狀態 |
| 憑證、網路、簽章或拒單錯誤 | 顯示去識別化失敗原因；另一家仍繼續執行 |

## 今日成交與昨日發票

Dashboard 的 `DAILY PULSE` 分開顯示：

- **今日成交**：由 repository 與官方成交 API 自動判斷；dry-run 會清楚標示「僅模擬，未成交」。
- **昨日發票**：由 `data/invoice-records.json` 的安全紀錄判斷。
- **發票明細**：有安全 `detail_url` 時可以點擊；否則顯示交易所官方查詢說明。

BitoPro 與 MAX 的交易 API 都不會回傳台灣電子發票號碼，因此不能只靠交易 API 自動確認開票。BitoPro 約兩天內通知，MAX 約 1–3 個工作天開立；「昨日尚未查到」不代表最終不會開立。

### Gmail 自動核對邏輯

1. 每次 schedule 或手動 workflow 完成交易檢查後，以 `gmail.readonly` 回查最近 14 天的兩家發票通知。
2. 從信件記憶體中解析發票號碼、開立日、成交／消費日與金額；不下載一般附件、不修改已讀狀態，也不刪除或加 Gmail label。
3. 只有信件明確包含兩家識別字與台灣發票號碼時才建立紀錄。repository 只寫入遮罩號碼、日期、金額、檢查時間與 Gmail message ID 的不可逆雜湊。
4. 信件有明確成交日就直接配對；否則只有在最近 7 天恰好存在一筆尚未配對的正式成交時才自動配對。候選超過一筆時不猜測，紀錄保留「無法唯一對應成交日」。
5. 更新 `data/invoice-records.json` 後執行 `--refresh`，Dashboard 同時顯示 Gmail 成功／部分待人工／失敗、掃描封數、更新筆數與遮罩發票明細。
6. Gmail 失敗不會阻止已完成的交易狀態 commit；Dashboard 會留下安全錯誤狀態，下一次排程再重試。

完整信件、主旨、寄件者、完整號碼、隨機碼、載具、client secret、refresh token 與短效 access token 都不會寫入 repository 或 Pages。官方規格：[Gmail 唯讀 messages.list](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list)、[Gmail 伺服器端 OAuth](https://developers.google.com/workspace/gmail/api/auth/web-server)。

### 更新發票紀錄

以成交日作為 `trade_date`，編輯 `data/invoice-records.json`：

```json
[
  {
    "id": "2026-08-05-max",
    "exchange": "max",
    "trade_date": "2026-08-05",
    "status": "confirmed",
    "checked_at": "2026-08-06T10:30:00+08:00",
    "issued_date": "2026-08-06",
    "amount_twd": "1",
    "masked_number": "AB••••••12",
    "detail_url": "https://www.einvoice.nat.gov.tw/APCONSUMER/BTC601W/",
    "note": "已由載具確認"
  }
]
```

| `status` | 意義 |
| --- | --- |
| `pending_confirmation` | 已成交，仍在合理等待期 |
| `confirmed` | 已從 Email、載具或財政部平台確認開立 |
| `not_found` | 已查詢但目前尚無資料，之後仍可更新 |
| `manual_check` | 資訊不足，需要人工再確認 |

推送紀錄後會自動更新 Pages：

```bash
git add data/invoice-records.json
git commit -m "chore: update invoice records"
git push origin main
```

公開 repository 不可保存完整發票號碼、隨機碼、手機條碼、Email、會員資料或查詢 token。完整號碼即使誤填也會在 Dashboard 輸出時遮罩；含帳密、query string 或 fragment 的 `detail_url` 會被拒絕發布。

官方查詢方式：[BitoPro 發票查詢與載具綁定](https://support.bitopro.com/hc/zh-tw/articles/360018704812)、[MAX 發票查詢與對領獎](https://support.maicoin.com/zh-TW/support/solutions/articles/32000026066)。

## Workflow 模式

| 觸發方式 | 行為 | 會下單嗎 | 會部署 Pages 嗎 |
| --- | --- | --- | --- |
| push `main` | `--refresh`，只重建公開資料 | 否 | 是 |
| 手動 `validate` | 只讀私人帳戶 API；啟用時也核對 Gmail | 否 | 是 |
| 手動 `dry-run` | 公開行情模擬；啟用時也核對 Gmail | 否 | 是 |
| 手動 `live` | 查重後依餘額決定交易，再核對 Gmail | 可能；需通過雙重安全鎖 | 是 |
| 每日 schedule | `LIVE_TRADING=true` 才 live，之後核對 Gmail | 依設定 | 是 |

Workflow 檔案：`.github/workflows/dashboard.yml`。權限用途：

- `contents: write`：提交去識別化 Dashboard 與防重複狀態。
- `pages: write`、`id-token: write`：部署 GitHub Pages。

若 organization 或 branch protection 禁止 Actions 寫入，資料 commit 可能失敗，需要 repository 管理員調整政策。

## 緊急停止

1. 將 Actions Variable `LIVE_TRADING` 改成 `false`。
2. 到交易所撤銷 API Key。
3. 到 GitHub Actions 取消仍在執行的 workflow。

不需要刪除 repository 或 Dashboard。

## 本機驗證

需求：Python 3.12、Node.js 22。

`.env.example` 是設定名稱參考，Python 不會自動載入該檔；本機驗證私人 API 前，請先在目前 shell 設定需要的環境變數。GitHub 部署則使用前述 Variables 與 Secrets。

```bash
npm ci
python -m bot.runner --dry-run
npm run bot:verify
npm run invoice:sync
npm test
```

`npm test` 會執行 Python 防護測試、TypeScript 檢查與 Vite 正式建置。

## 常見問題

| 現象 | 原因與處理 |
| --- | --- |
| MAX 顯示 8 USDT | 正常；`ORDER_USDT=1` 是下限，MAX 會提高到官方最低量 |
| MAX 計畫量高於 8 USDT | NT$250 最低成交額換算後需要更多 USDT，或 `ORDER_USDT` 設得更高 |
| MAX 閃兌失敗 | 官方未公開最低量；程式不會自動加碼，可調整閃兌 Variables 或關閉 fallback |
| `LIVE_TRADING 尚未開啟` | workflow 選了 live，但 Variable 仍是 `false` |
| `Unauthorized api key`／簽章失敗 | 檢查 Secret、BitoPro Email、權限與 Key 是否過期；不要把值貼到 log |
| 餘額不足而略過 | TWD 不足，扣除保留量後的 USDT 也不足；MAX 可能同時沒有可用閃兌額 |
| `USDT_RESERVE` 要設多少 | 它不是交易所門檻，只是防止程式賣光 USDT；不需要保留量就維持 `0` |
| 今日已有正式成交 | 防重複機制生效，會沿用既有結果而不再下單 |
| deploy 顯示 skipped | 手動 workflow 選的 Branch 不是 `main` |
| Pages 404 或仍是舊版 | 確認 deploy job 成功，從 Settings → Pages 的 **Visit site** 開啟並等待快取更新 |
| 排程沒有執行 | 到 Actions 檢查 scheduled workflow 是否被 GitHub 停用 |
| Gmail 顯示 `failed`／`invalid_grant` | 檢查三個 Gmail Secrets；Testing 狀態的 refresh token 7 天後會失效 |
| Gmail 掃描為 0 | 先把 README 的預設 query 貼到 Gmail；若實際主旨不同，再用兩個 query Variables 覆寫 |
| Gmail 有掃描但更新為 0 | 信件內文格式未符合解析器；提供移除姓名、Email、完整號碼、隨機碼與連結後的欄位名稱範例再調整 parser |
| Gmail 已確認但沒有成交日 | 同一期間有多筆候選成交，程式刻意不猜；可依信件內容人工補上 `trade_date` |

## 官方參考

- [BitoPro 官方 API](https://github.com/bitoex/bitopro-official-api-docs)
- [BitoPro 發票規則](https://support.bitopro.com/hc/zh-tw/articles/360001517911)
- [MAX API 文件](https://max-api.maicoin.com/doc/v3.html)
- [MAX 發票規則](https://support.maicoin.com/zh-TW/support/solutions/articles/32000021074)
- [Gmail API messages.list](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list)
- [Google OAuth Web Server 流程](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Google 個人用途 OAuth 驗證例外](https://support.google.com/cloud/answer/13464323)
- [GitHub Pages 自訂 Actions](https://docs.github.com/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [GitHub Actions Secrets](https://docs.github.com/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)

## 免責

本專案是個人自動化與紀錄工具，不構成投資、稅務、法律或中獎建議。自動交易可能因價格波動、API 變更、餘額不足、排程延遲或交易所規則而失敗；啟用真實模式前請自行確認最新條款與風險。
