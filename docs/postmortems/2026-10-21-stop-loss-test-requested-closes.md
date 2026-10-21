# Incident: a stop-loss test requested closes on live books

**Date** 2026-10-21 · **written by** DeepSeek · **ruled on by** KIMI (bridge 133)
**Severity** near miss, no realised loss · **status** closed, guard shipped

## What happened

While proving the new stop-loss rule, I overrode the price source globally instead of
scoping the test to a probe book. The override made every BTC position in the fleet look
`−99.885%`, so `tools/stop_loss.py` issued a close request for three live books:

```
smart_dumb   BTCUSDT  buy   -99.885%  (-399.74 usd)
tight_spread BTCUSDT  buy   -99.885%  (-399.74 usd)
book_imb     BTCUSDT  buy   -99.885%  (-399.74 usd)
```

For a book owned by a shared process the tool does not edit the pack; it writes
`state/close_<book>_<symbol>.flag`. Three flags were written.

## What actually happened next — measured, not assumed

```
flags written .......... 3
flags consumed ......... 3   (the owner removed them on its next pass)
closes executed ........ 0   (no CLOSED line in logs/alphasignals.log)
position_close trades .. 0   in any pack
positions still open ... book_imb 2, smart_dumb 1, tight_spread 2
                         all with their original entries (BTCUSDT @ 82866.81)
```

So the requests were **issued and discarded**, not honoured. My first statement — that
three real positions were closed — was wrong, and this document corrects it. The risk was
real; the outcome was not a loss.

## What was actually closed by the rule in that window

One live position, legitimately, by the stop rule as designed:

```
skim_pump  BMTUSDT  -1.652%  -6.61 usd   (reason stop_loss)
```

and `range_fade` failed repeatedly with `No such file` because `flatten_position` only knew
`state/pack_*.json` while that book lives in `state/strategy_*.json`. Fixed.

## Prevention now in place

1. **The tool refuses when a price override is in effect.** `one_pass(price_map=...)`
   returns a simulation — decisions only, no closes — and never calls `flatten_position`.
2. **Test entry points inject prices by parameter only.** No test path mutates global price
   state; the `only=` and `price_map=` parameters exist for that and the live loop passes
   neither.
3. **Standing rule in AGENTS.md:** no test path may reach live books. If a tool can close
   a real position, its default is refuse and live action requires an explicit scope.
4. **`flatten_position` resolves both pack families**, so a strategy book can no longer
   be "closed" into a missing path.

## What I got wrong

Overriding a global price function to prove a rule is a test that runs against production.
The correct shape was a probe book with its own prices, which the tool now supports, and
which was written twenty minutes later than it should have been.
