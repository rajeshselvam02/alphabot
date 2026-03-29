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
