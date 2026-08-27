"""The ONE fleet roster — loader + drift check for fleet/roster.json.

    python -m fleet.roster        # check the roster against the dispatcher

roster.json is the single hand-maintained list of agents; the /week Fleet
cards and Org Chart both build from it (GET /api/roster). The dispatcher
schedule in fleet/dispatch.py stays the sole authority on what actually runs —
this check exists so the display file can never silently disagree with it:

  1. every cron job has a roster entry, marked deployed, with the same cadence;
  2. every roster agent claiming "deployed" is actually on the cron
     (the dispatcher itself excepted — it IS the cron);
  3. every org-lane member that references an agent references a real one.

Purely local (no network, no env): safe to run anywhere, cheap enough to run
whenever the roster or the schedule changes.
"""

import json
from pathlib import Path

ROSTER_PATH = Path(__file__).resolve().parent / "roster.json"

# dispatch.py due-function name -> the cadence word roster.json must use.
CADENCE_BY_DUE = {
    "_due_daily": "daily",
    "_due_weekday": "weekdays",
    "_due_saturday": "saturday",
    "_due_research": "sunday",
}


def load_roster():
    return json.loads(ROSTER_PATH.read_text(encoding="utf-8"))


def drift_check():
    """Return a list of human-readable problems (empty = roster in step)."""
    from fleet import dispatch

    roster = load_roster()
    agents = {a["id"]: a for a in roster["agents"]}
    problems = []

    for job in dispatch.JOBS:
        a = agents.get(job["name"])
        want = CADENCE_BY_DUE.get(job["due"].__name__, "?")
        if a is None:
            problems.append(f"job '{job['name']}' runs on the cron but has no roster entry")
        elif a.get("mode") != "deployed":
            problems.append(f"job '{job['name']}' runs on the cron but the roster "
                            f"says mode '{a.get('mode')}'")
        elif a.get("cadence") != want:
            problems.append(f"job '{job['name']}': the cron runs it {want}, "
                            f"the roster says {a.get('cadence')}")

    job_names = {j["name"] for j in dispatch.JOBS}
    for a in roster["agents"]:
        if a.get("mode") == "deployed" and a["id"] != "dispatch" and a["id"] not in job_names:
            problems.append(f"roster says '{a['id']}' is deployed but no cron job runs it")

    for lane in roster["lanes"]:
        for m in lane["members"]:
            if "agent" in m and m["agent"] not in agents:
                problems.append(f"lane '{lane['id']}' references unknown agent '{m['agent']}'")

    return problems


def main():
    problems = drift_check()
    if problems:
        print("ROSTER DRIFT — fleet/roster.json disagrees with fleet/dispatch.py:")
        for p in problems:
            print(f"  - {p}")
        return 1
    roster = load_roster()
    print(f"roster OK — {len(roster['agents'])} agents, "
          f"{len(roster['lanes'])} lanes, in step with the dispatcher schedule.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
