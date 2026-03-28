# AlphaBot Research Enhancement Roadmap

## Objective

Turn the research library in `/sdcard/DCIM/attachmentstermux` into a controlled improvement pipeline for AlphaBot. The goal is to improve reliability first, then risk control, then strategy quality, rather than adding theory directly into production.

## Implementation Order

1. Engine lifecycle and observability
2. Canonical state and persistence
3. Validation and backtesting standards
4. Risk model upgrades
5. Forex execution and session filters
6. Structured ICT feature extraction
7. Portfolio coordination and regime routing

## Source-To-Module Mapping

### Immediate Use

#### `Mark Needham - Building Real-Time Analytics Systems...pdf`
- Target modules:
  - `backend/core/engine.py`
  - `backend/api/main.py`
  - `frontend/src/App.jsx`
- Purpose:
  - readiness state
  - event flow clarity
  - telemetry and status health
- Priority:
  - P1

#### `Abdullah Karasan - Machine Learning for Financial Risk Management with Python...pdf`
- Target modules:
  - `backend/core/execution/risk_manager.py`
  - `backend/api/main.py`
- Purpose:
  - VaR improvements
  - exposure throttling
  - confidence-aware risk controls
- Priority:
  - P1

#### `Sofien Kaabar - Mastering Financial Pattern Recognition...pdf`
- Target modules:
  - `backend/backtester/`
  - `backend/core/strategies/`
- Purpose:
  - pattern labeling
  - signal feature extraction
  - reusable backtest fixtures
- Priority:
  - P1

#### `Beat the Forex Dealer...pdf`
- Target modules:
  - `backend/core/strategies/forex_mr.py`
  - `backend/core/execution/forex_paper_trader.py`
- Purpose:
  - session filters
  - rollover protection
  - execution realism for forex
- Priority:
  - P1

#### `Naked Forex...pdf`
- Target modules:
  - `backend/core/strategies/forex_mr.py`
  - future forex signal modules
- Purpose:
  - price action filters
  - cleaner entry confirmation logic
- Priority:
  - P1

### Secondary Use

#### `Quantitative Trading with R...pdf`
- Target modules:
  - `backend/backtester/`
  - `backend/core/strategies/`
- Purpose:
  - robustness checks
  - walk-forward validation
  - parameter discipline
- Priority:
  - P2

#### `Quantitative Portfolio Management...pdf`
- Target modules:
  - `backend/core/execution/risk_manager.py`
  - future portfolio router
- Purpose:
  - strategy capital allocation
  - correlated exposure controls
  - portfolio-level risk budgets
- Priority:
  - P2

#### `Global Macro Trading...pdf`
- Target modules:
  - future regime classifier
  - strategy gating layer
- Purpose:
  - macro regime throttles
  - when to favor MR vs trend vs flat exposure
- Priority:
  - P2

#### `ICT *.pdf`, `10142025_ICT_Notes 01.pdf`
- Target modules:
  - future structured signal feature set
  - xaufx modules
  - forex session filters
- Purpose:
  - liquidity sweep features
  - session windows
  - market structure state
- Priority:
  - P2

### Long-Horizon Reference

#### `Reinforcement Learning for Finance...pdf`
- Target modules:
  - research only for now
- Purpose:
  - future policy selection or regime adaptation
- Priority:
  - P3

#### `Arbitrage Theory in Continuous Time...pdf`
#### `Mathematics for Finance...pdf`
- Target modules:
  - none in the short term
- Purpose:
  - theoretical grounding
- Priority:
  - P3

#### `Blockchain blueprint for a new economy...pdf`
- Target modules:
  - none currently
- Priority:
  - P3

## First Three Concrete Enhancements

### 1. Engine Lifecycle State
- Problem removed:
  - dashboard ambiguity during restore and warmup
- Files:
  - `backend/core/engine.py`
  - `backend/api/main.py`
  - `frontend/src/App.jsx`

### 2. Risk Budget Layer
- Problem removed:
  - flat risk treatment across regimes and strategies
- Planned files:
  - `backend/core/execution/risk_manager.py`
  - `backend/api/main.py`

### 3. Forex Session and Quality Filters
- Problem removed:
  - poor execution windows and mixed-quality forex entries
- Planned files:
  - `backend/core/strategies/forex_mr.py`
  - `backend/core/execution/forex_paper_trader.py`
  - `backend/backtester/backtest_forex.py`

## Promotion Rules

No new strategy feature should be promoted unless it passes:

1. deterministic replay and state consistency
2. backtest metrics above baseline
3. out-of-sample or walk-forward check
4. paper-trading stability
5. dashboard visibility and explainability
