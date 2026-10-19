package turtle

import (
	"context"
	"log"
	"time"
)

// Selector supplies a fresh ranking on demand. Supplied by the caller so this
// package stays free of the flow engine, and so the rotation can be tested with a
// deterministic ranking.
type Selector func(at time.Time) []Scored

// RunRotation rotates the watchlist every RotationInterval while a session is
// running, and does nothing while it is not.
//
// IT LOOPS RATHER THAN TICKING UNCONDITIONALLY, and it checks the session state on
// each pass rather than being started and stopped with the session. That is
// deliberate: a goroutine that has to be started and stopped in step with a
// user-triggered button is a goroutine that will eventually be started twice or
// stopped never, and both failures look like a stuck watchlist rather than an
// error.
func (e *Engine) RunRotation(ctx context.Context, sel Selector, interval time.Duration) {
	if interval <= 0 {
		interval = RotationInterval
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	log.Printf("turtle: watchlist rotation every %s (runs only while a session is live)", interval)

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if e.Session().State != StateRunning {
				continue
			}
			if sel == nil {
				continue
			}
			now := time.Now()
			fresh := sel(now)
			if len(fresh) == 0 {
				// An empty ranking is not a reason to empty the watchlist. In a
				// quiet market the selector returns nothing, and rotating to
				// nothing would leave the session unable to trade on the alert that
				// eventually fires -- the same silent-failure shape as the empty
				// watchlist at session start.
				continue
			}
			rec := e.RotateWatchlist(fresh, "scheduled rotation")
			if len(rec.Added) > 0 || len(rec.Removed) > 0 {
				log.Printf("turtle rotation %d: +%d -%d (held retained: %d)",
					rec.Seq, len(rec.Added), len(rec.Removed), len(rec.HeldRetained))
			}
		}
	}
}
