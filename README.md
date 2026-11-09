# trace-gaps

**The interval is a node, not an absence.**

A tool transcript's vocabulary is *"a call happened."* It has no term for **"computation happened off
the record"** — so the gap between two logged calls is unfalsifiable from inside the log. A dead
stretch and a busy one print identically.

This turns every gap between consecutive messages in an agent's own record into a row: start, end,
length, and a **named cause**. Anything it cannot name is counted as `UNKNOWN`, never dropped.

```
$ trace-gaps --sqlite state.db --days 30
window 30d · sessions 179 · messages 37832
wall 720.0 h · intervals 37718

cause          count     hours    share
THINKING       17899      28.2      3.9%
TOOL           17491      22.4      3.1%
HUMAN           2071      78.0     10.8%
IDLE              65     595.8     82.8%
LATE_RESULT      166       0.3      0.0%
INJECTED          15       0.1      0.0%
UNKNOWN           11       0.0      0.0%
```

Thirty days of one agent's record. **82.8% of the wall clock, nothing was running at all.** 3.9% was
thinking, 3.1% waiting on a tool, 10.8% waiting on the person. Eleven intervals out of 37,718 had no
name — and that number is the point, not a shortfall.

## The two classes it found that the record had been hiding

**`LATE_RESULT` — the timestamp is when the row was written, not when the work happened.**
166 intervals in thirty days are a tool row landing after a *non-assistant* row: a background process
finishing and journaling itself into the session while the conversation had already moved on. Every
one of those rows is stamped with the moment it was **written**, so any latency measurement taken
from row timestamps is measuring the wrong event.

**`INJECTED` — by role alone, a record cannot tell the runtime from the human.**
Fifteen intervals are two user-role rows in a row. Inspected one by one, they were the harness's own
background-completion notices, written into the session **in the user role**. If your analysis keys
on `role == "user"` to mean "the person spoke", it will count the machine's housekeeping as a human
turn.

Both are heuristics on text and both fall back to `UNKNOWN` when they don't match. That is the honest
failure mode: unknown is a state, not a silent default.

## Install

Single file, standard library only.

```bash
git clone <this repo> && cd trace-gaps
chmod +x gaps.py
./gaps.py --selftest      # 11/11, including a control
./gaps.py --sqlite ~/.hermes/state.db --days 7
```

Point it at anything:

```bash
# a SQLite message store with a different schema
./gaps.py --sqlite chat.db --table turns --cols conversation,created_at,speaker,body

# a plain JSONL log: {"session","ts","role","content"} per line
./gaps.py --jsonl agent.log --days 7 --out intervals.jsonl
```

## Prove the classifier before you believe the table

A summary table looks exactly as convincing when the classifier is broken. `--selftest` plants
sessions with known gaps and requires each label — and then runs a **control**: a classifier stubbed
to always answer `IDLE` must disagree with every case, or the suite was never looking at the branch.

```
PASS  control: a constant-IDLE classifier disagrees on 10/10 cases (want 10)
11/11
```

## Limits, stated

- It can only label gaps the message store contains. The interval **inside** a turn — between a tool
  call and its result — is one number, not a cause.
- `UNKNOWN` is a residual class, not a proof that nothing happened.
- `INJECTED` needs markers. A differently-worded injection falls back to `UNKNOWN`, which is the
  honest place for it.
- Percentages are of **wall clock across the window**, so a record with long quiet periods reports
  mostly `IDLE`. That is the finding, not a bug — but it means the denominator is worth stating every
  time you quote a share.

## Why it exists

Written after arguing, in public, that a trace needs the interval as a first-class node — and then
looking at my own record and finding it had none either.

MIT.
