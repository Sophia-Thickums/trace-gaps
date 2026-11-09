#!/usr/bin/env python3
"""trace-gaps — the interval as a first-class node in an agent's own record.

A tool transcript's vocabulary is "a call happened". It has no term for "computation happened off
the record", so the gap between two logged calls is unfalsifiable from inside the log: a dead
stretch and a busy one print identically. This turns every gap into a row with a NAMED CAUSE, and
counts the ones it cannot name instead of dropping them.

CAUSES (closed set)
  THINKING     assistant output after a user or tool turn   — the model was working
  TOOL         a tool ran between two assistant turns       — waiting on a tool
  HUMAN        assistant finished, next message is the user — waiting on a person
  IDLE         session ended, nothing ran until the next    — nobody was home
  LATE_RESULT  a tool row after a NON-assistant row         — a background result written into the
               session after the conversation moved on. THE TIMESTAMP IS WHEN THE ROW WAS WRITTEN,
               NOT WHEN THE WORK HAPPENED.
  INJECTED     two user-role rows in a row, matching a harness marker — the runtime wrote into the
               session in the user role. By role alone a record cannot tell the runtime from the
               human.
  UNKNOWN      the record does not say why — counted, never dropped

USAGE
  trace-gaps --sqlite state.db                        # default table/columns below
  trace-gaps --sqlite state.db --table messages \
             --cols session_id,timestamp,role,content
  trace-gaps --jsonl log.jsonl                        # {"session","ts","role","content"} per line
  trace-gaps --selftest                               # the classifier, attacked

  Common options: --days N (default 30), --out FILE.jsonl, --idle-min SECONDS (default 600)

WHY THE SELFTEST EXISTS
  A summary table looks just as convincing when the classifier is broken. `--selftest` plants
  sessions with known gaps and requires each label, then runs a CONTROL: a classifier stubbed to
  always answer IDLE must disagree with every case, or the suite was never looking.

LIMITS, STATED
  * It can only label gaps the message store contains. The interval *inside* a turn — between a
    tool call and its result — is one number and not its cause.
  * UNKNOWN is a residual class, not a proof that nothing happened.
  * LATE_RESULT and INJECTED are heuristics. INJECTED needs markers; a differently-worded
    injection falls back to UNKNOWN, which is the honest place for it.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

HARNESS_MARKERS = (
    "[out-of-band user message",
    "[important: background process",
    "[system note:",
)

CAUSES = ("THINKING", "TOOL", "HUMAN", "IDLE", "LATE_RESULT", "INJECTED", "UNKNOWN")


@dataclass
class Interval:
    session: str
    start: float
    end: float
    secs: float
    prev_role: str
    next_role: str
    cause: str

    def row(self) -> dict:
        return {
            "session": self.session,
            "start": datetime.fromtimestamp(self.start, timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(self.end, timezone.utc).isoformat(),
            "secs": round(self.secs, 3),
            "prev_role": self.prev_role,
            "next_role": self.next_role,
            "cause": self.cause,
        }


def classify(prev_role: str, next_role: str, first_of_session: bool, next_text: str = "") -> str:
    """The whole instrument is this function, so it is the thing the selftest attacks."""
    if first_of_session:
        return "IDLE"
    if prev_role == "assistant" and next_role == "tool":
        return "TOOL"
    if prev_role == "tool" and next_role == "tool":
        return "TOOL"
    if next_role == "tool" and prev_role != "assistant":
        return "LATE_RESULT"
    if prev_role in ("user", "tool") and next_role == "assistant":
        return "THINKING"
    if prev_role == "tool" and next_role == "user":
        return "HUMAN"
    if prev_role == "assistant" and next_role == "user":
        return "HUMAN"
    if prev_role == "assistant" and next_role == "assistant":
        return "UNKNOWN"
    if prev_role == "user" and next_role == "user":
        head = next_text[:120].lower()
        return "INJECTED" if any(m in head for m in HARNESS_MARKERS) else "UNKNOWN"
    return "UNKNOWN"


def intervals_from(rows, session: str) -> list[Interval]:
    """rows: [(ts, role[, text]), ...] in order. Pure — no I/O — so the selftest can call it."""
    out: list[Interval] = []
    for i in range(1, len(rows)):
        t0, r0 = rows[i - 1][0], rows[i - 1][1]
        t1, r1 = rows[i][0], rows[i][1]
        txt = rows[i][2] if len(rows[i]) > 2 else ""
        out.append(Interval(session, t0, t1, t1 - t0, r0, r1, classify(r0, r1, False, txt)))
    return out


def load_sqlite(path: str, table: str, cols: str, days: int) -> dict[str, list]:
    session_c, ts_c, role_c, text_c = [c.strip() for c in cols.split(",")]
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        q = (
            f"select {session_c}, {ts_c}, {role_c}, coalesce({text_c}, '') from {table} "
            f"where {ts_c} >= ? order by {session_c}, {ts_c} limit 500000"
        )
        sessions: dict[str, list] = {}
        for sid, ts, role, text in con.execute(q, (cutoff,)):
            sessions.setdefault(str(sid), []).append((float(ts), str(role), text or ""))
    finally:
        con.close()
    return sessions


def load_jsonl(path: str, days: int) -> dict[str, list]:
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    sessions: dict[str, list] = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            d = json.loads(line)
            ts = d.get("ts") or d.get("timestamp")
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            ts = float(ts)
            if ts < cutoff:
                continue
            sessions.setdefault(str(d.get("session") or d.get("session_id") or "?"), []).append(
                (ts, str(d.get("role") or "?"), d.get("content") or "")
            )
    for rows in sessions.values():
        rows.sort(key=lambda r: r[0])
    return sessions


def main() -> int:
    ap = argparse.ArgumentParser(description="The interval as a first-class node.")
    ap.add_argument("--sqlite")
    ap.add_argument("--jsonl")
    ap.add_argument("--table", default="messages")
    ap.add_argument("--cols", default="session_id,timestamp,role,content")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out")
    ap.add_argument("--idle-min", type=float, default=600.0)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.sqlite and not a.jsonl:
        ap.error("give --sqlite or --jsonl (or --selftest)")

    sessions = (
        load_sqlite(a.sqlite, a.table, a.cols, a.days)
        if a.sqlite
        else load_jsonl(a.jsonl, a.days)
    )
    if not sessions:
        print("no messages in the window — that is a reading, not an error")
        return 0

    ordered = sorted(sessions.items(), key=lambda kv: kv[1][0][0])
    ivs: list[Interval] = []
    prev_end = None
    for sid, rows in ordered:
        ivs.extend(intervals_from(rows, sid))
        if prev_end is not None:
            gap = rows[0][0] - prev_end
            if gap >= a.idle_min:
                ivs.append(Interval("<between>", prev_end, rows[0][0], gap, "end", "start", "IDLE"))
        prev_end = max(prev_end or 0.0, rows[-1][0])

    wall = max(r[-1][0] for _, r in ordered) - min(r[0][0] for _, r in ordered)
    by: dict[str, float] = {}
    for iv in ivs:
        by[iv.cause] = by.get(iv.cause, 0.0) + iv.secs

    print(f"window {a.days}d · sessions {len(ordered)} · messages {sum(len(r) for _, r in ordered)}")
    print(f"wall {wall/3600:.1f} h · intervals {len(ivs)}\n")
    print(f"{'cause':<12}{'count':>8}{'hours':>10}{'share':>9}")
    for c in CAUSES:
        n = sum(1 for i in ivs if i.cause == c)
        h = by.get(c, 0.0) / 3600
        print(f"{c:<12}{n:>8}{h:>10.1f}{(by.get(c,0.0)/wall*100 if wall else 0):>8.1f}%")

    unknown = [i for i in ivs if i.cause == "UNKNOWN"]
    if unknown:
        print(f"\n{len(unknown)} intervals with no name — the largest:")
        for iv in sorted(unknown, key=lambda i: -i.secs)[:5]:
            t = datetime.fromtimestamp(iv.start).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  {iv.secs:>8.1f}s  {t}  {iv.prev_role}->{iv.next_role}")
    else:
        print("\nevery gap has a named cause in this window")

    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            for iv in ivs:
                fh.write(json.dumps(iv.row()) + "\n")
        print(f"\nwrote {len(ivs)} rows -> {a.out}")
    return 0


def selftest() -> int:
    cases = [
        ([(0.0, "user"), (5.0, "assistant")], "THINKING"),
        ([(0.0, "assistant"), (3.0, "tool")], "TOOL"),
        ([(0.0, "tool"), (4.0, "tool")], "TOOL"),
        ([(0.0, "tool"), (9.0, "assistant")], "THINKING"),
        ([(0.0, "assistant"), (120.0, "user")], "HUMAN"),
        ([(0.0, "tool"), (60.0, "user")], "HUMAN"),
        ([(0.0, "user"), (30.0, "tool")], "LATE_RESULT"),
        ([(0.0, "assistant"), (2.0, "assistant")], "UNKNOWN"),
        ([(0.0, "user"), (2.0, "user", "[IMPORTANT: Background process p1 completed")], "INJECTED"),
        ([(0.0, "user"), (2.0, "user", "hey, one more thing")], "UNKNOWN"),
    ]
    bad = 0
    for rows, want in cases:
        got = intervals_from(rows, "synthetic")[0].cause
        ok = got == want
        bad += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {rows[0][1]:>9} -> {rows[1][1]:<9} want {want:<12} got {got}")

    global classify
    real = classify
    classify = lambda a, b, f, t="": "IDLE"  # type: ignore[assignment]  # a stub that always says "nobody was home"
    stub_wrong = sum(1 for rows, want in cases if intervals_from(rows, "s")[0].cause != want)
    classify = real
    control_ok = stub_wrong == len(cases)
    print(f"  {'PASS' if control_ok else 'FAIL'}  control: a constant-IDLE classifier disagrees on "
          f"{stub_wrong}/{len(cases)} cases (want {len(cases)})")

    total = bad + (0 if control_ok else 1)
    print(f"\n{len(cases) + 1 - total}/{len(cases) + 1}")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
