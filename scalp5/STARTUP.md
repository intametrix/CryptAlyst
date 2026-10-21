# Startup — every command `scripts/start.sh` runs

`./scripts/start.sh` brings the platform up from a clean slate. Every stage either
proves itself or stops the run naming the stage that failed.

```bash
./scripts/start.sh                 # kill everything, start everything, trading OFF
./scripts/start.sh --trade         # the same, plus the books
./scripts/start.sh --keep          # start without killing anything running
./scripts/start.sh --keep-broker   # restart the platform, leave RabbitMQ alone
./scripts/start.sh --help          # usage
```

Trading is off unless asked for: the stop flag is written **before** any service
starts, so a book that somehow comes up finds trading already blocked.

---

## 0 · Kill

Anything from a previous run is stopped and its ports are waited for.

```bash
pkill -f "supervisor.py"
pkill -f "models10.py crypto_pack.py executor.py hybrids.py alpha_rank.py"
pkill -f "alpha_signals.py skim_pump.py curve_rider.py churn.py multi_models.py"
pkill -f "stats_models.py sympathy_lag.py slow_edges.py crypto_edges.py"
pkill -f "turtle_multi.py swing_v2.py swing_turtle.py scalp_fast.py skim.py"
pkill -f "skim_ai.py grid.py hft.py movers.py flash.py fast_ignition.py"
pkill -f "market_agent.py signals.py heartbeat.py optimiser_svc.py master.py"
pkill -f "bin/marketdata bin/recorder bin/busconsumer bin/healthpub bin/signalbus bin/envelopeapi"

# anything still holding a port is escalated, not ignored
pkill -9 -f "bin/marketdata bin/recorder bin/busconsumer bin/healthpub bin/signalbus bin/envelopeapi master.py"

# the broker is part of "everything" unless --keep-broker is passed
rabbitmqctl stop
pkill -f "beam.smp"
pkill -9 -f "beam.smp|epmd"
```

Waits for ports `8796 8798 8799 8801 8802 8803 8804 8805 8806 61613 15672` to be free.
If any is still held the run stops there.

## 1 · Dependencies

```bash
# broker: start it if it is not answering
/opt/homebrew/sbin/rabbitmq-server -detached
# then wait up to 45 s for STOMP on :61613 to accept CONNECT

# binaries: build only what is missing
cd core && go build -o bin/ ./cmd/...
# then assert all six exist: marketdata recorder envelopeapi busconsumer healthpub signalbus

# optional producers are named when absent, not silently skipped
core/tad       # reported present or "not built in this tree"
core/forecast  # same

# runtime and storage
command -v python3
touch state/lake3/.write-test && rm state/lake3/.write-test

# the exchange: a warning, not a fatal
curl -sS https://api.binance.com/api/v3/ping

# trading off before anything starts
printf '{"stopped_at": …}' > state/STOP
```

## 2 · Core services

| Command | Gate |
|---|---|
| `core/bin/marketdata --port 8796 --top 400` | `http://127.0.0.1:8796/health` |
| `core/bin/recorder --port 8801 --data ../state/lake3` | `http://127.0.0.1:8801/stats` |
| `core/bin/busconsumer --port 8798` | `http://127.0.0.1:8798/` |
| `core/bin/healthpub --every 5s` | started, log checked |
| `core/bin/signalbus --every 2s` | started, log checked |
| `python3 -u signals.py --every 10 --top 30` | process still alive after 3 s |
| `core/tad --addr 127.0.0.1:61613 --every 5s` | only if the binary exists |
| `core/forecast --addr 127.0.0.1:61613` | only if the binary exists |

The recorder takes optional stores, passed only when the environment names them:

```bash
--kv      $STORE_KV        # bbolt
--sql     $STORE_SQL       # sqlite: books and trades
--lsm     $STORE_LSM       # pebble
--rocksdb $STORE_ROCKS     # rocksdb, needs a -tags rocksdb build
--docs    $STORE_DOCS      # envelope documents as files
--mongo   $MONGO_URI       # envelope documents in mongodb
```

## 3 · Dashboards

| Command | Gate |
|---|---|
| `python3 -u master.py --host 0.0.0.0 --port 8799` | `http://127.0.0.1:8799/api/health` |
| `python3 -u heartbeat.py` | started |
| `core/bin/envelopeapi --port 8802 --dir ../state/lake3` | `http://127.0.0.1:8802/api/stats` |

## 4 · Books — only with `--trade`

```bash
rm -f state/STOP
python3 -u supervisor.py --every 60 --hours 8
```

The supervisor is checked for liveness after 5 s; if it is not running the script
stops rather than reporting success.

---

## Endpoints after a successful start

```
dashboard            http://127.0.0.1:8799
envelope explorer    http://127.0.0.1:8802
recorder stats       http://127.0.0.1:8801/stats
market data health   http://127.0.0.1:8796/health
broker STOMP         127.0.0.1:61613
broker management    http://127.0.0.1:15672
```

## Stopping

```bash
./scripts/stop.sh                  # the documented stop path
pkill -f 'bin/(marketdata|recorder|envelopeapi)'
pkill -f 'supervisor.py'           # books only
rabbitmqctl stop                   # the bus, if you want it down too
```

A stop is not a STOP: `state/STOP` is what blocks trading, and a stop leaves the books
as they were. Use `./scripts/start.sh --trade` to clear the flag and trade again.
