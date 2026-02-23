from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import logging
import math
import random
from typing import Protocol

try:
    from src.utils.logger import get_logger
except ModuleNotFoundError:

    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)


class _FallbackEnv:
    metadata: dict[str, object] = {}


try:
    import gymnasium as gym
    from gymnasium import spaces
except ImportError:
    try:
        import gym
        from gym import spaces
    except ImportError:
        gym = None

        class _Discrete:
            def __init__(self, n: int) -> None:
                self.n = n

            def sample(self) -> int:
                return random.randrange(self.n)

        class _Box:
            def __init__(
                self,
                low: float,
                high: float,
                shape: tuple[int, ...],
                dtype: type[float] = float,
            ) -> None:
                self.low = low
                self.high = high
                self.shape = shape
                self.dtype = dtype

        class _Spaces:
            Box = _Box
            Discrete = _Discrete

        spaces = _Spaces()

_EnvBase = gym.Env if gym is not None else _FallbackEnv


class BacktestReader(Protocol):
    async def fetch_range(
        self,
        symbol: str,
        timeframe: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, float]]: ...


def _dot(lhs: Sequence[float], rhs: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(lhs, rhs, strict=False))


def _softmax(logits: Sequence[float]) -> list[float]:
    max_logit = max(logits)
    shifted = [math.exp(value - max_logit) for value in logits]
    denom = sum(shifted)
    if denom <= 0:
        return [1.0 / len(logits)] * len(logits)
    return [value / denom for value in shifted]


def _parse_periods_per_year(timeframe: str) -> float:
    suffix = timeframe[-1].lower() if timeframe else "m"
    numeric = timeframe[:-1] if len(timeframe) > 1 else "1"
    count = float(numeric) if numeric and numeric.isdigit() else 1.0
    if count <= 0:
        count = 1.0

    if suffix == "m":
        period_minutes = count
    elif suffix == "h":
        period_minutes = count * 60
    elif suffix == "d":
        period_minutes = count * 60 * 24
    else:
        period_minutes = count

    minutes_per_year = 365.0 * 24.0 * 60.0
    return max(minutes_per_year / period_minutes, 1.0)


def _compute_sharpe(returns: Sequence[float], periods_per_year: float) -> float:
    if len(returns) < 2:
        return 0.0

    mean_return = sum(returns) / len(returns)
    variance = sum((value - mean_return) ** 2 for value in returns) / len(returns)
    std_return = math.sqrt(variance)
    if std_return <= 1e-12:
        return 0.0
    sharpe = (mean_return / std_return) * math.sqrt(periods_per_year)
    return max(min(sharpe, 10.0), -10.0)


def _safe_float(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _reset_env(env: object) -> tuple[list[float], dict[str, float]]:
    result = getattr(env, "reset")()
    if isinstance(result, tuple) and len(result) == 2:
        state, info = result
        return list(state), dict(info)
    return list(result), {}


def _step_env(
    env: object, action: int
) -> tuple[list[float], float, bool, bool, dict[str, float]]:
    result = getattr(env, "step")(action)
    if isinstance(result, tuple) and len(result) == 5:
        next_state, reward, terminated, truncated, info = result
        return (
            list(next_state),
            float(reward),
            bool(terminated),
            bool(truncated),
            dict(info),
        )

    next_state, reward, done, info = result
    return list(next_state), float(reward), bool(done), False, dict(info)


class TradingGymEnv(_EnvBase):
    metadata = {"render_modes": ["human"]}

    def __init__(
        self,
        prices: Sequence[float],
        *,
        initial_capital: float = 10_000.0,
        fee_rate: float = 0.001,
        sharpe_window: int = 30,
        timeframe: str = "1m",
    ) -> None:
        if len(prices) < 2:
            raise ValueError("TradingGymEnv requires at least 2 prices")

        self._prices = [float(price) for price in prices]
        self._initial_capital = float(initial_capital)
        self._fee_rate = float(fee_rate)
        self._sharpe_window = max(int(sharpe_window), 2)
        self._periods_per_year = _parse_periods_per_year(timeframe)

        self.action_space = spaces.Discrete(3)
        self.observation_space = spaces.Box(
            low=-float("inf"), high=float("inf"), shape=(3,), dtype=float
        )

        self._logger = get_logger(self.__class__.__name__)

        self._step_index = 0
        self._position = 0
        self._units = 0.0
        self._cash = self._initial_capital
        self._equity = self._initial_capital
        self._trade_count = 0
        self._rolling_returns: deque[float] = deque(maxlen=self._sharpe_window)
        self._previous_sharpe = 0.0

    def reset(
        self,
        seed: int | None = None,
        options: Mapping[str, object] | None = None,
    ) -> tuple[list[float], dict[str, float]]:
        del options
        if seed is not None:
            random.seed(seed)

        self._step_index = 0
        self._position = 0
        self._units = 0.0
        self._cash = self._initial_capital
        self._equity = self._initial_capital
        self._trade_count = 0
        self._rolling_returns.clear()
        self._previous_sharpe = 0.0

        price = self._prices[self._step_index]
        state = self._build_state(price)
        info = self._build_info(sharpe=0.0)
        return state, info

    def step(
        self, action: int
    ) -> tuple[list[float], float, bool, bool, dict[str, float]]:
        if action not in (0, 1, 2):
            raise ValueError(f"invalid action={action}; expected 0/1/2")

        current_price = self._prices[self._step_index]
        previous_equity = self._equity

        self._apply_action(action, current_price)
        self._step_index += 1

        terminated = self._step_index >= len(self._prices) - 1
        if terminated:
            self._step_index = len(self._prices) - 1

        next_price = self._prices[self._step_index]

        if terminated and self._position == 1:
            self._close_long(next_price)

        self._equity = self._cash + self._units * next_price
        period_return = (
            (self._equity - previous_equity) / previous_equity
            if previous_equity > 0
            else 0.0
        )
        self._rolling_returns.append(period_return)

        current_sharpe = _compute_sharpe(self._rolling_returns, self._periods_per_year)
        reward = max(min(current_sharpe - self._previous_sharpe, 2.0), -2.0)
        self._previous_sharpe = current_sharpe

        state = self._build_state(next_price)
        info = self._build_info(sharpe=current_sharpe)
        return state, reward, terminated, False, info

    def render(self) -> None:
        self._logger.info(
            f"step={self._step_index} equity={self._equity:.2f} position={self._position} trades={self._trade_count}"
        )

    @property
    def final_equity(self) -> float:
        return self._equity

    @property
    def initial_capital(self) -> float:
        return self._initial_capital

    @property
    def trades(self) -> int:
        return self._trade_count

    def _apply_action(self, action: int, price: float) -> None:
        if action == 1 and self._position == 0:
            self._open_long(price)
            return

        if action == 2 and self._position == 1:
            self._close_long(price)

    def _open_long(self, price: float) -> None:
        qty = (self._cash * (1.0 - self._fee_rate)) / price
        cost = qty * price
        fee = cost * self._fee_rate

        self._cash -= cost + fee
        self._units = qty
        self._position = 1

    def _close_long(self, price: float) -> None:
        revenue = self._units * price
        fee = revenue * self._fee_rate
        self._cash += revenue - fee

        self._units = 0.0
        self._position = 0
        self._trade_count += 1

    def _build_state(self, price: float) -> list[float]:
        pnl = self._equity - self._initial_capital
        return [price, float(self._position), pnl]

    def _build_info(self, sharpe: float) -> dict[str, float]:
        return {
            "equity": self._equity,
            "position": float(self._position),
            "pnl": self._equity - self._initial_capital,
            "sharpe": sharpe,
            "trades": float(self._trade_count),
        }


@dataclass
class PPOConfig:
    epochs: int = 50
    gamma: float = 0.99
    gae_lambda: float = 0.95
    clip_epsilon: float = 0.2
    policy_lr: float = 0.01
    value_lr: float = 0.02
    update_epochs: int = 8
    entropy_coef: float = 0.001
    seed: int | None = 7


@dataclass
class _Rollout:
    states: list[list[float]]
    actions: list[int]
    rewards: list[float]
    values: list[float]
    log_probs: list[float]
    dones: list[bool]


class PPOBaselineAgent:
    def __init__(
        self,
        *,
        state_dim: int = 3,
        action_dim: int = 3,
        config: PPOConfig | None = None,
    ) -> None:
        self._config = config or PPOConfig()
        self._state_dim = state_dim
        self._action_dim = action_dim
        self._rng = random.Random(self._config.seed)
        self._logger = get_logger(self.__class__.__name__)

        self._policy_w = [
            [self._rng.uniform(-0.01, 0.01) for _ in range(state_dim)]
            for _ in range(action_dim)
        ]
        self._policy_b = [0.0 for _ in range(action_dim)]

        self._value_w = [self._rng.uniform(-0.01, 0.01) for _ in range(state_dim)]
        self._value_b = 0.0

    def train(self, env: TradingGymEnv) -> list[float]:
        episode_rewards: list[float] = []

        for epoch in range(self._config.epochs):
            rollout, total_reward = self._collect_rollout(env)
            advantages, returns = self._compute_gae(rollout)
            self._update_parameters(rollout, advantages, returns)
            episode_rewards.append(total_reward)

            if (epoch + 1) % 10 == 0:
                self._logger.info(
                    f"PPO epoch {epoch + 1}/{self._config.epochs} reward={total_reward:.4f}"
                )

        return episode_rewards

    def act(self, state: Sequence[float], deterministic: bool = True) -> int:
        probs = self._policy_probs(self._to_features(state))
        if deterministic:
            return max(range(self._action_dim), key=lambda index: probs[index])
        return self._sample_action(probs)

    def _collect_rollout(self, env: TradingGymEnv) -> tuple[_Rollout, float]:
        state, _ = _reset_env(env)
        states: list[list[float]] = []
        actions: list[int] = []
        rewards: list[float] = []
        values: list[float] = []
        log_probs: list[float] = []
        dones: list[bool] = []

        done = False
        truncated = False
        total_reward = 0.0

        while not (done or truncated):
            features = self._to_features(state)
            probs = self._policy_probs(features)
            action = self._sample_action(probs)
            log_prob = math.log(max(probs[action], 1e-12))
            value = self._value(features)

            next_state, reward, done, truncated, _ = _step_env(env, action)

            states.append(features)
            actions.append(action)
            rewards.append(reward)
            values.append(value)
            log_probs.append(log_prob)
            dones.append(done or truncated)

            total_reward += reward
            state = next_state

        rollout = _Rollout(
            states=states,
            actions=actions,
            rewards=rewards,
            values=values,
            log_probs=log_probs,
            dones=dones,
        )
        return rollout, total_reward

    def _compute_gae(self, rollout: _Rollout) -> tuple[list[float], list[float]]:
        count = len(rollout.rewards)
        advantages = [0.0] * count
        returns = [0.0] * count

        next_value = 0.0
        gae = 0.0

        for idx in range(count - 1, -1, -1):
            non_terminal = 0.0 if rollout.dones[idx] else 1.0
            delta = (
                rollout.rewards[idx]
                + self._config.gamma * next_value * non_terminal
                - rollout.values[idx]
            )
            gae = (
                delta
                + self._config.gamma * self._config.gae_lambda * non_terminal * gae
            )
            advantages[idx] = gae
            returns[idx] = rollout.values[idx] + gae
            next_value = rollout.values[idx]

        advantages = [max(min(value, 50.0), -50.0) for value in advantages]

        mean_adv = sum(advantages) / len(advantages) if advantages else 0.0
        variance_adv = (
            sum((value - mean_adv) ** 2 for value in advantages) / len(advantages)
            if advantages
            else 0.0
        )
        std_adv = math.sqrt(variance_adv)
        if std_adv > 1e-12:
            advantages = [(value - mean_adv) / std_adv for value in advantages]

        return advantages, returns

    def _update_parameters(
        self,
        rollout: _Rollout,
        advantages: Sequence[float],
        returns: Sequence[float],
    ) -> None:
        indices = list(range(len(rollout.states)))

        for _ in range(self._config.update_epochs):
            self._rng.shuffle(indices)
            for idx in indices:
                state = rollout.states[idx]
                action = rollout.actions[idx]
                old_log_prob = rollout.log_probs[idx]
                advantage = advantages[idx]
                target_return = returns[idx]

                probs = self._policy_probs(state)
                selected_prob = max(probs[action], 1e-12)
                new_log_prob = math.log(selected_prob)
                ratio = math.exp(new_log_prob - old_log_prob)

                clip_min = 1.0 - self._config.clip_epsilon
                clip_max = 1.0 + self._config.clip_epsilon
                unclipped_obj = ratio * advantage
                clipped_obj = min(max(ratio, clip_min), clip_max) * advantage

                use_unclipped_gradient = False
                if advantage >= 0 and unclipped_obj <= clipped_obj:
                    use_unclipped_gradient = True
                if advantage < 0 and unclipped_obj >= clipped_obj:
                    use_unclipped_gradient = True

                grad_scale = advantage * ratio if use_unclipped_gradient else 0.0
                grad_scale = max(min(grad_scale, 20.0), -20.0)

                for action_idx in range(self._action_dim):
                    indicator = 1.0 if action_idx == action else 0.0
                    policy_grad = grad_scale * (indicator - probs[action_idx])
                    entropy_pull = self._config.entropy_coef * (
                        (1.0 / self._action_dim) - probs[action_idx]
                    )
                    total_grad = policy_grad + entropy_pull

                    for state_idx in range(self._state_dim):
                        self._policy_w[action_idx][state_idx] += (
                            self._config.policy_lr * total_grad * state[state_idx]
                        )
                    self._policy_b[action_idx] += self._config.policy_lr * total_grad

                value_pred = self._value(state)
                value_error = value_pred - target_return
                value_error = max(min(value_error, 50.0), -50.0)
                for state_idx in range(self._state_dim):
                    self._value_w[state_idx] -= (
                        self._config.value_lr * value_error * state[state_idx]
                    )
                self._value_b -= self._config.value_lr * value_error

    def _policy_probs(self, state: Sequence[float]) -> list[float]:
        logits = [
            _dot(weights, state) + bias
            for weights, bias in zip(self._policy_w, self._policy_b, strict=False)
        ]
        return _softmax(logits)

    def _sample_action(self, probs: Sequence[float]) -> int:
        sample = self._rng.random()
        cumulative = 0.0
        for idx, prob in enumerate(probs):
            cumulative += prob
            if sample <= cumulative:
                return idx
        return len(probs) - 1

    def _value(self, state: Sequence[float]) -> float:
        return _dot(self._value_w, state) + self._value_b

    def _to_features(self, raw_state: Sequence[float]) -> list[float]:
        price = _safe_float(raw_state[0])
        position = _safe_float(raw_state[1])
        pnl = _safe_float(raw_state[2])

        price_feature = math.log1p(max(price, 0.0)) / 10.0
        pnl_feature = math.tanh(pnl / 1_000.0)
        return [price_feature, position, pnl_feature]


@dataclass
class PaperTestMetrics:
    final_equity: float
    total_return_pct: float
    sharpe_ratio: float
    trades: int


@dataclass
class PaperTestComparison:
    rl: PaperTestMetrics
    buy_hold: PaperTestMetrics
    outperformed: bool
    delta_return_pct: float
    delta_sharpe: float
    training_curve: list[float]


def prices_from_backtest_rows(rows: Sequence[Mapping[str, object]]) -> list[float]:
    prices: list[float] = []
    for row in rows:
        if "close_price" not in row:
            raise ValueError("backtest row missing close_price")
        prices.append(_safe_float(row["close_price"]))

    if len(prices) < 2:
        raise ValueError("paper test requires at least 2 rows")

    return prices


def evaluate_agent(env: TradingGymEnv, agent: PPOBaselineAgent) -> PaperTestMetrics:
    state, _ = _reset_env(env)
    done = False
    truncated = False
    final_sharpe = 0.0

    while not (done or truncated):
        action = agent.act(state, deterministic=True)
        next_state, _reward, done, truncated, info = _step_env(env, action)
        state = next_state
        final_sharpe = info.get("sharpe", final_sharpe)

    final_equity = env.final_equity
    total_return_pct = (
        (final_equity - env.initial_capital) / env.initial_capital
    ) * 100
    return PaperTestMetrics(
        final_equity=final_equity,
        total_return_pct=total_return_pct,
        sharpe_ratio=final_sharpe,
        trades=env.trades,
    )


def evaluate_buy_hold(
    prices: Sequence[float],
    *,
    initial_capital: float,
    fee_rate: float,
    timeframe: str,
) -> PaperTestMetrics:
    if len(prices) < 2:
        raise ValueError("buy-hold requires at least 2 prices")

    qty = (initial_capital * (1.0 - fee_rate)) / prices[0]
    cash = initial_capital - (qty * prices[0] * (1.0 + fee_rate))

    equity_curve = [initial_capital]
    for price in prices[1:]:
        equity_curve.append(cash + qty * price)

    revenue = qty * prices[-1]
    final_equity = cash + revenue - (revenue * fee_rate)

    returns = []
    prev = equity_curve[0]
    for equity in equity_curve[1:]:
        returns.append((equity - prev) / prev if prev > 0 else 0.0)
        prev = equity

    sharpe = _compute_sharpe(returns, _parse_periods_per_year(timeframe))
    total_return_pct = ((final_equity - initial_capital) / initial_capital) * 100

    return PaperTestMetrics(
        final_equity=final_equity,
        total_return_pct=total_return_pct,
        sharpe_ratio=sharpe,
        trades=1,
    )


def paper_test_from_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    initial_capital: float = 10_000.0,
    fee_rate: float = 0.001,
    timeframe: str = "1m",
    sharpe_window: int = 30,
    ppo_config: PPOConfig | None = None,
) -> PaperTestComparison:
    prices = prices_from_backtest_rows(rows)

    train_env = TradingGymEnv(
        prices,
        initial_capital=initial_capital,
        fee_rate=fee_rate,
        sharpe_window=sharpe_window,
        timeframe=timeframe,
    )
    agent = PPOBaselineAgent(config=ppo_config)
    training_curve = agent.train(train_env)

    eval_env = TradingGymEnv(
        prices,
        initial_capital=initial_capital,
        fee_rate=fee_rate,
        sharpe_window=sharpe_window,
        timeframe=timeframe,
    )
    rl_metrics = evaluate_agent(eval_env, agent)

    buy_hold_metrics = evaluate_buy_hold(
        prices,
        initial_capital=initial_capital,
        fee_rate=fee_rate,
        timeframe=timeframe,
    )

    return PaperTestComparison(
        rl=rl_metrics,
        buy_hold=buy_hold_metrics,
        outperformed=rl_metrics.total_return_pct > buy_hold_metrics.total_return_pct,
        delta_return_pct=rl_metrics.total_return_pct
        - buy_hold_metrics.total_return_pct,
        delta_sharpe=rl_metrics.sharpe_ratio - buy_hold_metrics.sharpe_ratio,
        training_curve=training_curve,
    )


async def paper_test_vs_buy_hold(
    reader: BacktestReader,
    *,
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    initial_capital: float = 10_000.0,
    fee_rate: float = 0.001,
    sharpe_window: int = 30,
    ppo_config: PPOConfig | None = None,
) -> PaperTestComparison:
    start_dt = datetime.fromisoformat(start_date)
    end_dt = datetime.fromisoformat(end_date)

    rows = await reader.fetch_range(symbol, timeframe, start_dt, end_dt)
    if not rows:
        raise ValueError("No backtest rows returned for RL paper test")

    return paper_test_from_rows(
        rows,
        initial_capital=initial_capital,
        fee_rate=fee_rate,
        timeframe=timeframe,
        sharpe_window=sharpe_window,
        ppo_config=ppo_config,
    )
