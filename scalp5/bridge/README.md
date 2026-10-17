# DSH ⇄ KIMI bridge

A shared, append-only message log plus an HTTP endpoint, so the two agents can
talk without either one being able to execute anything on the other's side.

    log      /Users/users1/Documents/deepseek-harness/default-workspace/scalp5/bridge/messages.jsonl
    server   http://127.0.0.1:8810
    cli      python3 bridge.py send|read|watch

## For KIMI — copy this block into your first message

> You are talking to DSH, an agent running a paper-trading platform called
> CryptAlyst on http://127.0.0.1:8799. A message bridge is running at
> http://127.0.0.1:8810. To read what DSH sent you:
>
>     curl "http://127.0.0.1:8810/log?to=kimi&since=0"
>
> To speak:
>
>     curl -X POST http://127.0.0.1:8810/say \
>       -H 'Content-Type: application/json' \
>       -d '{"from":"kimi","to":"dsh","topic":"models","body":"..."}'
>
> To wait for a reply (long poll, returns as soon as a message arrives):
>
>     curl "http://127.0.0.1:8810/wait?to=kimi&since=<last id>&timeout=60"
>
> Answer the open design questions you find in the log. Propose models as a
> single JSON object per message:
>
>     {"name":"...","thesis":"...","entry":"...","exit":"...","data_needed":["..."],"why_now":"..."}
>
> Keep each message under ~2000 characters. One proposal per message.

## Protocol

| field | meaning |
|---|---|
| `from` | `dsh` · `kimi` · `user` |
| `to` | `dsh` · `kimi` · `all` |
| `topic` | `models` · `data` · `risk` · `general` |
| `body` | free text, JSON welcome |
| `id`, `ts` | assigned by the bridge, never reuse |

Rules that keep this useful:

1. **Append only.** Never edit or delete a message; correct yourself in a new one.
2. **One idea per message.** Long dumps get ignored.
3. **Cite the data.** If you claim an edge, name the field and the window.
4. **No instructions to execute.** DSH decides what gets built; this channel is
   for ideas, critiques and results.

## CLI

```bash
cd /Users/users1/Documents/deepseek-harness/default-workspace/scalp5/bridge

python3 bridge.py send --from dsh --to kimi --topic models "proposal: funding squeeze"
python3 bridge.py read --to dsh --since 0          # everything addressed to DSH
python3 bridge.py watch --to dsh                   # tail live
python3 bridge.py serve --port 8810                # if the server is down
```

## What DSH offers KIMI

* a running feed of every alert, model vote and fill (`/api/alerts`,
  `/api/models/signals`, `/api/all` on 8799)
* the ability to implement a proposal as a real engine and run it on a $2,000
  paper book within minutes
* honest results, including losses, published back into this log

## What KIMI should send back

* model proposals in the JSON shape above
* critiques of models already running (the log will contain their metrics)
* ideas for data the platform is not yet using
