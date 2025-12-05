# main.py
# Requirements: pip install requests feedparser
import os
import json
import requests
import feedparser
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# File paths
CHANNELS_FILE = "channels.json"
YT_STATE_FILE = "yt_state.json"
TG_STATE_FILE = "tg_state.json"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise SystemExit("Missing TELEGRAM_TOKEN env var")
TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/116.0 Safari/537.36"
}

# ---------- helpers ----------
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
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=20)
    except Exception as e:
        print("send_msg error", e)

def fetch_updates(offset):
    params = {}
    if offset:
        params["offset"] = offset
    resp = requests.get(f"{TG_API}/getUpdates", params=params, timeout=60)
    resp.raise_for_status()
    return resp.json().get("result", [])

# ---------- channel id extraction ----------
def extract_channel_id_from_url(url):
    """
    Attempt to extract a YouTube numeric channel id (starts with UC...) from a URL.
    Strategies:
      1) If URL contains '/channel/UC...' extract directly.
      2) Fetch the page and search for "channelId":"UC..." in the HTML/JSON.
      3) Search for a canonical link to /channel/UC...
    Returns channel_id string or None.
    """
    if not url:
        return None
    url = url.strip()
    # Quick parse: if it already is a channel ID
    if re.match(r"^UC[0-9A-Za-z_-]{20,}$", url):
        return url

    # If URL contains /channel/<id>
    m = re.search(r"/channel/(UC[0-9A-Za-z_-]{20,})", url)
    if m:
        return m.group(1)

    # Normalize URL
    try:
        parsed = urlparse(url)
        # If user provided a path like /@handle or /c/name or /user/name
        # we'll fetch page and try to find channelId within page HTML/JSON.
        fetch_url = url if parsed.scheme else ("https://" + url)
    except Exception:
        fetch_url = url

    try:
        r = requests.get(fetch_url, headers=HEADERS, timeout=15, allow_redirects=True)
        html = r.text
        # 1) Search for "channelId":"UC..."
        m = re.search(r'"channelId"\s*:\s*"(?P<id>UC[0-9A-Za-z_-]{20,})"', html)
        if m:
            return m.group("id")
        # 2) Search for /channel/UC... in canonical or og:url
        m2 = re.search(r'href="https?://www\.youtube\.com/channel/(UC[0-9A-Za-z_-]{20,})"', html)
        if m2:
            return m2.group(1)
        m3 = re.search(r'property="og:url"\s+content="https?://www\.youtube\.com/channel/(UC[0-9A-Za-z_-]{20,})"', html)
        if m3:
            return m3.group(1)
        # 3) Fallback: sometimes JavaScript has "externalId":"UC..."
        m4 = re.search(r'"externalId"\s*:\s*"(?P<id>UC[0-9A-Za-z_-]{20,})"', html)
        if m4:
            return m4.group("id")
    except Exception as e:
        print("extract fetch error", e)
    return None

# ---------- feed handling ----------
def check_youtube_and_notify(channels, yt_state, subscribers):
    changed = False
    for name, rss in channels.items():
        try:
            feed = feedparser.parse(rss)
        except Exception as e:
            print("feed parse error", rss, e)
            continue
        if not feed.entries:
            continue
        latest = feed.entries[0]
        vid = latest.get("yt_videoid") or latest.get("id") or latest.get("link")
        title = latest.get("title", "No title")
        link = latest.get("link", "")
        last_seen = yt_state.get(rss)
        if last_seen != vid:
            yt_state[rss] = vid
            changed = True
            text = f"🔔 New video from {name}\n{title}\n{link}"
            for chat_id in subscribers:
                send_msg(chat_id, text)
    return changed

# ---------- command handling ----------
def handle_updates_and_commands(channels, tg_state):
    last_id = tg_state.get("last_update_id", 0)
    updates = fetch_updates(last_id + 1)
    changed = False

    for u in updates:
        last_id = max(last_id, u["update_id"])
        msg = u.get("message") or u.get("edited_message")
        if not msg:
            continue
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        text = msg.get("text", "").strip()
        if not text:
            continue
        parts = text.split(maxsplit=2)
        cmd = parts[0].lower()
        arg1 = parts[1] if len(parts) >= 2 else ""
        arg2 = parts[2] if len(parts) >= 3 else ""

        # Commands
        if cmd in ("/start", "start"):
            subs = set(tg_state.get("subscribers", []))
            subs.add(chat_id)
            tg_state["subscribers"] = list(subs)
            send_msg(chat_id, "You are subscribed! Use /add <name> <channel_id> or /addurl <name> <youtube_url> to add a channel.")
            changed = True

        elif cmd in ("/help","help"):
            help_text = (
                "Commands:\n"
                "/start - Subscribe\n"
                "/add <name> <channel_id> - Add directly by channel id (UC...)\n"
                "/addrss <name> <rss_url> - Add by RSS URL\n"
                "/addurl <name> <youtube_url> - Add by any YouTube link or handle\n"
                "/remove <name> - Remove channel\n"
                "/list - Show tracked channels\n"
            )
            send_msg(chat_id, help_text)

        elif cmd == "/add" and arg1 and arg2:
            name = arg1.strip()
            cid = arg2.strip()
            # If user mistakenly provided a URL in arg2, try to extract
            if not cid.startswith("UC"):
                maybe = extract_channel_id_from_url(cid)
                if maybe:
                    cid = maybe
            rss = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
            channels[name] = rss
            save_json(CHANNELS_FILE, channels)
            send_msg(chat_id, f"Added channel: {name}\n{rss}")
            changed = True

        elif cmd == "/addrss" and arg1 and arg2:
            name = arg1.strip()
            rss_url = arg2.strip()
            channels[name] = rss_url
            save_json(CHANNELS_FILE, channels)
            send_msg(chat_id, f"Added RSS: {name} -> {rss_url}")
            changed = True

        elif cmd == "/addurl" and arg1 and arg2:
            name = arg1.strip()
            url = arg2.strip()
            cid = extract_channel_id_from_url(url)
            if not cid:
                send_msg(chat_id, "Failed to extract channel id from the URL. Try /add <name> <channel_id> or paste the standard channel link.")
            else:
                rss = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
                channels[name] = rss
                save_json(CHANNELS_FILE, channels)
                send_msg(chat_id, f"Added channel: {name}\n{rss}")
                changed = True

        elif cmd in ("/remove", "/rm") and arg1:
            name = arg1.strip()
            if name in channels:
                del channels[name]
                save_json(CHANNELS_FILE, channels)
                send_msg(chat_id, f"Removed channel: {name}")
                changed = True
            else:
                send_msg(chat_id, f"Channel '{name}' not found.")

        elif cmd == "/list":
            if not channels:
                send_msg(chat_id, "No channels tracked yet.")
            else:
                out = "\n".join([f"{k}: {v}" for k,v in channels.items()])
                send_msg(chat_id, "Tracked channels:\n" + out)

        else:
            # Not recognized - ignore or guide
            # If user sent "<name> <url>" without /addurl, try to parse
            if len(parts) >= 2 and parts[0].startswith("http"):
                send_msg(chat_id, "Use /addurl <name> <youtube_url> to add by link.")
    # end for

    if last_id != tg_state.get("last_update_id"):
        tg_state["last_update_id"] = last_id
        changed = True

    return channels, tg_state, changed

# ---------- main ----------
def main():
    channels = load_json(CHANNELS_FILE, {})
    yt_state = load_json(YT_STATE_FILE, {})
    tg_state = load_json(TG_STATE_FILE, {"last_update_id": 0, "subscribers": []})

    try:
        channels, tg_state, upd_changed = handle_updates_and_commands(channels, tg_state)
    except Exception as e:
        print("handle updates error", e)
        upd_changed = False

    try:
        subs = tg_state.get("subscribers", [])
        feed_changed = check_youtube_and_notify(channels, yt_state, subs)
    except Exception as e:
        print("feed check error", e)
        feed_changed = False

    if upd_changed:
        save_json(CHANNELS_FILE, channels)
        save_json(TG_STATE_FILE, tg_state)
    if feed_changed:
        save_json(YT_STATE_FILE, yt_state)

if __name__ == "__main__":
    main()
