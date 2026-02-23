# Deep research on upgrading the crypto-trading-agent toward an AI-powered systematic strategy factory

## Baseline assessment of the current repository

The current codebase is an async-first Python trading system with a modular pipeline, organized around ingestion → storage → features/indicators → strategy signals → execution, with monitoring and operational guardrails built in. The repo’s own contributor rules explicitly define it that way (Python 3.11+, aiohttp + TimescaleDB + Prometheus + Grafana, “paper mode is the default,” live requires explicit config). citeturn21view1

At runtime, the orchestrating entrypoint (`src/main.py`) wires together the major subsystems:

- **Market-data ingestion**: it can ingest via WebSockets or REST-like paths depending on configuration, and it runs the ingestor as a background asyncio task. citeturn8view2  
- **TimescaleDB persistence**: Timescale is the default database target in settings (host `timescaledb`, standard Postgres fields), and Docker Compose provisions TimescaleDB plus persistence volumes. citeturn8view0turn19view0  
- **Indicator pipeline**: `IndicatorComputer` computes indicators periodically and `IndicatorWriter` persists; `IndicatorReader` supplies the latest indicator rows to the strategy engine. citeturn8view2turn17view3  
- **Strategy engine**: the engine runs multiple strategies per symbol, collects per-strategy signals, and converts them into one consensus signal using a signal aggregator plus optional cooldown logic. citeturn9view0turn17view2turn8view0  
- **Execution**: there’s an internal paper executor that simulates fills and fees, and a live executor path that uses a Binance private API client. citeturn17view0turn21view2turn8view2  
- **Risk management**: the risk manager loads YAML risk limits, maintains state, and includes kill switch + circuit breakers + loss limits (daily loss, drawdown, latency spike, API errors, consecutive losses), with a background monitoring loop. citeturn16view0turn8view1turn8view2  
- **Portfolio accounting**: positions and trades are persisted to DB (tables `positions` and `trades`), with a local cache and normalization logic for duplicates. citeturn17view1  
- **Monitoring stack**: Prometheus and Grafana are deployed via Compose; the `agent` service exposes port 8000 and includes a healthcheck. The prod compose adds an Nginx reverse proxy and resource limits. citeturn19view0turn19view1  

There’s already an “AI” component, but it’s an **operational overseer** rather than a research/backtesting agent: `OverseerAgent` polls Telegram, supports `/status`, `/risk`, `/positions`, `/reset`, and `/ask`, and can call an xAI client for Q&A if configured. citeturn13view1turn8view2

### Key mismatches and “truth drift” inside the repo

Several repo documents appear slightly out of sync with the actual config and runtime wiring:

- The README describes “Spot trading only” and claims no futures support, and shows “enabled: false” examples for execution; however the current `config/settings.yaml` includes an explicit `futures:` section enabled and a strategy routing concept (`default_trading_mode: spot`), while `USAGE.md` describes both spot and futures modes. citeturn10view0turn8view0turn10view1  
- A generated `CODE_REVIEW.md` (dated Feb 7, 2026) reports that the strategy engine is “under-developed” and “not connected,” but the actual `src/main.py` clearly instantiates `StrategyEngine` and routes signals (to paper, spot, and futures paths). This suggests the review document is stale relative to the current code. citeturn6view0turn8view2  

That drift matters for an AI-driven lifecycle: autonomous systems depend on documents/config/code agreeing, or you get the software equivalent of “the map says bridge; reality says canyon.”

## Gap analysis against the provided autonomous AI-agent plan

Your reference plan requires an agent that (a) discovers research-backed hypotheses, (b) writes and debugs backtesting code, (c) runs Walk-Forward Optimization (WFO) and Monte Carlo robustness checks, (d) monitors live results vs. simulated expectations, and (e) retrains/re-optimizes or stops trading under strict risk rules.

Compared to that target, the repo is already strong in **operations and guardrails**, but incomplete as a **systematic strategy factory**:

What you already have (and can re-use directly):

- A signal-based strategy abstraction and multi-strategy aggregation (consensus scoring + agreement thresholds). citeturn9view0turn17view2turn8view0  
- Built-in risk manager primitives (kill switch, circuit breakers, loss limits) and an operational reset workflow via Telegram. citeturn16view0turn13view1  
- A portfolio/trade ledger (positions + trades tables) that can anchor monitoring and performance attribution. citeturn17view1  
- A basic paper execution simulator (fills at signal price with configurable fees), which is a useful staging area even though it’s not a real backtest engine. citeturn17view0  

What is missing or not rigorous enough for the plan’s validation bar:

- **Backtesting as a first-class workflow**: the repo has paper trading, but not a historical event-driven backtester that replays years of data with realistic slippage/fees/latency/resolution issues (the plan depends on this starting Week 1–3). citeturn17view0turn10view0  
- **Automated WFO and parameter optimization**: no evidence of a WFO pipeline or automated rolling train/test windows in this repo (by contrast, QuantConnect has explicit WFO support). citeturn0search2  
- **Monte Carlo robustness checks**: the repo doesn’t appear to generate distributions of outcomes via resampling/bootstrapping/perturbations to measure strategy fragility (the plan calls for it in Weeks 4–6). citeturn14search22  
- **Research ingestion**: there’s no pipeline to scan academic sources (SSRN/arXiv) and produce defensible hypotheses; the only AI usage here is an ops chatbot, not an R&D agent. citeturn13view1  
- **Lifecycle governance**: the plan requires explicit gates (“you review and approve one idea per week,” paper trading before live, stop trading on thresholds). The repo has risk controls, but not a full “strategy lifecycle state machine” (idea → prototype → WFO → paper → live → retire). citeturn16view0turn10view0  

This gap profile strongly suggests the best “upgrade” path is: **preserve your execution + operations core, and add a dedicated research/backtest/validation subsystem** instead of trying to bolt “AI strategy generation” directly onto live trading loops.

## Target architecture choices that align with the plan

The plan is explicitly QuantConnect-centric (Research environment, cloud backtesting, Mia, WFO, Monte Carlo tooling). Conveniently, QuantConnect also has first-class support for Binance brokerage, data feeds, and both spot and futures models, which creates a clean path to implement the plan without rebuilding infrastructure. citeturn20search0turn20search2turn20search8

image_group{"layout":"carousel","aspect_ratio":"16:9","query":["QuantConnect LEAN platform screenshot","Binance exchange logo","TimescaleDB logo","Grafana trading dashboard Prometheus"]}

### QuantConnect-first architecture

In this model, **QuantConnect becomes the primary research/backtest/live runtime**, and this repo becomes either:

- a secondary “sidecar” for custom monitoring/metrics, or  
- a deprecated execution stack (kept only for experiments).

Why this aligns so well with your plan:

- QuantConnect positions Mia as an “agentic coding assistant” that can help write code and interact with platform tooling. citeturn0search5turn0search37  
- QuantConnect documents “AI Assistance” as connecting with OpenAI/Claude/self-hosted LLMs to write strategies and interpret results. citeturn0search1  
- QuantConnect’s documentation explicitly describes walk-forward optimization, which is central to your plan’s robustness requirements. citeturn0search2  
- QuantConnect’s Lean CLI and cloud APIs can automate backtests and parameter optimizations (which maps nicely to the tool-calling agent design in your plan). citeturn14search0turn14search23turn14search14  
- Binance integration is first-class in QuantConnect docs and Lean CLI live-trading brokerages. citeturn20search1turn20search2turn20search7  

Under this approach, much of what your repo does today (data ingestion, storage plumbing, some execution logic) is “reinventing QuantConnect/Lean.” QuantConnect already offers Binance-curated historical data workflows and live execution environments, which is exactly what your plan wants. citeturn20search0turn20search8turn20search18  

### Hybrid architecture

If you want to keep this repo as your production execution and observability stack (for control, customization, or cost reasons), the hybrid design is:

- **QuantConnect/Lean as the research + validation engine**  
- **This repo as the execution + monitoring engine**

The key architectural constraint here is **behavioral equivalence**: the signal logic you validate in QuantConnect must match what you deploy in this repo. Otherwise, WFO/Monte Carlo results won’t predict live behavior.

The most practical hybrid pattern is:

- Use QuantConnect APIs to **generate strategy artifacts**: parameters, indicator definitions, and a “signal spec” (not raw, platform-specific code). QuantConnect’s REST API supports backtest creation and reading output, and supports optimization job creation. citeturn14search0turn14search24turn14search23  
- Implement the “signal spec” in this repo as a strategy plugin (your `BaseStrategy`-based architecture is already designed for that). citeturn9view0turn10view0  
- Store “validated strategy versions” into your repo’s DB and enforce a gate in execution (only deploy strategies that have a passing WFO + robustness certificate).  

This hybrid approach keeps your strong operational primitives (risk manager + Telegram governance + Prometheus/Grafana dashboards) while outsourcing the hardest part—rigorous validation—to a platform with mature infra. citeturn16view0turn13view1turn19view0  

## Concrete improvements and changes to implement the plan using this repo as the baseline

### Add a strategy lifecycle subsystem

Right now strategies are executed because they’re configured (`settings.yaml`) and loaded at startup, not because they’ve passed a staged validation pipeline. citeturn8view0turn8view2  

Create a lifecycle component with explicit states:

- **candidate** (idea exists, not coded)
- **prototype** (code exists, baseline backtest exists)
- **validated** (passed WFO + robustness checks)
- **paper** (running paper trading with monitoring)
- **live** (enabled for real trading)
- **retired** (disabled, archived)

This can be implemented as:

- A new `src/lifecycle/` package (state machine + promotion rules).
- A DB table, e.g. `strategy_versions` and `strategy_runs`, keyed by a `strategy_id`, `git_commit_hash`, parameters hash, and “proof artifacts” (backtest IDs, optimization IDs, WFO report outputs).

This is important because your repo already has the execution and monitoring layers; what’s missing is the *governance glue* that prevents “random code becomes real money.” citeturn16view0turn21view1  

### Build QuantConnect tool functions for an agentic R&D loop

Your plan is explicitly tool-driven: `run_backtest(parameters)`, `optimize(parameter_grid)`, `fetch_ohlcv(symbol, start, end)`. QuantConnect provides exactly the kind of APIs an LLM agent can call:

- Create backtests via the REST endpoint. citeturn14search0turn14search2  
- Read backtest results (statistics/charts/orders). citeturn14search24turn14search9  
- Create optimization jobs via REST. citeturn14search23  
- Run local backtests and optimizations via Lean CLI (`lean backtest`, `lean optimize`). citeturn14search14turn14search30turn14search21  

A high-leverage change to this repo is adding a new “research agent service” with a narrow, auditable tool surface:

- `qc_create_backtest(project_id, compile_id, params, name)`  
- `qc_read_backtest_statistics(project_id, backtest_id)`  
- `qc_create_optimization(job_spec)`  
- `qc_pull_optimization_results(job_id)`  
- `qc_run_wfo(job_spec)` (either using QC WFO mechanisms or composing rolling optimizations + result stitching)  
- `store_experiment(metadata, artifacts)`  

Then your runtime LLM agent can *only* operate through that tool layer, which is far safer than letting it write arbitrary code in production.

This also makes it easy to switch between **QuantConnect cloud** and **Lean CLI local** backtests, depending on cost and throughput needs. citeturn14search7turn14search31  

### Make Walk-Forward Optimization a first-class pipeline stage

WFO isn’t optional if you want to follow the validation principles you cited; it’s one of the most effective “anti-overfitting” mechanisms for parameterized strategies.

QuantConnect defines walk-forward optimization as periodically adjusting parameters to optimize an objective function using a trailing window of data. citeturn0search2  
Independent educational sources emphasize that static “single split” validation fails to account for changing regimes, and WFO creates a sequence of rolling train/test evaluations. citeturn15search27  

In the repo, implement a `src/validation/wfo.py` module that can:

- define windows (e.g., 24/6 months, 36/9 months, 48/12 months as in your plan),
- run optimization on in-sample windows (grid search or whatever method you support),
- evaluate on subsequent out-of-sample windows,
- stitch OOS equity curves, and
- persist results to DB as “validation artifacts”.

If using QuantConnect’s optimizer, be aware of platform constraints like grid search characteristics and parameter limits in some contexts. citeturn14search1turn14search3  

### Add Monte Carlo robustness checks as a promotion gate

A strategy that “works once” may still be brittle. Monte Carlo is a standard approach for generating distributions of plausible outcomes via repeated random sampling (for example, bootstrapping trade returns, shuffling trade sequences, or sampling slippage/fee perturbations). citeturn14search22turn14search34  

A practical implementation for this repo:

- Once you have a trade list (from QC backtest orders or from your own simulated fills), run:
  - **trade sequence bootstrap** (resample trades with replacement),
  - **block bootstrap** (to preserve autocorrelation at the regime level),
  - **fee/slippage stress sampling** (randomly perturb cost parameters),
- derive distributions for MaxDD, Sharpe/Sortino, CAGR, ruin probability,
- require “stability margin” thresholds before promoting from validated → paper.

QuantConnect also includes Monte Carlo concepts and tutorials in its learning content (even if not always presented as “strategy simulation”), which can be used as platform-native scaffolding. citeturn14search11turn14search26  

### Upgrade paper trading from “fills at signal price” to a backtest-grade simulator

Your `PaperExecutor` is a good skeleton, but it fills immediately at the signal’s price and uses simple fee rates. That’s fine for plumbing tests, but it is not robust enough to be your Week 7–8 “paper trading correlates with WFO curve” gate. citeturn17view0  

To get closer to your plan’s “paper-trading correlates with simulated curve (R² > 0.8)” requirement, you need:

- **slippage model** (at minimum: spread + volatility-based slippage; ideally a microstructure model or orderbook-based approximation),
- **latency model** (execution delay distributions),
- **partial fill and rejection simulation** (especially for futures),
- **market regime handling** (different liquidity conditions).

If you decide to keep your own simulator rather than using Lean’s execution simulation, the work is non-trivial—Lean exists partly so you don’t have to write this yourself. citeturn14search17turn20search10  

### Close the loop with a performance monitoring agent that enforces lifecycle decisions

You already have the operational command path in Telegram (`/status`, `/risk`, `/reset`) and a risk manager that can block trading. citeturn13view1turn16view0  

To implement the plan’s “monitor and decide when to retrain or stop,” add a monitoring agent that:

- calculates rolling live performance metrics from `trades` and `positions` tables, citeturn17view1  
- compares live stats to the WFO out-of-sample distribution (not just a single curve),
- triggers:
  - “pause trading + alert” if deviation exceeds threshold,
  - “re-optimize monthly” if performance degrades but risk isn’t breached,
  - “retire strategy” if repeated validations fail.

This becomes much easier if your validation artifacts include not just point estimates (Sharpe, MaxDD) but distributions from Monte Carlo, because you can treat live results as another draw and ask “is it statistically plausible that we are still on-model?”

### Tighten exchange-rule validation for live orders

A core operational failure mode in crypto bots is order rejection (invalid quantity, min notional, precision filters). Your current Binance private client explicitly caches LOT_SIZE filters and formats quantities to step size. citeturn13view4  

However, Binance also enforces MIN_NOTIONAL / NOTIONAL constraints and other filters, and order validity depends on `exchangeInfo` filters. citeturn0search3turn0search27  

Concrete improvement: extend the cached symbol filters to include MIN_NOTIONAL/NOTIONAL and enforce them in `TradingExecutor` *before* placing orders. This reduces noisy rejection loops and improves the quality of your monitoring signals (a rejection should be a genuinely exceptional event, not a “normal” event).

### Resolve documentation and configuration drift

Before adding “autonomous strategy generation,” fix the drift between:

- README claims (“Spot only,” “enabled false,” etc.) citeturn10view0  
- actual defaults in `config/settings.yaml` (paper mode, execution enabled for paper pipeline tests, futures enabled). citeturn8view0turn18view1  

In an agentic lifecycle, docs are not just for humans—agents will ingest them as “policy.” Drift becomes a bug multiplier.

## Validation principles to encode based on robust quantitative methodology

Your plan references Ernest Chan’s approach: hypothesis-driven strategy development, careful backtesting, and awareness of biases (look-ahead bias, survivorship bias, data-snooping/overfitting). Chan’s own material emphasizes the importance of avoiding these pitfalls and using out-of-sample testing rather than data-mined “verification.” citeturn15search6turn15search3  

A concrete checklist to encode as an automated “promotion gate”:

- **Bias controls**: enforce causal feature availability at decision time and ensure your backtester doesn’t leak future data (hard requirement). citeturn15search6  
- **Out-of-sample emphasis**: WFO OOS results should be the primary “go/no-go” metric, not in-sample. citeturn0search2turn15search27  
- **Parameter discipline**: heavily parameterized strategies should require proportionally larger datasets / stronger evidence; otherwise they’re optimization theater. citeturn15search6  
- **Cost realism**: fees are modeled in paper executor, but slippage sensitivity must be tested explicitly (your plan calls for +0.1% slippage perturbations, which is sensible). citeturn17view0turn15search27  

If you lean into QuantConnect/Lean for backtesting and live, you also gain brokerage-specific modeling primitives (fees, slippage, buying power models) for Binance spot and futures. citeturn20search2turn20search3  

## Roadmap aligned to the eight-week reference plan

The plan’s weekly structure maps cleanly onto a “two-track” effort: build the agent tools + build the lifecycle gates.

During the first phase, treat the agent as a **research engineer**, not as a “live trader.”

- In the first week, implement the agent’s tool layer and experiment memory (QuantConnect backtest/optimization APIs or Lean CLI wrappers), and add persistent experiment logging in your DB. citeturn14search0turn14search23turn17view1  
- In the second week, implement literature ingestion with a strict “defensible hypothesis” schema (human approval required), and store summaries plus structured hypotheses in the experiment DB. (The repo currently has no equivalent subsystem.) citeturn13view1  
- In the third week, implement a code generation workflow that outputs either QuantConnect algorithm files or repo-native `BaseStrategy` subclasses, then runs baseline backtests with realistic Binance fees. citeturn0search1turn21view2turn20search2  
- In the mid-phase, implement automated WFO windows and Monte Carlo gates; QuantConnect explicitly supports WFO as a concept and provides optimization tooling and analysis paths. citeturn0search2turn14search12turn14search1  
- In the final pre-live phase, integrate monitoring: compare paper/live equity curves vs. WFO expectations, and automate monthly re-optimization (but only promote if stability improves). Your repo already has the telemetry plumbing—this is largely “new logic + new tables.” citeturn19view0turn13view1turn17view1  

## Risks, limitations, and why “risk-first automation” must be non-negotiable

Recent research on autonomous trading agents consistently highlights a hard truth: general LLM capability does not automatically translate into profitable or safe trading behavior, and risk control is a determinant of cross-market robustness. citeturn1academia23  

This is why your plan’s “strict risk limits + stop trading + human-in-the-loop approvals” is not bureaucracy—it’s survival. Your repo already has the beginnings of that stance (paper mode defaults; risk manager kill switch; Telegram reset workflow). citeturn21view1turn16view0turn13view1  

If you implement the full agentic pipeline, treat the agent as a collection of specialized roles (planner/orchestrator/backtest/risk/execution/memory), which is exactly how recent agentic finance orchestration frameworks model the system. citeturn1academia22  

The blunt takeaway: keep the “AI” out of direct execution authority until it has earned the privilege through repeatable WFO + robustness gates—and even then, keep a kill switch within arm’s reach (preferably one that doesn’t require begging the bot to behave). citeturn16view0turn13view1