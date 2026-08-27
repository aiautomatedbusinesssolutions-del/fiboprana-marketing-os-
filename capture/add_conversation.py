"""Interactive CLI to add a single conversation entry.

Walks through every field one prompt at a time. Blank input is allowed
for every field except `date` (which defaults to today if blank).
Multi-line fields are terminated with a blank line.

Run:
    python add_conversation.py
"""

from datetime import date, datetime

from db import ensure_schema, get_connection


# ---------- small prompt helpers ----------

def prompt_single(label, hint=None):
    """Single-line prompt. Returns stripped input (may be '')."""
    suffix = f" [{hint}]" if hint else ""
    return input(f"{label}{suffix}: ").strip()


def prompt_multiline(label, hint=None):
    """Multi-line prompt. Blank line ends. Returns lines joined with \\n."""
    suffix = f" [{hint}]" if hint else ""
    print(f"{label}{suffix} (blank line to finish):")
    lines = []
    while True:
        line = input("  ")
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines)


def prompt_date():
    """Prompt for YYYY-MM-DD; default to today; re-ask on bad format."""
    today = date.today().isoformat()
    while True:
        raw = input(f"date [YYYY-MM-DD, default {today}]: ").strip()
        if not raw:
            return today
        try:
            datetime.strptime(raw, "%Y-%m-%d")
            return raw
        except ValueError:
            print("  invalid date, expected YYYY-MM-DD")


def prompt_tags():
    """Comma-separated tags; lowercased, trimmed, empties dropped."""
    raw = input("tags [comma-separated, e.g. reddit,beginner,broker-question]: ").strip()
    if not raw:
        return ""
    parts = [t.strip().lower() for t in raw.split(",") if t.strip()]
    return ",".join(parts)


# ---------- entry collection ----------

def collect_entry():
    """Walk the user through each field and return a dict ready for INSERT."""
    return {
        "date":                 prompt_date(),
        "source":               prompt_single("source",           "reddit / discord / twitter / in-person / other"),
        "source_detail":        prompt_single("source_detail",    "e.g. r/Meditation, a Discord server"),
        "handle":               prompt_single("handle",           "their username"),
        "contact_info":         prompt_single("contact_info",     "email / DM link / etc."),
        "segment":              prompt_single("segment",          "beginner / intermediate / has-broker, or free text"),
        "experience_level":     prompt_single("experience_level", "never-practiced / on-and-off / daily-practice, or free text"),
        "stated_goal":          prompt_multiline("stated_goal",   "what they said they want to achieve"),
        "pain_points":          prompt_multiline("pain_points",   "frustrations, obstacles, unmet needs"),
        "quotes":               prompt_multiline("quotes",        "verbatim quotes worth preserving"),
        "feature_implications": prompt_multiline("feature_implications", "what this suggests for the product"),
        "downloaded_app":       prompt_single("downloaded_app",   "yes / no / unknown, optionally with notes"),
        "feedback_on_app":      prompt_multiline("feedback_on_app"),
        "follow_up_status":     prompt_single("follow_up_status", "none / awaiting-reply / converted / lost, or free text"),
        "notes":                prompt_multiline("notes",         "anything else worth remembering"),
        "tags":                 prompt_tags(),
    }


# ---------- summary + save ----------

def print_summary(data):
    print("\n--- entry summary ---")
    for key, value in data.items():
        display = value if value else "(blank)"
        if "\n" in str(display):
            print(f"{key}:")
            for line in str(display).splitlines():
                print(f"    {line}")
        else:
            print(f"{key}: {display}")
    print("---------------------")


def confirm_save():
    ans = input("Save? [Y/n]: ").strip().lower()
    return ans in ("", "y", "yes")


def insert(conn, data):
    cols = list(data.keys())
    placeholders = ", ".join(f":{c}" for c in cols)
    sql = f"INSERT INTO conversations ({', '.join(cols)}) VALUES ({placeholders})"
    cur = conn.execute(sql, data)
    conn.commit()
    return cur.lastrowid


def main():
    conn = get_connection()
    ensure_schema(conn)
    try:
        data = collect_entry()
    except (KeyboardInterrupt, EOFError):
        print("\nAborted. Nothing saved.")
        return
    print_summary(data)
    if not confirm_save():
        print("Aborted. Nothing saved.")
        return
    new_id = insert(conn, data)
    print(f"Saved as id={new_id}")


if __name__ == "__main__":
    main()
