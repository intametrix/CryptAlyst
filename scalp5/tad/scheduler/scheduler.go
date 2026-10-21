// Package scheduler is the book-decision work scheduler: per-symbol "evaluate"
// events flow in, and a bounded worker pool evaluates each symbol once per burst.
//
// Design invariants (owner + KIMI ruling):
//   - coalescing: the same symbol arriving twice while still queued is one run,
//     not two (a "coalesced" counter records the drop).
//   - no lost wakeup: a symbol that cannot enter the bounded primary queue goes
//     to a SECONDARY queue that workers drain first, so it is eventually evaluated.
//   - bounded: the secondary queue is bounded; overflow evicts the oldest with a
//     counter, never silently.
//   - the producer never blocks: Submit is non-blocking.
package scheduler

import (
	"sync"
	"sync/atomic"
)

type Scheduler struct {
	mu         sync.Mutex
	cond       *sync.Cond
	queue      []string // primary FIFO, bounded
	dirtyQ     []string // secondary FIFO, bounded (the no-lost-wakeup queue)
	busy       map[string]bool
	processing map[string]bool
	dirty      map[string]bool // re-run requested while processing
	maxQueue   int
	maxDirty   int
	dropped    atomic.Int64 // primary full -> routed to secondary
	coalesced  atomic.Int64 // same symbol while still queued
	evicted    atomic.Int64 // secondary overflow
	eval       func(string)
	stop       chan struct{}
	wg         sync.WaitGroup
}

func New(queueCap, workers, maxDirty int, eval func(string)) *Scheduler {
	s := &Scheduler{
		queue:      []string{},
		dirtyQ:     []string{},
		busy:       map[string]bool{},
		processing: map[string]bool{},
		dirty:      map[string]bool{},
		maxQueue:   queueCap,
		maxDirty:   maxDirty,
		eval:       eval,
		stop:       make(chan struct{}),
	}
	s.cond = sync.NewCond(&s.mu)
	for i := 0; i < workers; i++ {
		s.wg.Add(1)
		go s.worker()
	}
	return s
}

// Submit requests an evaluation of sym. Non-blocking; never drops a symbol whose
// evaluation is still owed, except via the bounded-secondary eviction path.
func (s *Scheduler) Submit(sym string) {
	s.mu.Lock()
	if s.busy[sym] {
		if s.processing[sym] {
			// a new event during processing: re-run once after the current run
			s.dirty[sym] = true
		} else {
			// still queued, not yet started: coalesce into the queued run
			s.coalesced.Add(1)
		}
		s.mu.Unlock()
		return
	}
	s.busy[sym] = true
	if len(s.queue) < s.maxQueue {
		s.queue = append(s.queue, sym)
	} else {
		if len(s.dirtyQ) >= s.maxDirty {
			victim := s.dirtyQ[0]
			s.dirtyQ = s.dirtyQ[1:]
			delete(s.busy, victim)
			s.evicted.Add(1)
		}
		s.dirtyQ = append(s.dirtyQ, sym)
		s.dropped.Add(1)
	}
	s.cond.Signal()
	s.mu.Unlock()
}

func (s *Scheduler) worker() {
	defer s.wg.Done()
	for {
		s.mu.Lock()
		for len(s.queue) == 0 && len(s.dirtyQ) == 0 {
			select {
			case <-s.stop:
				s.mu.Unlock()
				return
			default:
			}
			s.cond.Wait()
		}
		var sym string
		if len(s.dirtyQ) > 0 { // secondary first: no lost wakeup
			sym = s.dirtyQ[0]
			s.dirtyQ = s.dirtyQ[1:]
		} else {
			sym = s.queue[0]
			s.queue = s.queue[1:]
		}
		s.processing[sym] = true
		s.mu.Unlock()

		s.run(sym) // run() clears processing+busy atomically with its dirty check
	}
}

// run evaluates sym, re-running once per dirty flag set while it was processing.
func (s *Scheduler) run(sym string) {
	for {
		s.eval(sym)
		s.mu.Lock()
		again := s.dirty[sym]
		if again {
			delete(s.dirty, sym)
		}
		if !again {
			// clear processing + busy in the SAME lock that observed dirty==false:
			// a Submit landing after this can only see busy==false (enqueue normally);
			// one landing before re-runs us via dirty. No orphaned dirty flag.
			delete(s.processing, sym)
			delete(s.busy, sym)
			s.mu.Unlock()
			return
		}
		s.mu.Unlock()
	}
}

func (s *Scheduler) Close() {
	close(s.stop)
	s.cond.Broadcast()
	s.wg.Wait()
}

// Stats exposes the observability counters for /health.
func (s *Scheduler) Stats() (dropped, coalesced, evicted, depth int64) {
	s.mu.Lock()
	depth = int64(len(s.queue) + len(s.dirtyQ))
	s.mu.Unlock()
	return s.dropped.Load(), s.coalesced.Load(), s.evicted.Load(), depth
}
