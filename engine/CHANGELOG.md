# Changelog

All notable changes to the LEVIATHAN engine are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- **Day 13** — Gamma calibration cron (`engine/scripts/calibrate_gamma.py`): nightly
  power-law gamma fit against SlippageFeedbackCollector history (48h window).
  Writes `slippage.gamma` + `slippage.gamma_calibrated=true` to `engine.json`.
  Gate: R² > 0.6, gamma ∈ [0.2, 1.0], ≥ 100 samples.  Atomic write with backup.
  Synthetic test harness (`tests/unit/scripts/test_gamma_calibration.py`, 7 tests).
