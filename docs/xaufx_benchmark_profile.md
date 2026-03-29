# XAU/FX Locked Benchmark Profile

## Goal

Promote the strongest documented XAUUSD NDOG Asia validation case into a repo-controlled benchmark so AlphaBot can evolve toward an industry-grade realtime trading system without losing its research baseline.

This benchmark is the canonical reference for:
- reproducible strategy evaluation
- future regression checks
- promotion gating
- benchmark drift detection

## Locked Benchmark

Profile name:
- `xauusd_best_validated_v1`

One-command run:

```bash
cd /root/alphabot
source /root/alphabot-venv/bin/activate
python -m backend.backtester.xaufx.backtest_xau_ndog_asia --profile xauusd_best_validated_v1
```

The profile is locked. Benchmark-managed parameters cannot be overridden when `--profile xauusd_best_validated_v1` is used.

## Freeze Reproducible Inputs

Capture the exact provider dataset used by the benchmark:

```bash
cd /root/alphabot
source /root/alphabot-venv/bin/activate
python -m backend.backtester.xaufx.backtest_xau_ndog_asia \
  --profile xauusd_best_validated_v1 \
  --freeze-snapshot
```

This writes:
- `reports/benchmarks/xauusd_best_validated_v1_dataset.json`

The snapshot stores:
- provider source
- fetch timestamp
- exact intraday and daily candles
- dataset hash

Replay the benchmark from frozen local inputs:

```bash
cd /root/alphabot
source /root/alphabot-venv/bin/activate
python -m backend.backtester.xaufx.backtest_xau_ndog_asia \
  --profile xauusd_best_validated_v1 \
  --snapshot @profile
```

## Automated Regression Check

Run the locked benchmark against the frozen dataset and fail on metric drift:

```bash
cd /root/alphabot
source /root/alphabot-venv/bin/activate
python -m backend.backtester.xaufx.benchmark_regression --profile xauusd_best_validated_v1
```

This check enforces:
- exact dataset hash match
- exact trade count and side distribution
- no drift in key metrics beyond the profile tolerances

Current baseline snapshot hash:
- `e4ef7288432aba280a3e5c905a3b163e04064b83de46e3e7fa9759d63d53221b`

## Promotion Governance

XAU/FX promotion now distinguishes:
- `research_winner`
- `promotable_baseline`
- `candidate`
- `reject`

Promotion is no longer based only on train/test and walk-forward returns. It also considers:
- cost-stress retention under higher spread assumptions
- slippage-proxy stress retention under even higher spread assumptions
- session concentration risk from the entry-hour distribution

## Structured Validation Artifacts

Serious XAU/FX validation runs now emit structured artifacts under:
- `reports/validation_artifacts/xaufx/`

Artifact identity is keyed by:
- runner
- `config_hash`
- `code_version`
- `run_id`

These artifacts are intended to become the durable handoff format for:
- analytics registration
- benchmark provenance
- promotion review
- future dashboard/API exposure

## Promotion Workflow

Promotion now has a dedicated GitHub Actions workflow:
- `.github/workflows/xaufx_promotable_baseline.yml`

Use it with `workflow_dispatch` from the branch that produced the validation result.

The workflow:
- selects the latest XAU/FX validation artifact or an explicitly provided path
- refuses promotion unless the verdict is `promotable_baseline` or `research_winner`
- creates a draft release tagged as `xaufx-baseline-<run_id>`
- opens or reuses a draft PR into the selected base branch

## Fixed Parameters

The profile encodes the documented best validated case:

| Parameter | Value |
|---|---:|
| `bars` | `10000` |
| `capital` | `10000.0` |
| `risk` | `0.005` |
| `spread` | `0.5` |
| `target_r` | `2.0` |
| `no_fvg` | `true` |
| `session_cap` | `1` |
| `stop_buffer` | `2.5` |
| `breakeven_r` | `1.0` |
| `trail_r` | `1.5` |
| `allow_hours` | `19,20` |
| `max_risk_distance` | `60.0` |
| `max_risk_to_range` | `0.7` |

Default CSV output:
- `reports/benchmarks/xauusd_best_validated_v1_trades.csv`

## Source of Truth

Code:
- [backtest_xau_ndog_asia.py](/root/alphabot/backend/backtester/xaufx/backtest_xau_ndog_asia.py)
- [benchmark_profiles.py](/root/alphabot/backend/backtester/xaufx/benchmark_profiles.py)

Historical reference note:
- `/sdcard/DCIM/attachmentstermux/alphabot_xaufx_best_test_case.md`

## Intended Role

This profile is the benchmark foundation for the next stages:
1. frozen dataset snapshots
2. automated regression validation
3. robustness stress checks
4. promotion governance
5. structured result artifacts keyed by config and code version
