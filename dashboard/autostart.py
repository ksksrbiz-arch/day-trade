"""
Boot resume: start every bot the user had enabled, creating a default 'main'
bot the first time. Run by start_all.ps1 after the dashboard comes up.
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
