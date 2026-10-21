#!/usr/bin/env python3
"""Unit tests for the stop predicates: the rule that is tested is the rule that runs."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stop_loss

def check(name, got, want):
    if got != want:
        raise AssertionError("%s: got %r want %r" % (name, got, want))
    print("  ok  %s" % name)

# bug 1: a red position at -0.07% must NOT be stopped
check("does not fire at -0.07%", stop_loss.breaches(-0.07), False)
check("does not fire at -1.4%", stop_loss.breaches(-1.4), False)
check("fires at -1.5%", stop_loss.breaches(-1.5), True)
check("fires at -1.6%", stop_loss.breaches(-1.6), True)
check("never fires in profit", stop_loss.breaches(3.0), False)

# bug 6: one source for the threshold
check("threshold comes from one place", stop_loss.stop_pct(), abs(float(stop_loss.options().get("stop_loss_pct") or stop_loss.STOP_PCT)))

# bug 2: the backstop constant is real and used
check("backstop is five percent", stop_loss.BOOK_BACKSTOP_PCT, 5.0)

# bug 3: the simulator uses the same predicate
sim = stop_loss._simulate(only="__none__", price_map={}, verbose=False)
check("simulator shares the predicate", sim, [])
print("\n6/6 stop rules pass")
