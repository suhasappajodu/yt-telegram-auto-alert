import os
import json
import requests
import feedparser
from pathlib import Path

# File paths
CHANNELS_FILE = "channels.json"
YT_STATE_FILE = "yt_state.json"
TG_STATE_FILE = "tg_state.json"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def load_json(file, default):
    p = Path(file)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except:
        return default

def save_json(file, data):
    Path(file).write_text(json.dumps(data, indent=2))

def send_msg(chat_id, text):
    url = f"{TG_API}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})

def fetch_updates(offset):
    r = requests.get(f"{TG_API}/getUpdates?offset={offset}")
    return r.json().get("result", [])

def handle_commands(channels, tg_state):
    last_id = tg_state.get("last_update_id", 0)
    updates = fetch_updates(last_id + 1)

    for u in updates:
        last_id = u["update_id"]
        msg = u.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        text = msg.get("text", "")

        if not text:
            continue

        parts = text.split()
        cmd = parts[0].lower()

        if cmd == "/start":
            tg_state["subscribers"].append(chat_id)
            tg_state["subscribers"] = list(set(tg_state["subscribers"]))
            send_msg(chat_id, "You are subscribed! Use /add <name> <channel_id> to add a YouTube channel.")
        
        elif cmd == "/add" and len(parts) == 3:
            name = parts[1]
            cid = parts[2]
            rss = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
            channels[name] = rss
            send_msg(chat_id, f"Added channel: {name}")
        
        elif cmd == "/remove" and len(parts) == 2:
            name = parts[1]
            channels.pop(name, None)
            send_msg(chat_id, f"Removed channel: {name}")
        
        elif cmd == "/list":
            out = "\n".join([f"{k}: {v}" for k,v in channels.items()]) or "No channels added."
            send_msg(chat_id, out)

    tg_state["last_update_id"] = last_id
    return channels, tg_state

def check_youtube(channels, yt_state, tg_state):
    for name, rss in channels.items():
        try:
            feed = feedparser.parse(rss)
        except:
            continue

        if not feed.entries:
            continue

        latest = feed.entries[0]
        vid = latest.get("yt_videoid")
        title = latest.get("title")
        link = latest.get("link")

        if yt_state.get(rss) != vid:
            yt_state[rss] = vid

            for chat_id in tg_state.get("subscribers", []):
                send_msg(chat_id, f"🔔 New video from {name}\n{title}\n{link}")

    return yt_state

def main():
    channels = load_json(CHANNELS_FILE, {})
    yt_state = load_json(YT_STATE_FILE, {})
    tg_state = load_json(TG_STATE_FILE, {"last_update_id": 0, "subscribers": []})

    channels, tg_state = handle_commands(channels, tg_state)
    yt_state = check_youtube(channels, yt_state, tg_state)

    save_json(CHANNELS_FILE, channels)
    save_json(YT_STATE_FILE, yt_state)
    save_json(TG_STATE_FILE, tg_state)

if __name__ == "__main__":
    main()
