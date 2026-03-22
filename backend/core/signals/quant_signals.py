"""
Quant Signal Library
═══════════════════════════════════════════════════════
Implements mathematical tools from three books:

1. KALMAN FILTER (Chan Ch.3, Williams Ch.15)
   - Dynamic hedge ratio estimation (Eq 3.5-3.13)
   - Market-making mean model (Eq 3.14-3.20)
   - State: β(t) = β(t-1) + ω(t-1)  [state transition]
   - Obs:   y(t) = x(t)β(t) + ε(t)  [measurement]

2. STATISTICAL TESTS (Chan Ch.2, Platen Ch.1)
   - ADF test for mean reversion
   - Hurst exponent (H < 0.5 → mean reverting)
   - Half-life of mean reversion (Platen Ch.4 OU process)
   - Johansen cointegration test

3. Z-SCORE (Bollinger strategy signals)
   - Rolling Z-score with adaptive lookback
   - Bollinger band levels

Theory:
   Half-life from OU process: ΔY(t) = λY(t-1) + μ + ε
   → half_life = -log(2)/λ  (Platen Eq 4.2.3)
   
   Kalman gain K(t) = R(t|t-1)·x(t) / Q(t)  (Williams Eq 15.9)
═══════════════════════════════════════════════════════
"""
import numpy as np
from typing import Optional, Tuple
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════
#  KALMAN FILTER
# ═══════════════════════════════════════════════════════

@dataclass
class KalmanState:
    """Internal state of the Kalman filter."""
    beta: np.ndarray = field(default_factory=lambda: np.zeros(2))
    P: np.ndarray    = field(default_factory=lambda: np.zeros((2, 2)))
    initialized: bool = False
    update_count: int = 0


class KalmanFilter:
    """
    Kalman filter for dynamic hedge ratio estimation.
    Direct implementation of Chan Ch.3, Williams Ch.15 (Kalman-Bucy).

    Measurement:      y(t) = x(t)·β(t) + ε(t),    Var(ε) = Ve
    State transition: β(t) = β(t-1) + ω(t-1),      Cov(ω) = Vw

    β = [slope, intercept] — 2-dimensional hidden variable
    x = [price_x, 1.0]    — augmented observation vector
    y = price_y            — observable variable

    Parameters
    ----------
    delta : float
        Rate of adaptation. 0.0001 = slow (stable pairs). 0.001 = faster.
        Vw = δ/(1-δ) · I   (Chan's parameterization)
    Ve : float
        Measurement noise variance. 0.001 is a good default.
    """

    def __init__(self, delta: float = 0.0001, Ve: float = 0.001):
        self.delta = delta
        self.Ve    = Ve
        self.Vw    = delta / (1.0 - delta) * np.eye(2)
        self.state = KalmanState()

    def reset(self):
        self.state = KalmanState()

    def update(self, x: float, y: float) -> dict:
        """Process one (x,y) observation. Jump detection via Shreve Ch.11."""
        JUMP_THRESHOLD = 4.0

        prev_beta  = self.state.beta.copy()
        prev_P     = self.state.P.copy()
        prev_count = self.state.update_count

        x_vec     = np.array([x, 1.0])
        beta_pred = self.state.beta
        R_pred    = self.state.P + self.Vw
        y_hat     = float(x_vec @ beta_pred)
        Q         = float(x_vec @ R_pred @ x_vec.T) + self.Ve
        e         = y - y_hat
        K         = (R_pred @ x_vec) / Q

        self.state.beta          = beta_pred + K * e
        self.state.P             = R_pred - np.outer(K, x_vec) @ R_pred
        self.state.initialized   = True
        self.state.update_count += 1

        zscore  = e / np.sqrt(Q) if Q > 0 else 0.0
        # Only detect jumps after filter is warmed up (50+ updates)
        is_jump = abs(zscore) > JUMP_THRESHOLD and self.state.update_count > 50

        if is_jump:
            self.state.beta         = prev_beta
            self.state.P            = prev_P
            self.state.update_count = prev_count
            return {
                "hedge_ratio":    float(prev_beta[0]),
                "mean":           float(y_hat),
                "intercept":      float(prev_beta[1]),
                "forecast_error": float(e),
                "forecast_std":   float(np.sqrt(Q)),
                "zscore":         0.0,
                "kalman_gain":    float(K[0]),
                "updates":        prev_count,
                "is_jump":        True,
                "jump_magnitude": round(abs(zscore), 2),
            }

        return {
            "hedge_ratio":    float(self.state.beta[0]),
            "mean":           float(y_hat),
            "intercept":      float(self.state.beta[1]),
            "forecast_error": float(e),
            "forecast_std":   float(np.sqrt(Q)),
            "zscore":         float(zscore),
            "kalman_gain":    float(K[0]),
            "updates":        self.state.update_count,
            "is_jump":        False,
            "jump_magnitude": 0.0,
        }


    def batch_update(self, x_series: list, y_series: list) -> list:
        """Warm up filter on historical data. Returns list of states."""
        return [self.update(x, y) for x, y in zip(x_series, y_series)]

    def to_dict(self) -> dict:
        """Serialize state for Redis persistence."""
        return {
            "beta":  self.state.beta.tolist(),
            "P":     self.state.P.tolist(),
            "count": self.state.update_count,
            "delta": self.delta,
            "Ve":    self.Ve,
        }

    def from_dict(self, d: dict):
        """Restore state from Redis."""
        self.state.beta          = np.array(d["beta"])
        self.state.P             = np.array(d["P"])
        self.state.update_count  = d.get("count", 0)
        self.state.initialized   = True
        self.delta               = d.get("delta", self.delta)
        self.Ve                  = d.get("Ve", self.Ve)
        self.Vw                  = self.delta / (1.0 - self.delta) * np.eye(2)


# ═══════════════════════════════════════════════════════
#  ROLLING Z-SCORE
# ═══════════════════════════════════════════════════════

class ZScoreCalculator:
    """
    Rolling Z-score: z = (x - μ) / σ
    Used for Bollinger Band entry/exit signals.
    """

    def __init__(self, lookback: int = 20):
        self.lookback = lookback
        self._buf: list = []

    def update(self, value: float) -> Optional[float]:
        self._buf.append(value)
        if len(self._buf) > self.lookback:
            self._buf.pop(0)
        if len(self._buf) < max(2, self.lookback // 2):
            return None
        arr = np.array(self._buf)
        std = arr.std()
        if std < 1e-10:
            return 0.0
        return float((value - arr.mean()) / std)

    @property
    def mean(self) -> Optional[float]:
        return float(np.mean(self._buf)) if self._buf else None

    @property
    def std(self) -> Optional[float]:
        return float(np.std(self._buf)) if len(self._buf) >= 2 else None

    @property
    def count(self) -> int:
        return len(self._buf)


# ═══════════════════════════════════════════════════════
#  HALF-LIFE OF MEAN REVERSION
#  (Platen Ch.4 Ornstein-Uhlenbeck, Chan Ch.2)
# ═══════════════════════════════════════════════════════

def compute_half_life(prices: list) -> Optional[float]:
    """
    Estimate half-life via OLS regression on the OU SDE:
        ΔY(t) = λ·Y(t-1) + μ + ε

    λ < 0 → mean-reverting, half_life = -log(2)/λ
    λ ≥ 0 → not mean-reverting (trending or random walk)

    From Williams Ch.15: the OU process is the continuous-time
    analog of the discrete regression used here.
    """
    if len(prices) < 20:
        return None

    y     = np.array(prices, dtype=float)
    y_lag = y[:-1]
    delta = np.diff(y)

    X = np.column_stack([y_lag, np.ones(len(y_lag))])
    try:
        coeffs, _, _, _ = np.linalg.lstsq(X, delta, rcond=None)
        lam = coeffs[0]
        if lam >= 0:
            return None
        hl = float(-np.log(2) / lam)
        return hl if 1 <= hl <= 1000 else None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════
#  HURST EXPONENT
#  (Chan Ch.2, Platen Ch.4 — stationarity test)
# ═══════════════════════════════════════════════════════

def compute_hurst(prices: list, max_lag: int = 20) -> float:
    """
    Hurst exponent via variance of log price differences.
    
    Var(z(t+τ) - z(t)) ~ τ^(2H)  where z = log(price)
    
    H < 0.5 → mean reverting (negatively autocorrelated returns)
    H = 0.5 → random walk (geometric Brownian motion)
    H > 0.5 → trending (positively autocorrelated returns)
    
    From Platen Ch.4: H connects to the Hurst parameter of
    fractional Brownian motion.
    """
    if len(prices) < max_lag + 5:
        return 0.5

    log_p  = np.log(np.array(prices, dtype=float))
    lags   = range(2, min(max_lag, len(prices) // 3))
    vars_  = [np.var(log_p[lag:] - log_p[:-lag]) for lag in lags]

    if len(vars_) < 2 or any(v <= 0 for v in vars_):
        return 0.5

    try:
        slope = np.polyfit(np.log(list(lags)), np.log(vars_), 1)[0]
        return float(np.clip(slope / 2.0, 0.01, 0.99))
    except Exception:
        return 0.5


# ═══════════════════════════════════════════════════════
#  ADF TEST (simplified, no statsmodels needed on Termux)
#  Chan Ch.2, Platen Ch.3
# ═══════════════════════════════════════════════════════

def adf_test(prices: list, lags: int = 1) -> Tuple[float, bool]:
    """
    Simplified ADF test:
        ΔY(t) = λ·Y(t-1) + μ + Σ αᵢ·ΔY(t-i) + ε

    Tests H₀: λ = 0 (unit root = not mean reverting)
    Returns (t_statistic, is_mean_reverting_90pct)

    Critical values (Chan Ch.2 Table):
        90% = -2.594
        95% = -2.871
        99% = -3.458
    """
    if len(prices) < 30:
        return (0.0, False)

    y     = np.array(prices, dtype=float)
    delta = np.diff(y)
    y_lag = y[:-1]

    # Build regressor matrix with lagged differences
    n = len(delta) - lags
    X_cols = [y_lag[lags:], np.ones(n)]
    for i in range(1, lags + 1):
        X_cols.append(delta[lags - i: -i if i > 0 else None][:n])
    X = np.column_stack(X_cols)
    d = delta[lags:]

    try:
        coeffs, resid, rank, _ = np.linalg.lstsq(X, d, rcond=None)
        lam = coeffs[0]
        n_obs = len(d)
        df = n_obs - X.shape[1]
        if df <= 0:
            return (0.0, False)
        ss_resid = float(np.sum(resid)) if len(resid) > 0 else float(np.sum((d - X @ coeffs)**2))
        if len(resid) == 0:
            ss_resid = float(np.sum((d - X @ coeffs)**2))
        s2 = ss_resid / df
        XtX_inv = np.linalg.pinv(X.T @ X)
        se_lam  = float(np.sqrt(s2 * XtX_inv[0, 0]))
        t_stat  = lam / se_lam if se_lam > 0 else 0.0
        is_mr   = t_stat < -2.594   # 90% critical value
        return (float(t_stat), bool(is_mr))
    except Exception:
        return (0.0, False)


# ═══════════════════════════════════════════════════════
#  CROSS-SECTIONAL MOMENTUM
#  Khandani-Lo (2007), Chan Ch.4
# ═══════════════════════════════════════════════════════

def cross_sectional_weights(returns: dict) -> dict:
    """
    Compute Khandani-Lo cross-sectional momentum weights.
    
    wᵢ = -(rᵢ - mean(r)) / Σ|rᵢ - mean(r)|
    
    Buy underperformers, short outperformers.
    Dollar-neutral: Σwᵢ = 0
    Normalized: Σ|wᵢ| = 1
    
    From Chan Ch.4: "completely linear, no parameters, almost perfectly
    dollar neutral"
    """
    if not returns or len(returns) < 2:
        return {}

    symbols = list(returns.keys())
    r_vals  = np.array([returns[s] for s in symbols])
    mean_r  = r_vals.mean()
    deviations = -(r_vals - mean_r)
    total = np.sum(np.abs(deviations))

    if total < 1e-10:
        return {}

    weights = deviations / total
    return {s: float(w) for s, w in zip(symbols, weights)}


# ═══════════════════════════════════════════════════════
#  KELLY POSITION SIZING
#  Chan Ch.8, Platen Ch.11 (Expected Utility Maximization)
# ═══════════════════════════════════════════════════════

def kelly_fraction(mean_return: float, variance: float,
                   kelly_scale: float = 0.25) -> float:
    """
    Full Kelly: f* = μ/σ²
    Fractional Kelly (Chan Ch.8): f = scale × f*

    From Platen Ch.11: For log-utility investors (CRRA with γ=1),
    the optimal fraction IS the full Kelly criterion. Fractional Kelly
    (scale=0.25) is the practical compromise balancing growth vs. risk.
    
    Returns fraction of wealth to invest [0, 1].
    """
    if variance <= 0:
        return 0.0
    full_kelly = mean_return / variance
    return float(np.clip(abs(full_kelly) * kelly_scale, 0.0, 1.0))


def zscore_to_size(zscore: float, base_pct: float = 0.05,
                   max_pct: float = 0.15) -> float:
    """
    Scale position size proportional to |z-score|.
    Implements the 'scaling-in' concept from Chan Ch.3.
    
    From Williams Ch.10: supermartingale property means expected
    return is proportional to deviation from mean — larger deviations
    warrant larger positions.
    """
    return float(np.clip(abs(zscore) * base_pct, 0.0, max_pct))

# ═══════════════════════════════════════════════════════
#  FIRST PASSAGE PROBABILITY
#  Shreve Ch.3 — Reflection Principle
#  P(max W(s) ≥ m, 0≤s≤T) = 2·(1 - N(m/√T))
# ═══════════════════════════════════════════════════════

from scipy.stats import norm as _norm

def prob_touch_before_revert(
    zscore: float,
    pain_z: float,
    half_life_bars: float,
) -> float:
    """
    Probability that Z-score touches pain_z before returning to 0.

    Shreve Ch.3.6 — First passage time for Brownian motion.
    For OU process with half-life H bars:
      time_to_revert ~ H / log(2)
      distance = |pain_z - zscore|

    Parameters
    ----------
    zscore        : current Z-score (e.g. -2.5 = entered long)
    pain_z        : danger level   (e.g. -5.0 = would force close)
    half_life_bars: from compute_half_life()

    Returns
    -------
    float: probability 0.0–1.0

    Example
    -------
    zscore=-2.5, pain_z=-5.0, half_life=8
    → ~18% chance price drops another 2.5σ before reverting
    → acceptable — enter full position

    zscore=-2.5, pain_z=-5.0, half_life=80
    → ~45% chance — slow reversion, risky
    → reduce position size
    """
    if half_life_bars is None or half_life_bars <= 0:
        return 0.5   # unknown — assume 50/50

    distance = abs(pain_z - zscore)
    if distance <= 0:
        return 1.0

    # Expected reversion time in bars
    T = half_life_bars / 0.693   # H = T·log(2) → T = H/log(2)

    # Reflection principle: P(touch m before 0) ≈ 2·(1 - N(m/√T))
    prob = 2.0 * (1.0 - _norm.cdf(distance / (T ** 0.5)))
    return float(np.clip(prob, 0.0, 1.0))


def position_scale_from_risk(
    zscore: float,
    half_life_bars: float,
    pain_multiplier: float = 2.0,
) -> float:
    """
    Scale position size based on first passage risk.

    pain_z = zscore × pain_multiplier
    (default: if entered at -2.5σ, pain point is -5.0σ)

    Returns
    -------
    float: 0.25–1.0 scaling factor for position size

    Scale table:
      prob < 20% → full size   (1.0)
      prob 20-35% → 75% size   (0.75)
      prob 35-50% → 50% size   (0.5)
      prob > 50% → 25% size    (0.25) — very risky
    """
    pain_z = zscore * pain_multiplier
    prob   = prob_touch_before_revert(zscore, pain_z, half_life_bars)

    if prob < 0.20:
        return 1.00
    elif prob < 0.35:
        return 0.75
    elif prob < 0.50:
        return 0.50
    else:
        return 0.25

# ═══════════════════════════════════════════════════════
#  JUMP-DIFFUSION VALUE AT RISK
#  Shreve Ch.11 — Compound Poisson + Brownian Motion
#
#  dS = μS dt + σS dW + S dJ
#
#  VaR = diffusion_VaR + jump_VaR
#  where jump_VaR accounts for Poisson jump arrivals
# ═══════════════════════════════════════════════════════

def jump_diffusion_var(
    equity:      float,
    confidence:  float = 0.99,
    horizon_h:   int   = 24,
    sigma_h:     float = 0.02,
    lambda_:     float = 0.1,
    jump_mean:   float = -0.05,
    jump_std:    float = 0.03,
) -> dict:
    """
    VaR under jump-diffusion model (Shreve Ch.11).

    Parameters
    ----------
    equity      : current portfolio value
    confidence  : VaR confidence level (0.99 = 99%)
    horizon_h   : horizon in hours (default 24h)
    sigma_h     : hourly diffusion volatility (default 2%)
    lambda_     : jump arrival rate per hour (default 0.1)
    jump_mean   : average jump size (default -5%)
    jump_std    : jump size std dev (default 3%)

    Returns
    -------
    dict with:
        var_total     : total VaR in dollars
        var_diffusion : diffusion component
        var_jump      : jump component
        var_pct       : VaR as % of equity
        expected_jumps: expected number of jumps in horizon
    """
    from scipy.stats import poisson

    # ── Diffusion VaR ─────────────────────────────────
    # σ scales with √time (Shreve Ch.3 — quadratic variation)
    sigma_T         = sigma_h * np.sqrt(horizon_h)
    z_score_conf    = _norm.ppf(1.0 - confidence)   # negative
    var_diffusion   = abs(z_score_conf * sigma_T * equity)

    # ── Jump VaR ──────────────────────────────────────
    # Expected jumps in horizon (Poisson, Shreve Ch.11.2)
    lambda_T        = lambda_ * horizon_h
    expected_jumps  = lambda_T

    # Worst-case jump loss at confidence level
    # Number of jumps: use 99th percentile of Poisson
    n_jumps_worst   = poisson.ppf(confidence, lambda_T)

    # Each jump: mean + 2σ downside
    worst_jump_size = abs(jump_mean) + 2 * jump_std
    var_jump        = n_jumps_worst * worst_jump_size * equity

    # ── Total VaR ─────────────────────────────────────
    # Combined (conservative: add rather than diversify)
    var_total = var_diffusion + var_jump
    var_pct   = var_total / equity * 100

    return {
        "var_total":      round(var_total, 2),
        "var_diffusion":  round(var_diffusion, 2),
        "var_jump":       round(var_jump, 2),
        "var_pct":        round(var_pct, 2),
        "expected_jumps": round(expected_jumps, 2),
        "confidence":     confidence,
        "horizon_h":      horizon_h,
    }

# ═══════════════════════════════════════════════════════
#  VASICEK FUNDING RATE MODEL
#  Shreve Ch.10 — Term Structure Models
#
#  dr = κ(θ - r)dt + σdW
#
#  Same OU process as price spread but applied to
#  perpetual futures funding rate.
#
#  When funding > θ + 2σ → market overpaying longs
#    → SHORT futures (collect funding + expect reversion)
#  When funding < θ - 2σ → market overpaying shorts
#    → LONG futures (collect funding + expect reversion)
# ═══════════════════════════════════════════════════════

class VasicekFundingModel:
    """
    Vasicek mean-reversion model for crypto funding rates.

    Shreve Ch.10.2.1 — Two-Factor Vasicek:
        dr = κ(θ - r)dt + σdW

    Parameters estimated via OLS on rolling window,
    same technique as compute_half_life().
    """

    def __init__(self, lookback: int = 72):
        self.lookback  = lookback   # 72 hours = 3 days
        self._rates:   list = []    # funding rate history
        self.kappa:    float = 0.0  # mean reversion speed
        self.theta:    float = 0.0  # long-run mean
        self.sigma:    float = 0.0  # volatility
        self.half_life: float = None

    def update(self, funding_rate: float) -> dict:
        """
        Add new funding rate observation and compute signal.

        Parameters
        ----------
        funding_rate : float
            Funding rate as decimal (e.g. 0.0001 = 0.01%)
            Binance perpetuals update every 8 hours.

        Returns
        -------
        dict with zscore, signal, and model parameters
        """
        self._rates.append(funding_rate)
        if len(self._rates) > self.lookback:
            self._rates.pop(0)

        if len(self._rates) < 10:
            return {"zscore": 0.0, "signal": "none",
                    "theta": 0.0, "sigma": 0.0, "half_life": None}

        self._fit()

        # Z-score of current rate vs long-run mean
        if self.sigma > 0:
            zscore = (funding_rate - self.theta) / self.sigma
        else:
            zscore = 0.0

        # Signal generation
        if zscore > 2.0:
            signal = "short_futures"   # funding too high, short
        elif zscore < -2.0:
            signal = "long_futures"    # funding too low, long
        elif abs(zscore) < 0.5:
            signal = "exit"            # near mean, exit
        else:
            signal = "hold"

        return {
            "zscore":    round(zscore, 3),
            "signal":    signal,
            "rate":      funding_rate,
            "theta":     round(self.theta, 6),
            "sigma":     round(self.sigma, 6),
            "kappa":     round(self.kappa, 4),
            "half_life": round(self.half_life, 1) if self.half_life else None,
        }

    def _fit(self):
        """Fit OU parameters via OLS — same as compute_half_life()."""
        r     = np.array(self._rates)
        r_lag = r[:-1]
        delta = np.diff(r)

        X = np.column_stack([r_lag, np.ones(len(r_lag))])
        try:
            coeffs, _, _, _ = np.linalg.lstsq(X, delta, rcond=None)
            lam   = coeffs[0]    # -κ·dt
            mu    = coeffs[1]    # κ·θ·dt

            if lam < 0:
                self.kappa = float(-lam)
                self.theta = float(mu / self.kappa) if self.kappa > 0 else float(r.mean())
                self.half_life = float(np.log(2) / self.kappa)
            else:
                self.theta = float(r.mean())
                self.kappa = 0.01
                self.half_life = None

            # Estimate σ from residuals
            residuals  = delta - X @ coeffs
            self.sigma = float(np.std(residuals)) if len(residuals) > 1 else 0.0

        except Exception:
            self.theta = float(r.mean())
            self.sigma = float(r.std()) if len(r) > 1 else 0.0
# ═══════════════════════════════════════════════════════
#  BLACK-SCHOLES OPTIONS PRICING
#  Shreve Ch.4.5 — Black-Scholes-Merton Equation
#  Shreve Ch.5.2 — Risk-Neutral Pricing
#
#  C = S·N(d1) - K·e^{-rT}·N(d2)
#  P = K·e^{-rT}·N(-d2) - S·N(-d1)
#
#  Greeks:
#    Delta = ∂V/∂S
#    Gamma = ∂²V/∂S²
#    Vega  = ∂V/∂σ
#    Theta = ∂V/∂t
#    Rho   = ∂V/∂r
# ═══════════════════════════════════════════════════════

def black_scholes(
    S:      float,
    K:      float,
    T:      float,
    r:      float,
    sigma:  float,
    option: str = "call",
) -> dict:
    """
    Black-Scholes option pricing with full Greeks.
    Shreve Ch.4.5, Ch.5.2.5

    Parameters
    ----------
    S      : current asset price
    K      : strike price
    T      : time to expiry in years (e.g. 30 days = 30/365)
    r      : risk-free rate (e.g. 0.05 = 5%)
    sigma  : implied/realized volatility (e.g. 0.8 = 80% for BTC)
    option : "call" or "put"

    Returns
    -------
    dict with price and all Greeks
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return {"price": 0.0, "delta": 0.0, "gamma": 0.0,
                "vega": 0.0, "theta": 0.0, "rho": 0.0}

    # d1, d2 (Shreve Eq 4.5.18)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)

    # Standard normal PDF and CDF
    Nd1  = _norm.cdf(d1)
    Nd2  = _norm.cdf(d2)
    Nnd1 = _norm.cdf(-d1)
    Nnd2 = _norm.cdf(-d2)
    nd1  = _norm.pdf(d1)   # φ(d1) for Greeks

    discount = np.exp(-r * T)

    if option == "call":
        price = S * Nd1 - K * discount * Nd2
        delta = Nd1
        rho   = K * T * discount * Nd2
    else:
        price = K * discount * Nnd2 - S * Nnd1
        delta = Nd1 - 1.0
        rho   = -K * T * discount * Nnd2

    # Greeks same for call and put (Shreve Ch.4.5.5)
    gamma = nd1 / (S * sigma * np.sqrt(T))
    vega  = S * nd1 * np.sqrt(T)          # per unit of σ
    theta = (-(S * nd1 * sigma) / (2 * np.sqrt(T))
             - r * K * discount * Nd2)    # per year, call

    return {
        "price":  round(float(price), 4),
        "delta":  round(float(delta), 4),
        "gamma":  round(float(gamma), 6),
        "vega":   round(float(vega), 4),
        "theta":  round(float(theta), 4),
        "rho":    round(float(rho), 4),
        "d1":     round(float(d1), 4),
        "d2":     round(float(d2), 4),
        "iv":     round(float(sigma), 4),
    }


def implied_vol(
    market_price: float,
    S: float, K: float, T: float, r: float,
    option: str = "call",
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float:
    """
    Compute implied volatility via Newton-Raphson.
    Shreve Ch.5 — inverse of Black-Scholes.

    Given market price → find σ such that BS(σ) = market_price.
    Uses Vega as the derivative for Newton-Raphson.
    """
    sigma = 0.5   # initial guess 50% vol
    for _ in range(max_iter):
        bs    = black_scholes(S, K, T, r, sigma, option)
        price = bs["price"]
        vega  = bs["vega"]
        if abs(vega) < 1e-10:
            break
        diff  = market_price - price
        sigma = sigma + diff / vega
        sigma = max(0.001, min(sigma, 10.0))   # clamp
        if abs(diff) < tol:
            break
    return round(float(sigma), 6)


def vol_signal(
    realized_vol: float,
    implied_vol_: float,
    threshold: float = 0.15,
) -> str:
    """
    Volatility trading signal.
    
    When IV >> RV → options are expensive → SELL vol (sell straddle)
    When IV << RV → options are cheap   → BUY  vol (buy straddle)

    threshold: 15% difference triggers signal
    """
    if implied_vol_ <= 0 or realized_vol <= 0:
        return "none"
    ratio = (implied_vol_ - realized_vol) / realized_vol
    if ratio > threshold:
        return "sell_vol"    # IV expensive → sell options
    elif ratio < -threshold:
        return "buy_vol"     # IV cheap → buy options
    return "neutral"

# ═══════════════════════════════════════════════════════
#  RAVI — RANGE ACTION VERIFICATION INDEX
#  Chande "Beyond Technical Analysis" Ch.3
#  Velu "Algorithmic Trading" Ch.5
#
#  RAVI = |7-SMA - 65-SMA| / 65-SMA × 100
#
#  RAVI < 3% → ranging market  → USE mean reversion
#  RAVI > 3% → trending market → SKIP mean reversion
#
#  More responsive than ADX (single smoothing vs double)
# ═══════════════════════════════════════════════════════

def compute_ravi(prices: list, short: int = 7, long: int = 65) -> dict:
    """
    Range Action Verification Index (Chande 1997).

    Parameters
    ----------
    prices : list of close prices (need at least 65)
    short  : short SMA period (default 7 = 1 week daily)
    long   : long SMA period  (default 65 = 1 quarter daily)
             For hourly bars: short=7h, long=65h

    Returns
    -------
    dict:
        ravi      : float — RAVI value in percent
        regime    : "ranging" | "trending" | "unknown"
        signal    : "use_mr" | "skip_mr"
        sma_short : float
        sma_long  : float
    """
    if len(prices) < long:
        return {
            "ravi":      None,
            "regime":    "unknown",
            "signal":    "use_mr",   # default to MR when insufficient data
            "sma_short": None,
            "sma_long":  None,
        }

    arr       = np.array(prices[-long:], dtype=float)
    sma_short = float(arr[-short:].mean())
    sma_long  = float(arr.mean())

    if sma_long == 0:
        return {"ravi": None, "regime": "unknown", "signal": "use_mr",
                "sma_short": sma_short, "sma_long": sma_long}

    ravi = abs(sma_short - sma_long) / sma_long * 100.0

    # Chande's threshold: 3% for daily data
    # For hourly crypto: slightly higher threshold (3.5%) due to higher vol
    THRESHOLD = 3.5

    regime = "ranging" if ravi < THRESHOLD else "trending"
    signal = "use_mr"  if ravi < THRESHOLD else "skip_mr"

    return {
        "ravi":      round(ravi, 3),
        "regime":    regime,
        "signal":    signal,
        "sma_short": round(sma_short, 4),
        "sma_long":  round(sma_long, 4),
    }


def compute_ravi_series(prices: list, short: int = 7, long: int = 65) -> list:
    """
    Compute RAVI for each point in a price series.
    Returns list of RAVI values (None for first 64 points).
    """
    result = []
    for i in range(len(prices)):
        if i < long - 1:
            result.append(None)
        else:
            r = compute_ravi(prices[:i+1], short, long)
            result.append(r["ravi"])
    return result

# ═══════════════════════════════════════════════════════
#  DATA SCRAMBLING ROBUSTNESS TEST
#  Chande "Beyond Technical Analysis" Ch.8
#
#  Randomize time order of returns (destroy serial structure)
#  Retest strategy on scrambled data N times
#  If original Sharpe >> scrambled → captures genuine edge
#  If similar → random noise, not a real strategy
#
#  p_value < 0.05 → strategy is robust (5% significance)
# ═══════════════════════════════════════════════════════

def data_scrambling_test(
    prices:     list,
    entry_z:    float = 2.0,
    exit_z:     float = 0.0,
    n_trials:   int   = 500,
    lookback:   int   = 20,
) -> dict:
    """
    Chande data scrambling robustness test.

    Tests whether strategy profits come from genuine time-series
    structure or from random chance.

    Parameters
    ----------
    prices   : list of close prices
    entry_z  : Z-score entry threshold
    exit_z   : Z-score exit threshold
    n_trials : number of scramble trials (500 recommended)
    lookback : rolling window for Z-score calculation

    Returns
    -------
    dict:
        original_sharpe  : Sharpe on real data
        scrambled_mean   : mean Sharpe on scrambled data
        scrambled_std    : std of scrambled Sharpes
        p_value          : fraction of scrambled >= original
        is_robust        : True if p_value < 0.05
        z_score_vs_null  : how many std above scrambled mean
    """
    prices = np.array(prices, dtype=float)
    if len(prices) < lookback * 3:
        return {"error": "insufficient data", "is_robust": False}

    def simple_mr_sharpe(px):
        """Simple MR strategy Sharpe on a price series."""
        px       = np.array(px, dtype=float)
        n        = len(px)
        position = 0
        pnl      = []

        for i in range(lookback, n - 1):
            window = px[i-lookback:i]
            mean   = window.mean()
            std    = window.std()
            if std < 1e-10:
                continue
            z = (px[i] - mean) / std

            # Signal based on current bar
            if position == 0:
                if z < -entry_z:
                    position = 1
                elif z > entry_z:
                    position = -1
            elif position == 1 and z >= exit_z:
                position = 0
            elif position == -1 and z <= -exit_z:
                position = 0

            # Return on NEXT bar
            ret = (px[i+1] - px[i]) / px[i]
            pnl.append(position * ret)

        if len(pnl) < 10:
            return 0.0
        pnl = np.array(pnl)
        if pnl.std() < 1e-10:
            return 0.0
        return float(pnl.mean() / pnl.std() * np.sqrt(252 * 24))

    # Original Sharpe
    original_sharpe = simple_mr_sharpe(prices)

    # Scrambled Sharpes — destroy time structure
    log_returns = np.diff(np.log(prices))
    scrambled_sharpes = []

    for _ in range(n_trials):
        scrambled_ret = log_returns.copy()
        np.random.shuffle(scrambled_ret)
        # Reconstruct prices from scrambled returns
        scrambled_px = np.exp(
            np.concatenate([[np.log(prices[0])], np.cumsum(scrambled_ret)])
        )
        s = simple_mr_sharpe(scrambled_px)
        scrambled_sharpes.append(s)

    scrambled_arr = np.array(scrambled_sharpes)
    p_value       = float(np.mean(scrambled_arr >= original_sharpe))
    scr_mean      = float(scrambled_arr.mean())
    scr_std       = float(scrambled_arr.std())
    z_vs_null     = (original_sharpe - scr_mean) / scr_std if scr_std > 0 else 0.0

    return {
        "original_sharpe": round(original_sharpe, 3),
        "scrambled_mean":  round(scr_mean, 3),
        "scrambled_std":   round(scr_std, 3),
        "p_value":         round(p_value, 3),
        "is_robust":       p_value < 0.05,
        "z_score_vs_null": round(z_vs_null, 2),
        "n_trials":        n_trials,
    }



def prob_of_loss(sharpe: float, volatility: float, horizon_days: int = 30, loss_threshold: float = 0.05) -> dict:
    """
    Probability of Loss metric (Dunis Ch.1).
    Estimates probability of losing more than loss_threshold over horizon_days
    given annualised Sharpe and daily volatility.
    """
    import math
    if volatility <= 0 or horizon_days <= 0:
        return {"prob_loss": None, "error": "invalid inputs"}
    daily_vol = volatility / math.sqrt(252)
    daily_return = sharpe * daily_vol
    horizon_vol = daily_vol * math.sqrt(horizon_days)
    horizon_return = daily_return * horizon_days
    if horizon_vol <= 0:
        return {"prob_loss": None, "error": "zero volatility"}
    z = (-loss_threshold - horizon_return) / horizon_vol
    def std_normal_cdf(x):
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))
    prob = std_normal_cdf(z)
    return {
        "prob_loss_pct": round(prob * 100, 2),
        "loss_threshold_pct": round(loss_threshold * 100, 1),
        "horizon_days": horizon_days,
        "daily_vol": round(daily_vol, 6),
        "horizon_vol": round(horizon_vol, 4),
        "z_score": round(z, 4),
        "sharpe_input": round(sharpe, 4),
    }


def logit_direction_filter(prices: list, zscores: list = None, lookback: int = 20) -> dict:
    """
    Logit Directional Filter (Dunis Ch.1).
    Estimates probability that next bar return is positive using
    logistic regression on lagged returns + zscore features.
    Returns: prob_up, signal, confidence
    """
    if len(prices) < lookback + 2:
        return {"prob_up": 0.5, "signal": "neutral", "confidence": 0.0, "error": "insufficient data"}

    # Build feature matrix from lagged log returns
    log_rets = [np.log(prices[i] / prices[i-1]) for i in range(1, len(prices))]
    if len(log_rets) < lookback + 1:
        return {"prob_up": 0.5, "signal": "neutral", "confidence": 0.0}

    X, y = [], []
    for i in range(lookback, len(log_rets)):
        features = log_rets[i-lookback:i]
        if zscores and i < len(zscores):
            features = features + [zscores[i]]
        X.append(features)
        y.append(1 if log_rets[i] > 0 else 0)

    X = np.array(X)
    y = np.array(y)

    if len(X) < 10:
        return {"prob_up": 0.5, "signal": "neutral", "confidence": 0.0}

    # Normalise features
    mu = X.mean(axis=0)
    sd = X.std(axis=0) + 1e-8
    X = (X - mu) / sd

    # Logistic regression via gradient descent
    n_features = X.shape[1]
    w = np.zeros(n_features)
    b = 0.0
    lr = 0.01
    for _ in range(200):
        z = X.dot(w) + b
        p = 1 / (1 + np.exp(-np.clip(z, -10, 10)))
        err = p - y
        w -= lr * X.T.dot(err) / len(y)
        b -= lr * err.mean()

    # Predict on last available features
    last_feat = np.array(log_rets[-lookback:])
    if zscores and len(zscores) > 0:
        last_feat = np.append(last_feat, float(zscores[-1]))
    if len(last_feat) != len(mu):
        last_feat = last_feat[:len(mu)]
    last_feat = (last_feat - mu) / sd
    z_last = last_feat.dot(w) + b
    prob_up = float(1 / (1 + np.exp(-np.clip(z_last, -10, 10))))

    confidence = abs(prob_up - 0.5) * 2  # 0=no edge, 1=full confidence
    if prob_up > 0.55:
        signal = "long"
    elif prob_up < 0.45:
        signal = "short"
    else:
        signal = "neutral"

    return {
        "prob_up": round(prob_up, 4),
        "signal": signal,
        "confidence": round(confidence, 4),
        "n_samples": len(y),
        "feature_count": n_features,
    }


def compute_rsi(prices: list, period: int = 14) -> dict:
    """
    Relative Strength Index (Murphy Ch.10, p.239).
    RSI = 100 - (100 / (1 + RS)) where RS = avg_gain / avg_loss
    Entry filter: skip BUY when RSI > 70 (overbought), skip SELL when RSI < 30 (oversold)
    """
    if len(prices) < period + 1:
        return {"rsi": 50.0, "signal": "neutral", "error": "insufficient data"}

    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [abs(d) if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        rsi = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))

    if rsi > 70:
        signal = "overbought"
    elif rsi < 30:
        signal = "oversold"
    else:
        signal = "neutral"

    return {
        "rsi": round(rsi, 2),
        "signal": signal,
        "avg_gain": round(avg_gain, 6),
        "avg_loss": round(avg_loss, 6),
    }


def compute_bbw(prices: list, period: int = 20, num_std: float = 2.0) -> dict:
    """
    Bollinger Band Width (Murphy p.211).
    BBW = (Upper - Lower) / Middle * 100
    Low BBW = contracting bands = MR regime = good to trade
    High BBW = expanding bands = trending = skip MR
    Threshold: BBW < 4.0 = tight (trade), BBW > 8.0 = wide (skip)
    """
    if len(prices) < period:
        return {"bbw": None, "signal": "neutral", "error": "insufficient data"}

    window = prices[-period:]
    middle = sum(window) / period
    variance = sum((p - middle) ** 2 for p in window) / period
    std = variance ** 0.5

    upper = middle + num_std * std
    lower = middle - num_std * std
    bbw = ((upper - lower) / middle * 100) if middle > 0 else 0.0

    if bbw < 4.0:
        signal = "tight"    # MR regime — good to trade
    elif bbw > 8.0:
        signal = "wide"     # Trending — skip MR
    else:
        signal = "neutral"

    return {
        "bbw": round(bbw, 4),
        "signal": signal,
        "upper": round(upper, 4),
        "lower": round(lower, 4),
        "middle": round(middle, 4),
        "std": round(std, 4),
    }


def compute_macd(prices: list, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """
    MACD Histogram (Murphy p.255).
    MACD line = EMA(fast) - EMA(slow)
    Signal line = EMA(MACD, signal)
    Histogram = MACD - Signal
    Entry filter: only enter in direction histogram is pointing
    histogram > 0 and rising = bullish momentum
    histogram < 0 and falling = bearish momentum
    """
    if len(prices) < slow + signal:
        return {"macd": None, "signal_line": None, "histogram": None, "signal": "neutral", "error": "insufficient data"}

    def ema(data, period):
        k = 2.0 / (period + 1)
        result = [data[0]]
        for p in data[1:]:
            result.append(p * k + result[-1] * (1 - k))
        return result

    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)
    macd_line = [f - s for f, s in zip(ema_fast[slow-1:], ema_slow[slow-1:])]
    signal_line = ema(macd_line, signal)
    histogram = [m - s for m, s in zip(macd_line[signal-1:], signal_line[signal-1:])]

    if len(histogram) < 2:
        return {"macd": None, "signal_line": None, "histogram": None, "signal": "neutral"}

    h_now = histogram[-1]
    h_prev = histogram[-2]

    if h_now > 0 and h_now > h_prev:
        sig = "bullish"
    elif h_now < 0 and h_now < h_prev:
        sig = "bearish"
    else:
        sig = "neutral"

    return {
        "macd": round(macd_line[-1], 6),
        "signal_line": round(signal_line[-1], 6),
        "histogram": round(h_now, 6),
        "histogram_prev": round(h_prev, 6),
        "signal": sig,
    }


def frac_diff_weights(d: float, size: int) -> list:
    """
    Compute fractional differentiation weights (Lopez de Prado Ch.5).
    w_k = -w_{k-1} * (d - k + 1) / k
    """
    w = [1.0]
    for k in range(1, size):
        w.append(-w[-1] * (d - k + 1) / k)
    return list(reversed(w))

def frac_diff(prices: list, d: float = 0.4, threshold: float = 1e-4) -> list:
    """
    Fractionally differentiated price series (Lopez de Prado Ch.5).
    Makes prices stationary while preserving maximum memory.
    d=0: original series (non-stationary)
    d=1: standard differencing (loses memory)
    d=0.4: typical value preserving most memory while achieving stationarity
    Returns series of same length with NaN-equivalent 0.0 for initial window.
    """
    if len(prices) < 10:
        return prices

    # Compute weights until they fall below threshold
    w = [1.0]
    k = 1
    while True:
        w_k = -w[-1] * (d - k + 1) / k
        if abs(w_k) < threshold:
            break
        w.append(w_k)
        k += 1
        if k > len(prices):
            break

    w = list(reversed(w))
    width = len(w)
    result = []

    for i in range(len(prices)):
        if i < width - 1:
            result.append(0.0)  # not enough history
            continue
        window = prices[i - width + 1: i + 1]
        val = sum(wt * px for wt, px in zip(w, window))
        result.append(val)

    return result

def find_min_frac_diff(prices: list, start_d: float = 0.1, step: float = 0.1) -> dict:
    """
    Find minimum d that makes series stationary (passes ADF test).
    Lopez de Prado Ch.5 — preserves maximum memory.
    """
    best_d = 1.0
    for d in [round(start_d + step * i, 2) for i in range(int((1.0 - start_d) / step) + 1)]:
        fd = frac_diff(prices, d=d)
        fd_clean = [x for x in fd if x != 0.0]
        if len(fd_clean) < 20:
            continue
        p_val, is_stat = adf_test(fd_clean)
        if is_stat:
            best_d = d
            break
    return {
        "min_d": best_d,
        "is_stationary": best_d < 1.0,
        "memory_preserved": round(1.0 - best_d, 2),
    }


def triple_barrier_label(prices: list, entry_idx: int, pt: float = 1.0, 
                         sl: float = 1.0, max_bars: int = 10, 
                         vol: float = None) -> dict:
    """
    Triple Barrier Method (Lopez de Prado Ch.3).
    Labels a trade from entry_idx with three barriers:
    - Upper: profit take at +pt * vol
    - Lower: stop loss at -sl * vol  
    - Vertical: time exit after max_bars
    Returns: label (+1 hit upper, -1 hit lower, 0 hit vertical), 
             exit_idx, return_pct, barrier_hit
    """
    if entry_idx >= len(prices) - 1:
        return {"label": 0, "exit_idx": entry_idx, "return_pct": 0.0, "barrier": "none"}

    entry_price = prices[entry_idx]
    if vol is None:
        # Use rolling std as volatility estimate
        window = prices[max(0, entry_idx-20):entry_idx]
        if len(window) < 2:
            vol = entry_price * 0.01
        else:
            import statistics
            rets = [(window[i]-window[i-1])/window[i-1] for i in range(1, len(window))]
            vol = statistics.stdev(rets) * entry_price if rets else entry_price * 0.01

    upper = entry_price + pt * vol
    lower = entry_price - sl * vol

    for i in range(entry_idx + 1, min(entry_idx + max_bars + 1, len(prices))):
        p = prices[i]
        if p >= upper:
            return {
                "label": 1,
                "exit_idx": i,
                "exit_price": p,
                "return_pct": round((p - entry_price) / entry_price * 100, 4),
                "barrier": "upper",
                "bars_held": i - entry_idx,
            }
        if p <= lower:
            return {
                "label": -1,
                "exit_idx": i,
                "exit_price": p,
                "return_pct": round((p - entry_price) / entry_price * 100, 4),
                "barrier": "lower",
                "bars_held": i - entry_idx,
            }

    # Vertical barrier hit
    exit_idx = min(entry_idx + max_bars, len(prices) - 1)
    exit_price = prices[exit_idx]
    return {
        "label": 0,
        "exit_idx": exit_idx,
        "exit_price": exit_price,
        "return_pct": round((exit_price - entry_price) / entry_price * 100, 4),
        "barrier": "vertical",
        "bars_held": exit_idx - entry_idx,
    }

def compute_dynamic_exits(prices: list, zscore: float, 
                          pt_multiplier: float = 1.5,
                          sl_multiplier: float = 1.0,
                          max_bars: int = 20) -> dict:
    """
    Compute dynamic exit levels based on current volatility.
    Used in live trading to set exit targets.
    Returns take_profit price, stop_loss price, and expected holding bars.
    """
    if len(prices) < 20:
        return {"take_profit": None, "stop_loss": None, "max_bars": max_bars}

    import statistics
    window = prices[-20:]
    rets = [(window[i]-window[i-1])/window[i-1] for i in range(1, len(window))]
    if not rets or statistics.stdev(rets) == 0:
        return {"take_profit": None, "stop_loss": None, "max_bars": max_bars}

    vol = statistics.stdev(rets) * prices[-1]
    current = prices[-1]

    if zscore < 0:  # long position
        tp = round(current + pt_multiplier * vol, 4)
        sl = round(current - sl_multiplier * vol, 4)
    else:  # short position
        tp = round(current - pt_multiplier * vol, 4)
        sl = round(current + sl_multiplier * vol, 4)

    return {
        "take_profit": tp,
        "stop_loss":   sl,
        "vol":         round(vol, 4),
        "max_bars":    max_bars,
        "pt_mult":     pt_multiplier,
        "sl_mult":     sl_multiplier,
    }


def prob_bet_size(prob_correct: float, max_bet_pct: float = 0.15, 
                  kelly_fraction: float = 0.25) -> float:
    """
    Probability-based bet sizing (Lopez de Prado Ch.10).
    bet_size = (2 * prob - 1) * max_bet * kelly_fraction
    
    prob=0.5 → bet=0 (no edge)
    prob=0.6 → bet=20% of max
    prob=0.7 → bet=40% of max  
    prob=1.0 → bet=max
    
    Kelly fraction scales down for safety (0.25 = quarter Kelly).
    """
    if prob_correct <= 0.5:
        return 0.0
    raw = (2 * prob_correct - 1) * max_bet_pct * kelly_fraction
    return round(min(raw, max_bet_pct), 6)

def zscore_to_prob(zscore: float) -> float:
    """
    Convert Z-score to probability of correct mean reversion.
    Uses normal CDF: higher |z| = higher confidence.
    z=2.0 → prob=0.977
    z=1.5 → prob=0.933
    z=1.0 → prob=0.841
    """
    import math
    return 0.5 * (1 + math.erf(abs(zscore) / math.sqrt(2)))


def meta_label(prices: list, zscore: float, volume: float = None,
               avg_volume: float = None, rsi: float = 50.0,
               bbw: float = 4.0, lookback: int = 30) -> dict:
    """
    Meta-Labeling (Lopez de Prado Ch.3).
    Secondary model that decides WHETHER to take a primary signal.
    Features: zscore magnitude, RSI distance from neutral,
              volume ratio, BBW, recent return direction.
    Returns: prob_take (probability trade is worth taking),
             take (bool), confidence.
    """
    if len(prices) < lookback + 1:
        return {"prob_take": 0.5, "take": True, "confidence": 0.0, "error": "insufficient data"}

    # Feature 1: Z-score magnitude (higher = stronger signal)
    f_zscore = min(abs(zscore) / 3.0, 1.0)

    # Feature 2: RSI distance from 50 (neutral = 0, extreme = 1)
    f_rsi = abs(rsi - 50.0) / 50.0

    # Feature 3: Volume confirmation (above avg = 1, below = 0)
    f_vol = 1.0 if (volume and avg_volume and volume >= avg_volume) else 0.5

    # Feature 4: BBW regime (tight = good for MR, wide = bad)
    f_bbw = 1.0 if bbw < 4.0 else (0.5 if bbw < 8.0 else 0.0)

    # Feature 5: Recent return alignment
    # For BUY signal: recent prices should be falling (mean reversion opportunity)
    recent_return = (prices[-1] - prices[-lookback]) / prices[-lookback] if prices[-lookback] > 0 else 0
    if zscore < 0:  # buy signal — want recent fall
        f_align = 1.0 if recent_return < 0 else 0.3
    else:  # sell signal — want recent rise
        f_align = 1.0 if recent_return > 0 else 0.3

    # Weighted combination
    weights = [0.35, 0.15, 0.20, 0.15, 0.15]
    features = [f_zscore, f_rsi, f_vol, f_bbw, f_align]
    score = sum(w * f for w, f in zip(weights, features))

    # Convert to probability (sigmoid-like)
    import math
    prob_take = 1 / (1 + math.exp(-10 * (score - 0.5)))
    take = prob_take >= 0.55
    confidence = abs(prob_take - 0.5) * 2

    return {
        "prob_take":   round(prob_take, 4),
        "take":        take,
        "confidence":  round(confidence, 4),
        "score":       round(score, 4),
        "features":    {
            "zscore_mag": round(f_zscore, 3),
            "rsi_dist":   round(f_rsi, 3),
            "vol_conf":   round(f_vol, 3),
            "bbw_regime": round(f_bbw, 3),
            "alignment":  round(f_align, 3),
        }
    }


def wyckoff_analysis(bars: list, lookback: int = 20) -> dict:
    """
    Wyckoff Volume-Price Analysis (Wyckoff Method, Sections 3M, 5M, 14M).
    Analyzes effort vs result, climaxes, springs, upthrusts, no supply/demand.
    bars: list of dicts with open, high, low, close, volume keys.
    """
    if len(bars) < lookback + 1:
        return {"wyckoff_bias": "neutral", "error": "insufficient data"}

    closes  = [b["close"]  for b in bars]
    opens   = [b["open"]   for b in bars]
    highs   = [b["high"]   for b in bars]
    lows    = [b["low"]    for b in bars]
    volumes = [b["volume"] for b in bars]

    # Rolling averages
    avg_vol   = sum(volumes[-lookback:]) / lookback
    avg_range = sum(highs[i] - lows[i] for i in range(-lookback, 0)) / lookback

    # Current bar
    c_vol   = volumes[-1]
    c_range = highs[-1] - lows[-1]
    c_close = closes[-1]
    c_open  = opens[-1]
    c_high  = highs[-1]
    c_low   = lows[-1]

    # Relative metrics
    rel_vol   = c_vol / avg_vol if avg_vol > 0 else 1.0
    rel_range = c_range / avg_range if avg_range > 0 else 1.0
    efficiency = rel_range / rel_vol if rel_vol > 0 else 1.0
    close_pos  = (c_close - c_low) / c_range if c_range > 0 else 0.5

    # 1. Buying Climax — high vol, price closes in lower half, effort wasted
    is_buying_climax = (
        rel_vol > 2.5 and
        close_pos < 0.4 and
        c_close < c_open
    )

    # 2. Selling Climax — high vol, price closes in upper half, sellers absorbed
    is_selling_climax = (
        rel_vol > 2.5 and
        close_pos > 0.6 and
        c_close > c_open
    )

    # 3. Spring — breaks below recent low then recovers, low volume
    recent_low  = min(lows[-lookback:-1])
    is_spring = (
        c_low < recent_low and
        c_close > recent_low and
        rel_vol < 1.2
    )

    # 4. Upthrust — breaks above recent high then falls back, low volume
    recent_high = max(highs[-lookback:-1])
    is_upthrust = (
        c_high > recent_high and
        c_close < recent_high and
        rel_vol < 1.2
    )

    # 5. No Supply — narrow range, low volume, up close = no sellers
    no_supply = (
        rel_range < 0.5 and
        rel_vol < 0.5 and
        c_close > c_open
    )

    # 6. No Demand — narrow range, low volume, down close = no buyers
    no_demand = (
        rel_range < 0.5 and
        rel_vol < 0.5 and
        c_close < c_open
    )

    # 7. Stopping Volume — high vol, narrow range, closes upper half = buyers absorbing
    is_stopping_volume = (
        rel_vol > 2.0 and
        rel_range < 0.5 and
        close_pos > 0.6
    )

    # 8. Effort vs Result divergence
    # High effort (volume) but low result (range) = absorption = reversal likely
    effort_result_divergence = rel_vol > 1.5 and efficiency < 0.4

    # Bias synthesis
    bullish_signals = sum([
        is_selling_climax, is_spring, no_supply,
        is_stopping_volume, (effort_result_divergence and c_close > c_open)
    ])
    bearish_signals = sum([
        is_buying_climax, is_upthrust, no_demand,
        (effort_result_divergence and c_close < c_open)
    ])

    if is_spring or is_selling_climax:
        wyckoff_bias = "strong_bullish"
    elif is_upthrust or is_buying_climax:
        wyckoff_bias = "strong_bearish"
    elif bullish_signals > bearish_signals:
        wyckoff_bias = "bullish"
    elif bearish_signals > bullish_signals:
        wyckoff_bias = "bearish"
    else:
        wyckoff_bias = "neutral"

    return {
        "wyckoff_bias":            wyckoff_bias,
        "effort":                  round(rel_vol, 3),
        "result":                  round(rel_range, 3),
        "efficiency":              round(efficiency, 3),
        "close_position":          round(close_pos, 3),
        "is_buying_climax":        is_buying_climax,
        "is_selling_climax":       is_selling_climax,
        "is_spring":               is_spring,
        "is_upthrust":             is_upthrust,
        "no_supply":               no_supply,
        "no_demand":               no_demand,
        "is_stopping_volume":      is_stopping_volume,
        "effort_result_div":       effort_result_divergence,
        "bullish_signals":         bullish_signals,
        "bearish_signals":         bearish_signals,
    }
