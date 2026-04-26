# gemini advisor artifact

- Provider: gemini
- Exit code: 0
- Created at: 2026-04-18T10:21:07.349Z

## Original task

Same review: src/infra/fx/krw_rate_provider.py (Upbit poll 30s), wired in real_signal_producer, XE-KRW gated by config flag, PHOENIX Step 2-3 (USDT-only XE, KRW disabled). Check correctness + concurrency + fallback. 100 words.

## Final prompt

Same review: src/infra/fx/krw_rate_provider.py (Upbit poll 30s), wired in real_signal_producer, XE-KRW gated by config flag, PHOENIX Step 2-3 (USDT-only XE, KRW disabled). Check correctness + concurrency + fallback. 100 words.

## Raw output

```text
I will begin by reviewing the `src/infra/fx/krw_rate_provider.py` and its integration in `src/producers/real_signal_producer.py` to verify the polling interval, configuration gating, and PHOENIX compliance.
I will examine the `src` directory to locate the correct path for the signal producer and then proceed with the review of the FX provider logic.
I will search for "real_signal_producer" within the `src` directory to identify the relevant file and then review both the FX provider and its integration.
I will read `src/infra/fx/krw_rate_provider.py` to check the Upbit polling interval, configuration gating, and PHOENIX compliance.
I will now examine `src/core/real_signal_producer.py` to verify the integration of the FX provider, check for configuration gating of XE-KRW, and ensure adherence to PHOENIX Step 2-3 logic.
I will examine `config/engine.json` and `settings.toml` to verify the `xe_krw_enabled` setting and ensure it aligns with PHOENIX Step 2-3 requirements.
`src/infra/fx/krw_rate_provider.py` correctly implements Upbit polling for the `KRW-USDT` ticker with a **30s interval** (`_POLL_INTERVAL_S = 30.0`). It is seamlessly wired into `RealDataSignalProducer`, where `xe_krw_enabled` in `engine.json` (currently `false`) serves as the gatekeeper. 

Adherence to **PHOENIX Step 2-3** is verified: XE-KRW is disabled by default, and `_evaluate_spot_futures` explicitly skips KRW exchanges in live mode. The implementation is concurrency-safe within the asyncio loop, using atomic updates for the rate. Fallback logic is robust, reverting to the `engine.json` rate if data exceeds the **60s staleness threshold**.


YOLO mode is enabled. All tool calls will be automatically approved.
Loaded cached credentials.
YOLO mode is enabled. All tool calls will be automatically approved.
Error executing tool list_directory: Error: Failed to list directory.

```

## Concise summary

Provider completed successfully. Review the raw output for details.

## Action items

- Review the response and extract decisions you want to apply.
- Capture follow-up implementation tasks if needed.
