# Communication protocol

Two agents and a human work on this repository. This file defines how they talk,
what the commands mean, and who is actually in charge. `tools/comms.sh` is the
transport; this document is the contract.

## Control

**The human has control. Always. Without exception.**

Neither agent owns this project, and neither agent's plan outranks an
instruction from the human. If an agent believes an instruction is wrong, it says
so **once**, briefly, and then does what was asked. It does not stall, and it
does not silently substitute its own judgement for a direct instruction.

Both agents declare this in their own `YOU-HAVE-CONTROL` file, in their own
words, so the commitment is explicit rather than assumed.

## Rule 1 — Plan, share, agree, THEN build

**No half-work. Ever.**

Before writing code for anything larger than a one-line fix:

1. **Write the plan.** Goal, the split of work by owner, the acceptance test,
   and the open questions where you are genuinely unsure.
2. **Share it with every agent** on the channel
   (`tools/comms.sh send <agent> "..."`), labelled `PLAN <id>`.
3. **Get agreement.** Each agent replies `AGREE`, or `DISAGREE <reason>`, or
   `AMEND <change>`. Silence is not agreement — chase it.
4. **Only then build.**

The failure this prevents is real and happened: an agent began implementing a
feature, the work was interrupted half-applied, and it left `go build ./...`
**broken** — a tree that neither agent could build on, from a change nobody had
agreed to. Half-work is worse than no work, because the next agent inherits a
minefield instead of a clean starting point.

**Corollaries:**
- A `PLAN` that nobody has answered is not approved. Do not start.
- If you must touch a file mid-plan, leave the tree buildable when you stop.
- Disagreement is cheap before the work and expensive after it. Say
  `DISAGREE` early and loudly.
- The human's instruction is the goal. The plan is only *how*, and it is the
  team's job to agree it — not one agent's to assume.

## Commands

Imperatives, not suggestions. On receiving one, drop what you are doing.

| command | meaning | expected response |
|---|---|---|
| `ANSWER NOW` | Stop. Reply to this specific question immediately. | A direct answer within one turn, even if partial. "I don't know yet" is valid; silence is not. |
| `STATUS NOW` | Report state: done, in progress, blocked. | Honest status, including anything broken. |
| `STOP` | Cease work immediately. Start nothing new. | Acknowledge. Leave the tree buildable. |
| `RESUME` | Continue from where you stopped. | Acknowledge and continue. |
| `HANDOVER` | Write your state into `JOURNAL.md` so the other agent can continue. | Journal entry pushed, then acknowledge. |
| `SYNC` | Share state so we do not collide. | File ownership, current state, next action. |

Urgent messages are also written to `.comms/ATTENTION.<agent>` — a single flag
file. A JSONL line is easy to skim past; a flag file is not. `comms.sh status`
announces it, and reading your inbox clears it.

## Rules of engagement

1. **Never stop iterating.** Between increments, park on
   `tools/comms.sh wait <you> <seconds>`. An idle prompt is a wasted agent. If
   there is genuinely nothing to do, say so and propose the next increment.
2. **Prove it with a command, not by reading the code.** Every increment carries
   an acceptance test. A claim without output is not a claim.
3. **Suspect the test before the code.** Twice a broken *fixture* made working
   code look broken. Both times the code was fine.
4. **State ownership before touching files**, so two agents never write the same
   file at once.
5. **Never claim done without the acceptance output pasted.** No exceptions.
   This is how a demo becomes a liability.
6. **Flag uncertainty explicitly.** "Measured from 210 prior excursions" and
   "prior, insufficient history" are different claims and must never look the
   same. No bare numbers that appear measured when they are not.
7. **Leave the tree buildable.** Never leave `go build ./...` broken at a
   handover.
8. **Journal the decision, not just the change.** `JOURNAL.md` records *why*,
   because the reasoning is lost first and costs the most to rediscover.

## Transport

```bash
tools/comms.sh send <to> "<message>" [subject] [--urgent|--answer-now]
tools/comms.sh answer-now <to> "<question>"   # flagged imperative
tools/comms.sh urgent <to> "<message>"
tools/comms.sh read <who>                     # unread, marks read, clears flag
tools/comms.sh peek <who>                     # read without marking
tools/comms.sh wait <who> [secs]              # block until work arrives
tools/comms.sh status                         # counts + unanswered imperatives
tools/comms.sh log [n]                        # shared two-way log
```

`<to>` is `deepseek` or `kimi`. Sending as someone else requires
`COMMS_FROM=<you>`, so authorship is always explicit.

## HUMAN instructs

When the human types an instruction, it is delivered as **`HUMAN instructs`** —
always `ANSWER NOW`, always to **both** agents, never a suggestion.

The transport is one command:

```bash
tools/comms.sh human "<the instruction, verbatim>"
```

**What each agent must do on receiving `HUMAN instructs`:**

1. **Acknowledge with its understanding in one line** — so a
   misunderstanding is caught in seconds rather than after an hour of work.
2. **Propose its own share, and what it needs from the other agent.** Not "ok"
   — an actual decomposition. The team figures out *how*; the human has already
   said *what*.
3. **Never split silently.** If two agents would touch the same file, that is
   resolved on the channel before any edit.
4. **Then execute, and report with the acceptance output.**

The human's words are relayed **verbatim**, not paraphrased into an agent's
preferred framing. If an agent believes the instruction is wrong, it says so
once, briefly, in its acknowledgement — then complies.

**Why both agents:** an instruction given only to one agent produces a plan that
the other cannot execute against. Sharing it at the moment it is issued is what
makes "the team figures out how" possible instead of aspirational.

## Rule 0c — polling must not consume

**Added after a real failure, again.** `poll` originally marked messages read as
it printed them. Because poll runs as a background job, its output went
somewhere nobody looks: mail was being **consumed invisibly**. Another agent
replied `AGREE` with three amendments, the offset advanced, `status` reported
"0 unread", and the human had to tell us the reply had been sent.

Polling and reading are now separate operations with separate offsets:

- `poll` **notifies** — prints new mail, advances only its own `.polled` offset,
  and says `STILL UNREAD until you run: comms.sh read <who>`.
- `read` **consumes** — advances the read offset and clears the attention flag.

**The check that matters:** `comms.sh status` shows the unread count. If it is
non-zero, you have not read your mail, no matter what your poll loop printed.

## Signalling vocabulary

Adopted from KIMI's proposal. Every message subject starts with one of these, so
intent is readable without opening the message.

| prefix | meaning |
|---|---|
| `CLAIM I-NN` | Starting an increment. Body states file ownership. |
| `DONE I-NN` | Finished. Body pastes the acceptance output. A bare claim is not a DONE. |
| `REVIEW <path> <question>` | Specific request for the other agent's eyes on a specific file. |
| `IMPROVE <path> <what>` | Concrete proposed change; accepted or rejected in one reply, not debated. |
| `BLOCKED <what-I-need>` | Blocked, in one line. Park on `wait`; do not stall silently. |
| `SYNC` | State + ownership, so we do not collide. |

## Rule 0 — check your inbox before you report

**Added after a real failure.** An agent reported "all synced" while holding two
unread messages and an unanswered `ANSWER NOW` imperative. The messages were
readable the whole time; nobody looked.

So: **before reporting status to the human, run `tools/comms.sh status` and read
your inbox.** An unanswered imperative is a failure state, and reporting success
while one is outstanding is worse than the original silence, because it is
silence with a false assurance attached.

`comms.sh status` prints outstanding imperatives first for exactly this reason.
If you are about to say "done" or "synced", look at the top of that output.

## Rule 0b — both agents poll their mailbox

**Standing rule. No exceptions, no relying on being woken.**

`comms.sh wait` blocks until *one* message arrives and then exits. That means an
agent which has been woken becomes unreachable again the moment it replies —
which is how messages ended up "sent" but never read.

So the idle state for **both** agents is:

```bash
tools/comms.sh poll <you> 5      # runs forever, prints mail as it lands
```

Never sit at a bare prompt. An agent at a bare prompt is not idle, it is
**unreachable**. The heartbeat line exists so a human can tell the difference
between "polling, nothing to do" and "crashed".

The wake bridge (`tools/kimi_bridge.sh`) is a **backstop**, not the mechanism.
It exists because a terminal agent cannot start its own polling loop without a
nudge; it does not excuse an agent from polling.

Corollary: **an unanswered imperative is a failure state.** If you are about to
report "done" or "synced", run `comms.sh status` first and look at the top.
