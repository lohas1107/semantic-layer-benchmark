# Gemma 4 26B-A4B 在 PostgreSQL 版 Contoso benchmark 的結果

本報告依照官方 [`REPORT.md`](./REPORT.md) 第 2 節的統計方式整理，並把 Gemma 和論文的 6 個系統放在同一張表比較。官方的統計方式如下：

- 指標用 analytical verdict。
- 準確率附 95 % Wilson CI。
- 系統之間用雙尾精確 McNemar 檢定做逐題配對比較。
- n = 99，排除 q33。

## 0 設定差異

本次的設定和官方有三點不同。

| 項目 | 官方論文（Claude、GPT） | 本次（Gemma） |
|---|---|---|
| 資料庫 | ClickHouse，資料來自 Kaggle `bhanuthakurr/cleaned-contoso-dataset` | PostgreSQL 17，同一份 Kaggle CSV，image 是 `lohas1107/contoso:pg-cube-1.0.0` |
| 生成方式 | **single-shot**，模型輸出一段 SQL | **agentic**，模型透過 Open WebUI 的 native tool loop 呼叫自建的 `run_sql` 查詢資料，最多 15 輪，最後再輸出一段 SQL |
| 模型和推論 | Claude CLI、Codex CLI，reasoning effort 設為 medium | `google/gemma-4-26B-A4B-it`，跑在 vLLM 0.25.1、Nebius H200，context 32K，Open WebUI v0.13.3 |

以下項目和官方相同：

- 100 道題目，每題附 PostgreSQL 版的標準 SQL。
- semantic layer 的內容。PG 版只把 7 處 ClickHouse 語法改成 PG 語法。
- prompt 結構。
- judge 是 Claude Sonnet 4.6，判定規則相同。

**資料一致性：** 100 道 PG 標準 SQL 和論文 ClickHouse 的結果比對後，98 題一致，另外 2 題的差異原因已知，詳見 `docker/contoso/README.md`。所以 Gemma 和論文系統可以逐題配對比較。

**限制：** 生成方式（agentic 對 single-shot）和資料庫引擎同時不同。下面 Gemma 和論文系統的 cross-model 比較，衡量的是「整套系統」的差異，不是單純的模型差異。

## 2 Results

### 2.1 Headline accuracy table

| System | Analytical accuracy | 95 % Wilson CI | Mean latency | Mean tokens in | Mean tokens out |
|---|---|---|---|---|---|
| **Sonnet 4.6 + Sem** | **68.7 %** | [59.0, 77.0] | 11.4 s | 17 309 | 440 |
| **GPT-5.4 + Sem** | **68.7 %** | [59.0, 77.0] | 14.7 s | 18 958 | 284 |
| **Gemma 4 26B + Sem** † | **67.7 %** | [58.0, 76.1] | 19.4 s | 15 253 | 424 |
| **Opus 4.7 + Sem** | **67.7 %** | [58.0, 76.1] | 8.7 s | 23 322 | 271 |
| Opus 4.7 raw | 50.5 % | [40.8, 60.1] | 8.1 s | 19 123 | 230 |
| Sonnet 4.6 raw | 46.5 % | [37.0, 56.2] | 9.6 s | 14 370 | 305 |
| GPT-5.4 raw | 45.5 % | [36.0, 55.2] | 15.6 s | 16 598 | 313 |
| Gemma 4 26B raw † | 44.4 % | [35.0, 54.3] | 13.4 s | 9 745 | 419 |

n = 99 (q33 excluded — reference exceeds ClickHouse memory quota in the paper and PostgreSQL's statement timeout here).

† 本次的 PG＋agentic 結果（run `pg-raw-20261112`）。其餘 6 列由 `published-runs/paper-2026-04-22/` 重新計算，和官方表格的數字完全一致，可以用來驗證統計方式。

Gemma 的指標要注意兩點：

- **延遲：** 包含多輪 tool call 和 SQL 執行，是 agent 從開始到結束的總時間。
- **tokens in：** 是所有 tool 回合的累計值。每多一輪 tool call，就會把 system prompt、DDL 和先前的查詢結果再送一次，所以會隨回合數增加。

### 2.2 Within-model: semantic layer vs raw DDL

McNemar's `b` = "+Sem passes, raw fails"; `c` = "raw passes, +Sem fails".

| Model | b / c | p-value (two-sided exact) | Δ accuracy |
|---|---|---|---|
| Opus 4.7 | 22 / 5 | **0.0015 \*\*** | + 17.2 pp |
| Sonnet 4.6 | 30 / 8 | **0.0005 \*\*\*** | + 22.2 pp |
| GPT-5.4 | 27 / 4 | **< 0.0001 \*\*\*** | + 23.2 pp |
| **Gemma 4 26B †** | 27 / 4 | **< 0.0001 \*\*\*** | + 23.2 pp |

Gemma 的提升幅度（+23.2 pp，p < 0.0001）和論文三個前沿模型（+17.2 到 +23.2 pp）相當，和 GPT-5.4 的 +23.2 pp 相同。semantic layer 的效果在開源的 26B MoE 模型、agentic 執行方式和 PostgreSQL 上同樣成立。

### 2.3 Cross-model: do model differences matter?

The full pairwise McNemar matrix on the analytical verdict, now including Gemma.

|   | Sonnet + Sem | GPT-5.4 + Sem | Gemma + Sem † | Opus + Sem | Opus raw | Sonnet raw | GPT-5.4 raw | Gemma raw † |
|---|---|---|---|---|---|---|---|---|
| **Sonnet + Sem** | — | 8 / 8 ns | 8 / 7 ns | 8 / 7 ns | 27 / 9 \*\* | 30 / 8 \*\*\* | 30 / 7 \*\*\* | 33 / 9 \*\*\* |
| **GPT-5.4 + Sem** | 8 / 8 ns | — | 10 / 9 ns | 10 / 9 ns | 25 / 7 \*\* | 30 / 8 \*\*\* | 27 / 4 \*\*\* | 31 / 7 \*\*\* |
| **Gemma + Sem †** | 7 / 8 ns | 9 / 10 ns | — | 8 / 8 ns | 25 / 8 \*\* | 31 / 10 \*\* | 30 / 8 \*\*\* | 27 / 4 \*\*\* |
| **Opus + Sem** | 7 / 8 ns | 9 / 10 ns | 8 / 8 ns | — | 22 / 5 \*\* | 28 / 7 \*\*\* | 27 / 5 \*\*\* | 29 / 6 \*\*\* |
| **Opus raw** | 9 / 27 \*\* | 7 / 25 \*\* | 8 / 25 \*\* | 5 / 22 \*\* | — | 9 / 5 ns | 11 / 6 ns | 11 / 5 ns |
| **Sonnet raw** | 8 / 30 \*\*\* | 8 / 30 \*\*\* | 10 / 31 \*\* | 7 / 28 \*\*\* | 5 / 9 ns | — | 9 / 8 ns | 11 / 9 ns |
| **GPT-5.4 raw** | 7 / 30 \*\*\* | 4 / 27 \*\*\* | 8 / 30 \*\*\* | 5 / 27 \*\*\* | 6 / 11 ns | 8 / 9 ns | — | 11 / 10 ns |
| **Gemma raw †** | 9 / 33 \*\*\* | 7 / 31 \*\*\* | 4 / 27 \*\*\* | 6 / 29 \*\*\* | 5 / 11 ns | 9 / 11 ns | 10 / 11 ns | — |

(Cell shows row-passes / column-passes among discordant pairs and significance of the two-sided exact McNemar test. \*\*\* p < 0.001, \*\* p < 0.01, \* p < 0.05, ns p ≥ 0.05.)

加入 Gemma 後，官方的兩個群集仍然成立：

- **+Sem cluster:** Opus + Sem ≡ Sonnet + Sem ≡ GPT-5.4 + Sem ≡ **Gemma + Sem**. All six pairwise comparisons are not significant (every p = 1.00). Gemma + Sem against the paper's three +Sem systems: b / c = 7/8, 9/10, 8/8. Accuracy 67.7–68.7 %.
- **Raw cluster:** Opus raw ≡ Sonnet raw ≡ GPT-5.4 raw ≡ **Gemma raw**. All pairwise comparisons are not significant (p ≥ 0.21). Accuracy 44.4–50.5 %.

**Every cross-cluster comparison is significant at p ≤ 0.0046 (p < 0.01).** Gemma raw's discordant counts against the +Sem systems are 4/27, 9/33, 7/31 and 6/29, and Gemma + Sem's against the raw systems are 25/8, 31/10, 30/8 and 27/4.

官方的結論「有沒有 semantic layer 比用哪個模型更重要」在這裡依然成立。加上 semantic layer 後，一個每次推論只啟用約 4B 參數的開源模型，analytical 準確率在統計上和三個前沿閉源模型無法區分。

### 2.4 Cost and latency with the semantic layer

| Configuration | Latency | Tokens in | Tokens out |
|---|---|---|---|
| Opus 4.7 + Sem | 8.7 s | 23.3 K | 271 |
| Sonnet 4.6 + Sem | 11.4 s | 17.3 K | 440 |
| GPT-5.4 + Sem | 14.7 s | 19.0 K | 284 |
| Gemma 4 26B + Sem † | 19.4 s | 15.3 K | 424 |

Gemma + Sem 的成本和延遲要點：

- **延遲最長：** 19.4 s 包含 agent 的多輪 tool call 和每次 SQL 執行，raw 是 13.4 s。
- **輸入 token 最少：** 平均 15.3 K，因為有 22 題完全沒有呼叫 tool。
- **計費方式不同：** Gemma 是自架在 Nebius H200 上的 spot instance，依 GPU 時間計費，不是按 token 計費。semantic 這次 100 題在 endpoint 上只跑了約 9 分鐘（並行數 4）；加上冷啟動，總共約 20 分鐘 GPU 時間。

### 2.5 By difficulty tier (analytical)

| Tier | N | Opus + Sem | Sonnet + Sem | GPT-5.4 + Sem | Gemma + Sem † | Opus raw | Sonnet raw | GPT-5.4 raw | Gemma raw † |
|---|---|---|---|---|---|---|---|---|---|
| simple | 20 | 95.0 % | 95.0 % | 100.0 % | 95.0 % | 90.0 % | 90.0 % | 95.0 % | 95.0 % |
| medium | 20 | 75.0 % | 70.0 % | 65.0 % | 70.0 % | 60.0 % | 45.0 % | 45.0 % | 50.0 % |
| complex | 20 | 70.0 % | 75.0 % | 70.0 % | 75.0 % | 45.0 % | 35.0 % | 35.0 % | 40.0 % |
| advanced | 19 | 42.1 % | 52.6 % | 52.6 % | 57.9 % | 31.6 % | 36.8 % | 21.1 % | 26.3 % |
| expert | 20 | 55.0 % | 50.0 % | 55.0 % | 40.0 % | 25.0 % | 25.0 % | 30.0 % | 10.0 % |

### 2.6 Strict verdict（補充）

官方的 headline 用 analytical verdict。在 strict verdict（逐列完全一致）下，各系統的結果如下：

| System | Strict accuracy | 95 % Wilson CI |
|---|---|---|
| Sonnet 4.6 + Sem | 61.6 % | [51.8, 70.6] |
| GPT-5.4 + Sem | 53.5 % | [43.8, 63.0] |
| Gemma 4 26B + Sem † | 39.4 % | [30.3, 49.2] |
| Opus 4.7 + Sem | 57.6 % | [47.7, 66.8] |
| Opus 4.7 raw | 40.4 % | [31.3, 50.3] |
| Sonnet 4.6 raw | 40.4 % | [31.3, 50.3] |
| GPT-5.4 raw | 37.4 % | [28.5, 47.2] |
| Gemma 4 26B raw † | 31.3 % | [23.0, 41.0] |

Gemma + Sem 的 strict 準確率（39.4 %）明顯低於三個前沿模型 + Sem（53.5–61.6 %）。Gemma 自己的 semantic 對 raw 在 strict 下是 b / c = 13/5，p = 0.096，不顯著。

也就是說，Gemma 加上 semantic layer 後能選對資料表和業務定義，因此 analytical 準確率追上前沿模型。但輸出的欄位形狀、單位和捨入方式還不如前沿模型精確。官方 § 3.4 提到的「format conventions」規則，例如比率一律以百分比表示、四捨五入到 2 位小數，對 Gemma 可能特別有幫助。

## 3 Notes

- **semantic 反而變差的 4 題：**
  - q13：semantic layer 規定預設用 `FactOnlineSales`，但題目要的是每家店的全通路銷售。
  - q19、q28：semantic layer 把 order value 定義為「總額 ÷ 不重複訂單數」，標準答案卻是對每一列取平均。
  - q54：模型寫出不存在的欄位 `return_amount`，而且沒有先用 tool 驗證。

  前三題是 semantic layer 本身的定義和標準 SQL 不一致，論文的系統也有類似的 c 值（4–8）。
- **agent 行為：** 平均 tool 回合數從 raw 的 2.23 降到 +Sem 的 1.72，不呼叫 tool 的題目從 5 題增加到 22 題。
- **工具回合上限：** raw 有 1 題（q65）用滿 15 輪 tool 上限。Open WebUI 收尾時送出 `tools: []`，vLLM 拒絕了這個請求，所以記為失敗。
- **重現方式：** 用下面兩個指令依序跑 raw 和 semantic，最後用 `uv run python scripts/report_run.py --run-id pg-raw-20261112` 產生報告。資料庫是 `docker compose -f docker/contoso/compose.yaml up -d`，image 用 digest 鎖定版本。

  ```bash
  scripts/eval_openwebui.sh --run-id pg-raw-20261112
  scripts/eval_openwebui.sh --run-id pg-raw-20261112 --semantic
  ```

