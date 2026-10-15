# Iteration protocol

**The loop, per increment:**
1. **Scope it small.** One change, one owner, one acceptance test.
2. **Time-box it.** 15 minutes target. If it is not done at 30, split it or abandon it — do not let one increment eat the round.
3. **Run the acceptance test.** A command, not an opinion. Done = it passes.
4. **Post the result on the comms channel** (`tools/comms.sh send ...`) and take the next item.
5. **Never claim done without the test output.** No exceptions; that is how a demo becomes a liability.

**Coordination:** `tools/comms.sh` is the channel. `wait <who>` blocks until work arrives,
so neither agent idles at a prompt. Claim an item by posting `CLAIM <id>` before starting.

## Backlog

| id | owner | goal | acceptance test |
|---|---|---|---|
| I-01 | deepseek | durable alert history + bright red/green marker colours | `curl /api/klines?symbol=<alerted>` shows `marks[].color` green/red and `alerts.jsonl` grows |
| I-02 | kimi | fix duplicate `Report` in `internal/ledger/metrics.go` | `go build ./internal/ledger/ && go vet ./internal/ledger/` |
| I-03 | kimi | Go test for the ledger | `go test ./internal/ledger/` passes |
| I-04 | deepseek | engine exposes per-symbol 10s flow bins | `curl /api/flow/history?symbol=X` returns bins with `t, flow_quote, burst_multiple` |
| I-05 | deepseek | indicators stay drawn as the chart advances | chart shows indicator lines at the right edge after 60s of polling |
| I-06 | subagent | all pending UI (marks colours, indicator persistence, drill-down) | `pnpm build` exit 0 + `tsc -b --force` clean |
| I-07 | deepseek | verify heat glow survives a restart | lit tickers reappear within 60s of restart |

**Rule for anything that touches a running process:** restart it, then prove the
behaviour with a curl, not by reading the code.
