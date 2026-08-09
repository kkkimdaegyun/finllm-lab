# Final report source map

| Claim family | Authoritative source |
|---|---|
| v0.1 model/latency/throughput/VRAM | `results/2026-08-08c-*.json` |
| Evidence type boundary | `schemas/run-result.schema.json` |
| Policy thresholds | `configs/profiles.json` |
| v0.2 all-stage gate | `ops/evidence/final-rehearsal/gate-all.json` |
| Actual service/GPU/metrics | `ops/evidence/final-rehearsal/` |
| Incident timing and impact | `ops/incidents/INC-003-api-outage-container-rollback.md` |
| Immutable release identity | `ops/release/history/2026-08-09-v02-container-good.json` |
| Rollback state transition | `ops/release/rollback-log.jsonl` |
| A/B metric contract | `docs/cross-review/interface-contract-v0.2.md` |

`NOT_EXECUTED`: actual 24GB native validation, remote self-hosted A6000 CI execution,
production corpus/traffic validation.
