"""
Boot resume (cross-platform): start every bot the user had enabled, creating a
default 'main' bot the first time. Invoked by docker-entrypoint.sh on the cloud
and by start_all.ps1 on Windows. Idempotent -- start_bot no-ops when the bot's
PID is already alive, so re-running never double-launches a trade loop.
"""
from __future__ import annotations

from dashboard import bots as b


def main():
    reg = b._load()
    if not reg:
        bot = b.create_bot("main", {})
        b.start_bot(bot["id"])
        print(f"created+started default bot {bot['id']}")
        return
    started = []
    for bid, bot in reg.items():
        if bot.get("enabled"):
            b.start_bot(bid)
            started.append(bot.get("name", bid))
    print("resumed:", ", ".join(started) if started else "(none enabled)")


if __name__ == "__main__":
    main()
