# 12 Strategic Prompt Templates for Trading Agent Development

This document contains 12 prompt templates for building a production-grade quantitative trading system. Organized by prompt number with original and elaborated versions.

---

## 1. Time Series Forecasting Model

**Original prompt**
You are a Quantitative Researcher at Goldman Sachs Global Markets. I need a complete time series forecasting model for [STOCK/ASSET].
Please provide:

Data preprocessing: How to clean price data and handle missing values
Feature engineering: Technical indicators (moving averages, RSI, MACD, Bollinger Bands)
Model selection: Compare ARIMA, LSTM neural networks, and Prophet models
Training approach: Train-test split ratios and cross-validation strategy
Performance metrics: MAE, RMSE, directional accuracy for predictions
Backtesting framework: How to test strategy on historical data
Risk management: Stop-loss rules and position sizing based on confidence
Implementation code: Python pseudocode with library recommendations

Format as quantitative research report with model specifications and expected accuracy.
Asset: [DESCRIBE STOCK/CRYPTO/COMMODITY, TIME PERIOD, DATA SOURCE]

**Elaborated & enhanced version**
You are a senior Quantitative Researcher at Goldman Sachs Global Markets desk with 12+ years building production forecasting systems. Produce a comprehensive, professional-grade time series forecasting research report for trading [STOCK/ASSET].
Act as if this document will be presented to the head of the systematic trading desk — be rigorous, skeptical, and data-driven. Include realistic caveats about non-stationarity, regime shifts, and look-ahead bias.
Required sections (in this exact order):

Executive summary — one-paragraph overview of recommended approach + expected out-of-sample directional accuracy
Data sourcing & preprocessing pipeline (Yahoo Finance / Polygon / Binance API / etc.; cleaning logic for splits/dividends/outliers; forward-fill vs interpolation vs drop for gaps; handling extreme events)
Feature engineering blueprint (at least 12–15 indicators: include lagged returns, volatility clustering features, Fourier transforms for cycle detection, calendar effects, external regressors if relevant)
Model comparison table (ARIMA/SARIMAX, Prophet with regressors, LSTM/GRU with attention, Transformer-based time series like Informer or Temporal Fusion Transformer; include pros/cons, expected compute cost, overfitting risk)
Recommended model architecture + hyperparameters to start with (layers, units, dropout, learning rate schedule, early stopping)
Training & validation methodology (chronological 70/15/15 split or expanding/rolling window CV; walk-forward optimization; purging & embargo to prevent leakage)
Evaluation metrics dashboard (point-forecast: MAE/RMSE/MAPE; directional: accuracy + Matthews correlation; probabilistic calibration if applicable; economic significance: simulated PnL before costs)
Realistic backtest specification (vectorized vs event-driven; realistic slippage & commission assumptions; out-of-sample period performance by regime — bull / bear / choppy)
Risk overlay rules (confidence-based sizing using prediction intervals; volatility targeting; hard stop-loss & trailing mechanisms; maximum adverse excursion limits)
Production pseudocode skeleton (pandas → numpy → scikit-learn / statsmodels / tensorflow-pytorch / prophet / darts / gluonts; modular functions for retraining & inference)
Key risks & failure modes (non-stationarity, structural breaks, fat tails, correlation to macro surprises) + mitigation strategies

Output in clean, numbered Markdown report format suitable for internal Goldman distribution. Replace placeholders: Asset = [e.g. BTC/USD, 2018–2025 daily closes from Binance API]

---

## 2. Mean Reversion Trading Strategy

**Original prompt** (short version shown earlier)

**Elaborated version**
You are a VP of Systematic Trading at JP Morgan's high-capacity mean-reversion pod. Create a production-ready mean reversion / statistical arbitrage strategy specification document for [MARKET/ASSET or PAIR].
Write as if submitting to the portfolio committee — include mathematical rigor, edge decay awareness, and capacity estimates.
Must contain:

Core statistical foundation (Z-score on rolling window, Ornstein–Uhlenbeck process parameters if applicable, half-life estimation)
Entry filter logic (deviation threshold + confirmation signals like volume surge or RSI divergence; avoid during high-volatility regimes)
Exit logic hierarchy (primary: reversion to mean; secondary: time-based stop, profit target, hard stop-loss)
Pair / basket selection methodology (correlation > 0.8 + cointegration rank + sector / ETF membership filter + minimum ADV filter)
Cointegration & stationarity validation pipeline (Engle-Granger, Johansen test, ADF on residuals, Hurst exponent < 0.5)
Position sizing & leverage rules (Kelly / fractional Kelly, volatility parity, dollar-neutral construction, max notional per pair)
Risk & capacity envelope (max drawdown target 8–12%, VaR contribution limit, theoretical AUM capacity before 50% edge decay)
Simulated historical performance table (2015–2025, Sharpe, Calmar, win rate, avg hold time, worst streak) under realistic costs

Format as professional strategy memo with clear entry/exit pseudocode.
Market example: [e.g. US large-cap ETF pairs on 15-min bars, intraday mean reversion]

---

## 3. Sentiment Analysis Trading Model

**Original prompt**
You are a Machine Learning Engineer at Citadel's NLP trading team. I need a sentiment-based trading model for [STOCKS/SECTOR].
Please provide:

Data sources: Twitter, Reddit, news APIs, earnings call transcripts
Sentiment scoring: How to rate text as bullish/neutral/bearish (-1 to +1 scale)
NLP preprocessing: Tokenization, stop word removal, entity recognition
Model architecture: BERT, FinBERT, or custom transformer for financial text
Signal generation: How sentiment changes trigger buy/sell decisions
Volume weighting: Adjusting for tweet/article volume and source credibility
Lag analysis: Time delay between sentiment spike and price movement
Performance tracking: Correlation between sentiment and actual returns

Format as machine learning model specification with training pipeline.
Sector: [DESCRIBE STOCKS, SENTIMENT SOURCES, TARGET RETURNS]

**Elaborated & enhanced version**
You are a senior Machine Learning Engineer at Citadel's NLP trading team with 10+ years developing production sentiment systems. Produce a comprehensive, professional-grade sentiment analysis trading model research report for [STOCKS/SECTOR].
Act as if this document will be reviewed by the chief data officer — be precise, evidence-based, and highlight scalability issues like API rate limits and data freshness. Include caveats on sarcasm detection, fake news, and pump-and-dump schemes.
Required sections (in this exact order):

Executive summary — one-paragraph overview of recommended pipeline + expected sentiment-return correlation
Data sourcing & ingestion pipeline (X, Reddit via Pushshift/PSAWR, news from Alpha Vantage/NewsAPI, transcripts from Seeking Alpha; real-time streaming vs batch; filtering for relevance using keywords/entities)
NLP preprocessing workflow (lowercasing, tokenization with spaCy/Transformers, lemmatization, stop words, NER for companies/people, handling emojis/slang/abbreviations in social data)
Sentiment scoring methodology (fine-tuned FinBERT/Llama-finance, VADER for baseline, ensemble scoring; calibration to -1/+1 scale; handling multilingual text if global)
Model architecture comparison (BERT base vs FinBERT vs RoBERTa-fin; layers, attention heads, fine-tuning strategy; hybrid with classical ML for interpretability)
Feature engineering additions (sentiment momentum, virality score based on retweets/upvotes, source credibility weighting e.g. blue-check vs anon; topic modeling with LDA)
Signal generation & trading logic (thresholds for delta-sentiment triggers; combining with price/volume filters; long/short signals with confidence bands)
Lag & causality analysis (Granger tests, cross-correlation plots; optimal delay windows per source e.g. 15-min for X, 1-day for news)
Training & validation setup (labeled datasets like StockTwits/SemEval; time-series CV to avoid leakage; hyperparam tuning with Optuna/Bayesian)
Performance metrics dashboard (sentiment accuracy/F1; trading metrics: Sharpe, hit rate, alpha vs benchmark; ablation studies on features)
Backtest & simulation framework (vectorized with pandas/zipline; incorporate latency, costs; regime-specific performance e.g. earnings season)
Risk & deployment considerations (overfitting to hype cycles, adversarial attacks on sentiment; monitoring for drift; API failover, cloud scaling)
Production pseudocode skeleton (huggingface transformers → pytorch → integration with trading engine; functions for inference & alerting)

Output in clean, numbered Markdown report format suitable for internal Citadel distribution. Replace placeholders: Sector = [e.g. Tech stocks, X/Reddit/news, 5-10% monthly alpha]

---

## 4. Portfolio Optimization Algorithm

**Original prompt**
You are a Portfolio Manager at BlackRock's Systematic Strategies group. I need a portfolio optimization model for [ASSET UNIVERSE].
Please provide:

Modern Portfolio Theory: Efficient frontier calculation with mean-variance optimization
Sharpe ratio maximization: Finding optimal risk-adjusted return portfolio
Constraints definition: Sector limits, individual position caps, liquidity requirements
Covariance matrix: How assets move together (correlation and volatility)
Rebalancing rules: When and how much to adjust positions
Transaction costs: Incorporating trading fees and slippage into optimization
Risk budgeting: Allocating risk across assets based on contribution to portfolio variance
Scenario testing: How portfolio performs in market crash, rally, or sideways conditions

Format as portfolio construction framework with allocation percentages.
Portfolio: [DESCRIBE ASSETS, RISK TOLERANCE, CONSTRAINTS]

**Elaborated & enhanced version**
You are a senior Portfolio Manager at BlackRock's Systematic Strategies group with 15+ years optimizing multi-billion AUM portfolios. Produce a comprehensive, professional-grade portfolio optimization research report for [ASSET UNIVERSE].
Act as if presenting to the investment committee — be quantitative, conservative, and address diversification failures in correlated crashes. Include realistic notes on estimation errors in covariances and return forecasts.
Required sections (in this exact order):

Executive summary — one-paragraph overview of optimal allocation + expected Sharpe
Asset universe definition & data pipeline (e.g. S&P500 stocks + bonds + alts from Yahoo/Quandl; filtering for liquidity/ADV > $10M; handling survivorship bias)
Return & risk estimation methods (historical means vs CAPM/FF factors; shrinkage estimators for covariance like Ledoit-Wolf; robust to outliers)
Optimization framework comparison (mean-variance, Black-Litterman, robust/resampled, min-variance; include CVaR for tail risks)
Objective functions (Sharpe max, utility functions for risk aversion; multi-objective Pareto front)
Constraints & regularization (no-short, leverage limits, turnover caps, ESG filters, cardinality for sparse portfolios)
Risk budgeting & decomposition (marginal contribution to risk, Euler allocation; parity vs hierarchical)
Rebalancing strategy (threshold-based on deviation, calendar e.g. quarterly; tax-aware for individuals)
Transaction cost modeling (linear/quadratic impact functions; effective spread estimates)
Scenario & stress testing (Monte Carlo simulations, historical replays like 1987/2000/2008/2020; factor shocks)
Performance attribution (Brinson model; factor exposures over time)
Production pseudocode skeleton (cvxpy/scipy.optimize → pandas; modular for what-if analysis)
Key risks & mitigations (parameter instability, model risk, liquidity dries; diversification diagnostics)

Output in clean, numbered Markdown report format suitable for internal BlackRock distribution. Replace placeholders: Portfolio = [e.g. Global equities + fixed income, moderate risk, no leverage]

---

## 5. Machine Learning Feature Selection

**Original prompt**
You are a Senior Quant at Two Sigma's Research Platform. I need a feature engineering pipeline for [TRADING STRATEGY].
Please provide:

Raw features: Price, volume, volatility, bid-ask spread, market depth
Derived features: Returns, log returns, rolling statistics, momentum indicators
Alternative data: Satellite imagery, web traffic, credit card transactions
Feature importance: Which variables actually predict price movements
Dimensionality reduction: PCA or factor models to reduce feature count
Feature correlation: Removing redundant features that don't add information
Forward-looking bias: Ensuring no data leakage from future into training
Feature stability: Which features remain predictive across different market regimes

Format as feature engineering documentation with correlation matrix.
Strategy: [DESCRIBE TRADING APPROACH, PREDICTION TARGET, DATA AVAILABLE]

**Elaborated & enhanced version**
You are a senior Quant at Two Sigma's Research Platform with 12+ years curating alpha-generating features. Produce a comprehensive, professional-grade feature engineering pipeline report for [TRADING STRATEGY].
Act as if submitting for alpha review — be empirical, skeptical of spurious correlations, and quantify information content. Include notes on data vendor costs, refresh rates, and legal/ethical use of alts.
Required sections (in this exact order):

Executive summary — one-paragraph overview of top features + expected predictive power
Raw data inventory (OHLCV, L2 orderbook from TAQ/Polygon, fundamentals from Compustat; cleaning for errors/splits)
Derived feature catalog (at least 20-30: transforms like FFT/decomp, interactions, normalized ranks; domain-specific e.g. order imbalance)
Alternative data integration (geospatial from Orbital Insight, consumer from SimilarWeb/AdvantageData, sentiment from RavenPack; fusion techniques)
Feature selection methods (mutual info, SHAP/XGBoost importance, Boruta; wrapper vs filter vs embedded)
Correlation & multicollinearity analysis (VIF, heatmap; clustering to group redundants)
Dimensionality reduction techniques (PCA/SVD, autoencoders, ICA; target components explaining 95% variance)
Bias prevention protocols (strict time-barriering, embargo periods, synthetic data for testing)
Stability & regime analysis (feature importance drift detection; subperiod tests e.g. pre/post-COVID)
Evaluation metrics (information coefficient, hit rate per feature; orthogonalization benefits)
Production pseudocode skeleton (featuretools/pandas-ta/tsfresh → sklearn; automated pipeline)
Risks & best practices (over-engineering, vendor dependency; privacy regs like GDPR)

Output in clean, numbered Markdown report format suitable for internal Two Sigma distribution. Replace placeholders: Strategy = [e.g. Intraday momentum, next-bar return, tick + alt data]

---

## 6. High-Frequency Trading Signal Detection

**Original prompt**
You are an Algorithmic Trader at Virtu Financial's Market Making desk. I need a microstructure-based signal system for [LIQUID ASSETS].
Please provide:

Order book analysis: Bid-ask spread, depth imbalance, order flow toxicity
Tick data processing: How to handle millisecond-level price updates
Signal triggers: Imbalances, large orders, quote stuffing detection
Execution logic: Market orders vs. limit orders vs. hidden orders
Latency requirements: Infrastructure needs for sub-10ms execution
Slippage estimation: Expected cost of trading at different sizes
Market impact: How your orders move the price and how to minimize it
Profitability calculation: Edge per trade minus costs (commissions, exchange fees)

Format as high-frequency trading playbook with signal specifications.
Assets: [DESCRIBE LIQUID INSTRUMENTS, EXCHANGE, HOLDING PERIOD]

**Elaborated & enhanced version**
You are a senior Algorithmic Trader at Virtu Financial's Market Making desk with 10+ years in HFT. Produce a comprehensive, professional-grade microstructure signal system playbook for [LIQUID ASSETS].
Act as if briefing the execution team — be tactical, aware of adversarial markets, and estimate edge half-life. Include caveats on regulatory scrutiny (e.g. spoofing) and flash crashes.
Required sections (in this exact order):

Executive summary — one-paragraph overview of key signals + expected edge/trade
Data processing pipeline (tick data from SIP/Direct feeds, normalization, timestamp alignment; handling cancels/replaces)
Order book features (L1/L2/L3 metrics: quoted spread, effective spread, VPIN for toxicity, Herfindahl for concentration)
Signal detection algorithms (imbalance ratios, queue position, momentum ignition; ML classifiers for aggressive flow)
Execution venue analysis (dark pools vs lit, maker-taker fees, colocation benefits)
Latency & infra blueprint (FPGA/ASIC vs software, microwave vs fiber, clock sync; <5us tick-to-trade goal)
Impact & slippage models (Almgren-Chriss, square-root law; size-tiered estimates)
Profitability framework (expected value calc: prob(win)*avg gain - costs; breakeven analysis)
Risk controls (inventory limits, adverse selection monitors, circuit breakers)
Backtest & live testing protocol (replay simulators like Nanex, phased rollout)
Production pseudocode skeleton (C++/Python with kdb+/Aerospike; event-driven architecture)
Risks & adaptations (HFT arms race, reg changes like T+1, counterparty risks)

Output in clean, numbered Markdown report format suitable for internal Virtu distribution. Replace placeholders: Assets = [e.g. NASDAQ top 100, NYSE, microseconds]

---

## 7. Risk Management & VaR Model

**Original prompt**
You are a Risk Manager at Morgan Stanley's Quantitative Risk group. I need a Value at Risk model for [PORTFOLIO/STRATEGY].
Please provide:

VaR calculation: Historical simulation, parametric, or Monte Carlo approach
Confidence level: 95% or 99% probability of maximum loss
Time horizon: Daily, weekly, or monthly VaR estimation
Stress testing: How portfolio performs in 2008 crisis, COVID crash scenarios
Expected Shortfall: Average loss when VaR threshold is breached
Greeks calculation: Delta, gamma, vega for options portfolios
Correlation breakdown: How individual positions contribute to total risk
Risk limits: Position limits, leverage caps, concentration restrictions

Format as risk management framework with loss scenario projections.
Portfolio: [DESCRIBE HOLDINGS, LEVERAGE, RISK APPETITE]

**Elaborated & enhanced version**
You are a senior Risk Manager at Morgan Stanley's Quantitative Risk group with 15+ years modeling firm-wide exposures. Produce a comprehensive, professional-grade VaR and risk management framework report for [PORTFOLIO/STRATEGY].
Act as if reporting to the CRO — be conservative, regulatory-compliant (Basel/SEC), and stress non-Gaussian risks. Include notes on model validation and audit trails.
Required sections (in this exact order):

Executive summary — one-paragraph overview of VaR estimates + key vulnerabilities
Portfolio composition & data pipeline (positions from OMS, prices from Bloomberg; aggregation hierarchies)
VaR methodologies comparison (historical filtered, GARCH-parametric, MCMC Monte Carlo; pros/cons, backtesting Kupiec/Christoffersen)
Parameter selection (99% CL for reg, 95% for internal; 1-day/10-day horizons; scaling via square-root-time)
Conditional metrics (CVaR/ES, incremental/marginal VaR per asset)
Sensitivity & Greeks engine (finite differences, AD for speed; higher-order like charm/shadow)
Decomposition & attribution (component VaR, correlation matrix diag; factor-based like PCA)
Stress & scenario suite (historical replays, hypothetical e.g. rate shock +10%; reverse stress)
Limits & controls framework (hard/soft breaches, escalation protocols, diversification scores)
Monitoring & reporting dashboard (daily VaR tracking, exception reports, what-if tools)
Production pseudocode skeleton (riskmetrics/pyrisk → integration with Murex/Calypso)
Model risks & governance (wrong-way risk, procyclicality; annual review, independent validation)

Output in clean, numbered Markdown report format suitable for internal Morgan Stanley distribution. Replace placeholders: Portfolio = [e.g. Equity long/short fund, 2x leverage, conservative]

---

## 8. Options Pricing & Greeks Model

**Original prompt**
You are a Derivatives Trader at Citadel Securities' Options desk. I need an options pricing and hedging model for [UNDERLYING ASSET].
Please provide:

Black-Scholes model: Theoretical price calculation with assumptions
Implied volatility: Extracting market's volatility expectation from option prices
Greeks computation: Delta, gamma, theta, vega, rho for risk management
Volatility smile: How implied vol changes across strike prices
Delta hedging: How many shares to hold to be market-neutral
Gamma scalping: Profiting from volatility through dynamic hedging
Option strategies: Spreads, strangles, iron condors with P&L profiles
Scenario analysis: How position performs if stock moves ±5%, ±10%

Format as options trading manual with pricing formulas and hedge ratios.
Underlying: [DESCRIBE STOCK/INDEX, OPTION TYPE, EXPIRATION]

**Elaborated & enhanced version**
You are a senior Derivatives Trader at Citadel Securities' Options desk with 12+ years pricing complex books. Produce a comprehensive, professional-grade options pricing and hedging manual for [UNDERLYING ASSET].
Act as if training junior traders — be mathematical, practical for desk use, and warn on model breakdowns (e.g. jumps, neg rates). Include liquidity adjustments and bid-ask considerations.
Required sections (in this exact order):

Executive summary — one-paragraph overview of model + typical hedge costs
Pricing models hierarchy (BSM, binomial/CRR tree, stochastic vol like Heston/SABR; Monte Carlo for path-dependents)
Input estimation (spot from feeds, divs from Bloomberg, rates from LIBOR/SOFR, IV from surface fitting)
Greeks & sensitivities calc (analytical formulas, numerical perturbations; cross-Greeks like vanna/volga)
Volatility surface modeling (smile/skew interpolation, local vol duplex; arbitrage-free checks)
Hedging strategies (dynamic delta, min-variance with gamma/vega buckets; frequency & thresholds)
Advanced tactics (gamma trading, dispersion trades, tail hedges with OTM options)
Strategy playbook (at least 8: verticals, calendars, butterflies, collars; payoff diagrams, breakevens)
Scenario & P&L simulation (what-if grids, stress vols +50%; attribution to Greeks)
Risk limits (net Greeks exposures, stress loss caps, concentration by strike/exp)
Production pseudocode skeleton (ql/derivpy → Python; vectorized for books)
Limitations & extensions (fat tails, jumps via Merton; ML for IV prediction)

Output in clean, numbered Markdown report format suitable for internal Citadel distribution. Replace placeholders: Underlying = [e.g. AAPL equity options, American, 1-3 months]

---

## 9. Pairs Trading Cointegration Model

**Original prompt**
You are a Statistical Arbitrage Trader at Renaissance Technologies. I need a pairs trading model for [CORRELATED ASSETS].
Please provide:

Pair selection: Finding stocks that move together historically
Cointegration test: Augmented Dickey-Fuller test for statistical relationship
Spread calculation: Price difference or ratio between the two assets
Z-score threshold: Entry when spread is 2+ standard deviations from mean
Mean reversion speed: Half-life of spread returning to equilibrium
Position sizing: Dollar-neutral or beta-neutral pair construction
Exit rules: Close position when spread returns to mean or hits stop-loss
Risk monitoring: What if cointegration breaks down during holding period

Format as statistical arbitrage strategy with quantitative entry/exit criteria.
Pairs: [DESCRIBE ASSET PAIR, SECTOR, RELATIONSHIP TYPE]

**Elaborated & enhanced version**
You are a senior Statistical Arbitrage Trader at Renaissance Technologies with 15+ years in stat arb. Produce a comprehensive, professional-grade pairs trading model strategy document for [CORRELATED ASSETS].
Act as if for alpha allocation — be statistically rigorous, aware of crowding/decay, and capacity-limited. Include multi-pair basket extensions and regime filters.
Required sections (in this exact order):

Executive summary — one-paragraph overview of strategy + expected Sharpe
Universe screening & pair mining (correlation >0.7, Johansen rank, min vol/ADV; clustering for sectors)
Cointegration framework (Engle-Granger two-step, VECM; p-values, error correction terms)
Spread modeling (log-price ratio vs residuals; rolling windows, Kalman filter for dynamics)
Entry/exit signals (adaptive Z-scores via GARCH, half-life <30 days; profit targets, time stops)
Sizing & neutrality (beta-adjusted, vol-parity; optimal via OU process params)
Performance metrics (win rate, avg convergence time, Sharpe post-costs)
Breakdown detection (CUSUM tests, predictive half-life decay; diversification across 50+ pairs)
Backtest specs (event-driven, transaction costs 5bps, out-of-sample since 2010)
Risk overlay (max drawdown 5%, pair correlation limits, macro filters)
Production pseudocode skeleton (statsmodels/copula → pandas; vectorized for portfolio)
Risks & evolutions (fundamental shifts, HFT competition; ML for pair selection)

Output in clean, numbered Markdown report format suitable for internal Renaissance distribution. Replace placeholders: Pairs = [e.g. KO-PEP beverages, consumer, economic link]

---

## 10. Machine Learning Backtesting Framework

**Original prompt**
You are a Quantitative Developer at AQR Capital's Research Infrastructure team. I need a robust backtesting system for [TRADING STRATEGY].
Please provide:

Data pipeline: Historical price data ingestion and storage
Signal generation: How strategy produces buy/sell/hold decisions
Transaction simulation: Market orders, limit orders, realistic fill assumptions
Cost modeling: Commissions, slippage, market impact, borrowing costs
Performance metrics: Sharpe ratio, max drawdown, win rate, profit factor
Overfitting detection: Walk-forward testing, out-of-sample validation
Regime analysis: How strategy performs in bull, bear, sideways markets
Production readiness: Code structure, error handling, monitoring dashboards

Format as backtesting specification document with validation procedures.
Strategy: [DESCRIBE TRADING LOGIC, UNIVERSE, FREQUENCY]

**Elaborated & enhanced version**
You are a senior Quantitative Developer at AQR Capital's Research Infrastructure team with 10+ years building scalable sims. Produce a comprehensive, professional-grade backtesting framework specification for [TRADING STRATEGY].
Act as if for production handover — be modular, reproducible, and compliant with research standards. Include parallelism for speed and version control integration.
Required sections (in this exact order):

Executive summary — one-paragraph overview of framework + validation rigor
Data pipeline architecture (ingestion from S3/Quandl, storage in HDF5/Parquet; point-in-time to avoid bias)
Signal module specs (input features → predictions; handling multi-asset, async events)
Execution simulator (order types, partial fills, queue models; realistic delays)
Cost & friction layers (variable commissions, Kyle's lambda impact, short borrow rates)
Metrics suite (standard + custom: Sortino, Omega, ulcer index; bootstrapped CIs)
Anti-overfitting protocols (WFO, combinatorial purged CV, deflated Sharpe)
Regime segmentation (HMM for states, performance by VIX quartiles)
Code structure blueprint (OOP with backtrader/zipline extensions; unit tests, logging)
Monitoring & viz (dashboards with Plotly/Grafana; alerts for anomalies)
Production transition (containerization, API endpoints for live)
Limitations & upgrades (stochastic fills, multi-thread safety; GPU accel)

Output in clean, numbered Markdown report format suitable for internal AQR distribution. Replace placeholders: Strategy = [e.g. Factor momentum, global equities, daily]

---

## 11. Reinforcement Learning Trading Agent

**Original prompt**
You are an AI Researcher at JP Morgan's Machine Learning Center of Excellence. I need a reinforcement learning agent for [TRADING TASK].
Please provide:

Environment setup: State space (prices, positions, cash), action space (buy/sell/hold)
Reward function: Profit minus transaction costs minus risk penalty
RL algorithm: Deep Q-Learning, PPO, or Actor-Critic approach
Neural network architecture: Input layers, hidden layers, output layer specifications
Training approach: Episodes, experience replay, exploration vs. exploitation
Hyperparameter tuning: Learning rate, discount factor, batch size optimization
Performance benchmarks: Compare to buy-and-hold and simple moving average strategies
Risk constraints: Maximum position size, drawdown limits built into reward

Format as reinforcement learning project specification with training plan.
Task: [DESCRIBE ASSET, GOAL, TRAINING DATA PERIOD]

**Elaborated & enhanced version**
You are a senior AI Researcher at JP Morgan's Machine Learning Center of Excellence with 8+ years in RL for finance. Produce a comprehensive, professional-grade RL trading agent project specification for [TRADING TASK].
Act as if proposing for funding — be innovative, scalable to multi-asset, and address sample inefficiency. Include ethical AI notes and explainability.
Required sections (in this exact order):

Executive summary — one-paragraph overview of agent + benchmark outperformance
Environment modeling (Gym-compatible: states with tech indicators + macro; discrete/continuous actions)
Reward engineering (sharpe-based, drawdown penalties, multi-objective; shaping for sparse rewards)
Algorithm selection (DQN vs SAC vs TD3; offline RL for safety with historical data)
Network architectures (CNN/LSTM for time-series, transformers; dueling/quantile for distributions)
Training regime (simulated episodes, HER replay, epsilon-greedy decay; parallel envs with Ray)
Hyperparam optimization (grid/ray-tune, Bayesian; sensitivity analysis)
Benchmarks & ablation (vs baselines, random; component tests e.g. without risk term)
Constraints integration (hard via action masking, soft via Lagrangian)
Evaluation & deployment (live paper trading, robustness to sim-real gap)
Production pseudocode skeleton (stable-baselines3/gym → custom; tensorboard logging)
Risks & future work (overfitting to history, adversarial robustness; hybrid with supervised)

Output in clean, numbered Markdown report format suitable for internal JP Morgan distribution. Replace placeholders: Task = [e.g. BTC portfolio management, maximize wealth, 2015-2025]

---

## 12. Factor Investing Model

**Original prompt**
You are a Quantitative Portfolio Manager at AQR's Factor Investing group. I need a multi-factor model for [EQUITY UNIVERSE].
Please provide:

Factor definitions: Value (P/E, P/B), momentum (12-month return), quality (ROE, debt ratio)
Factor scoring: Ranking stocks within universe on each factor
Weight calculation: Combining multiple factors into single composite score
Portfolio construction: Long top quintile, short bottom quintile for each factor
Rebalancing frequency: Monthly, quarterly, or annual turnover
Capacity analysis: How much capital strategy can absorb before returns degrade
Factor timing: When to overweight/underweight certain factors
Attribution analysis: Which factors drove returns in each period

Format as factor investing strategy document with stock rankings.
Universe: [DESCRIBE STOCK UNIVERSE, FACTORS, TARGET RETURN]

**Elaborated & enhanced version**
You are a senior Quantitative Portfolio Manager at AQR's Factor Investing group with 15+ years harvesting premia. Produce a comprehensive, professional-grade multi-factor investing strategy document for [EQUITY UNIVERSE].
Act as if for client pitch — be evidence-based, transparent on fees/decay, and integrate smart beta evolutions. Include diversification across styles and geographies.
Required sections (in this exact order):

Executive summary — one-paragraph overview of model + historical alpha
Factor library (core: value/mom/qual/size/vol; premiums justification via academia/AQR papers)
Scoring & normalization (z-scores, robust winsorization, industry-neutral ranks)
Combination methods (equal-weight, risk-parity, ML like random forest for weights)
Construction rules (quintile/decile sorts, long/short 130/30; min market cap filters)
Rebalancing optimization (turnover minimization, tax-lot aware; calendar vs threshold)
Capacity estimation (slippage curves, AUM where alpha halves; per-factor limits)
Timing overlays (regime indicators like yield curve, sentiment; dynamic allocation)
Attribution framework (Fama-MacBeth regs, time-series decomposition)
Backtest & live metrics (excess returns, tracking error, info ratio; post-publication decay adj)
Production pseudocode skeleton (pandas/qs → integration with Aladdin)
Risks & enhancements (factor crowding, macro betas; ESG integration)

Output in clean, numbered Markdown report format suitable for internal AQR distribution. Replace placeholders: Universe = [e.g. US large cap, value+momentum+quality, 8-12% alpha target]

---

## Quick Reference: Priority Prompts for This Project

Based on the current state of the crypto-agent codebase, the following prompts are most relevant:

| Priority | Prompt | Gap It Addresses |
|----------|--------|------------------|
| **Now** | #2 Mean Reversion | Exit logic hierarchy (primary: reversion, secondary: time-stop, profit target, stop-loss) |
| **Now** | #10 Backtesting | Anti-overfitting protocols (WFO, purged CV, deflated Sharpe) |
| **Now** | #6 HFT/Risk | Risk controls (inventory limits, circuit breakers) |
| **Soon** | #9 Pairs Trading | Cointegration framework for crypto pairs |
| **Later** | #1 Time Series | LSTM/Transformer forecasting (requires substantial infrastructure) |
| **Later** | #11 RL Agent | Requires proven strategy first |

Note: Prompts #3 (Sentiment), #4 (Portfolio Opt), #5 (Feature Selection), #7 (VaR), and #8 (Options) are less relevant for the current crypto momentum strategy focus.
