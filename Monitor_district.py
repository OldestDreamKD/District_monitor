import requests
import os
import re
import json
from datetime import datetime

# ================== CONFIGURATION ==================

MOVIE_ID   = "MV194537"
MOVIE_SLUG = "spider-man-brand-new-day-movie-tickets-in-chennai"
MOVIE_NAME = "Spider-Man: Brand New Day"

LANGUAGE_KEYS = {
    "rrfdpndypd": "English",
    "hMPG2XHyKL": "Tamil",
}

TARGET_DATES = ["2026-07-30", "2026-07-31"]

TARGET_CINEMAS = [
    "PVR",
    "Express Avenue",
    "Palazzo",
    "Nexus Vijaya",
    "INOX",
    "MovieMax",
]

ALERTED_FILE = "district_alerted.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.district.in/",
    "Cache-Control": "no-cache",
}

# ================== HELPERS ==================

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def send_telegram_1(message):
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id_1 = os.getenv("TELEGRAM_CHAT_ID_1")

    if not token or not chat_id_1:
        print("Telegram not configured.")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id_1": chat_id_1, "text": message},
            timeout=15,
        )
        print(f"Telegram: {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def send_telegram_2(message):
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id_2 = os.getenv("TELEGRAM_CHAT_ID_2")

    if not token or not chat_id_2:
        print("Telegram not configured.")
        return False
   
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id_2": chat_id_2, "text": message},
            timeout=15,
        )
        print(f"Telegram: {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

# ================== DISTRICT SCRAPER ==================

def extract_session_data(html, lang_key, date):
    candidate_keys = [
        f"{lang_key}{date}",
        f"{lang_key.lower()}{date}",
        f"{lang_key.upper()}{date}",
        lang_key,
        lang_key.lower(),
        lang_key.upper(),
    ]

    for session_key in candidate_keys:
        idx = html.find(f'"{session_key}"')
        if idx == -1:
            continue

        val_start = html.find('{', idx)
        if val_start == -1:
            continue

        depth = 0
        i = val_start
        while i < len(html):
            c = html[i]
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(html[val_start:i+1])
                    except Exception:
                        return None
            i += 1

    return None


def get_cinemas_for(lang_key, lang_name, date):
    url = (
        f"https://www.district.in/movies/{MOVIE_SLUG}-{MOVIE_ID}"
        f"?frmtid={lang_key}&fromdate={date}"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"    HTTP {r.status_code} for {lang_name} / {date}")
            return []
    except Exception as e:
        print(f"    Request error ({lang_name}/{date}): {e}")
        return []

    data = extract_session_data(r.text, lang_key, date)
    if not data:
        print(f"    No session data for {lang_name}/{date}")
        return []

    page_data = data.get("pageData", {})
    cinemas = []

    for section in ("nearbyCinemas", "farCinemas"):
        for cinema in page_data.get(section, []):
            info       = cinema.get("cinemaInfo") or cinema.get("data") or cinema
            name       = info.get("name", "") or cinema.get("label", "")
            address    = info.get("address", "")
            cid        = cinema.get("id") or info.get("id")
            entity_url = cinema.get("entityUrl", "")
            sessions   = cinema.get("sessions", [])
            if not sessions:
                continue
            cinemas.append({
                "id":       cid,
                "name":     name,
                "address":  address,
                "sessions": sessions,
                "url":      f"https://www.district.in{entity_url}" if entity_url else "",
            })

    return cinemas


def is_target_cinema(name):
    return any(t.lower() in name.lower() for t in TARGET_CINEMAS)


def discover_language_keys(html):
    idx = html.find('"filterBuckets"')
    if idx == -1:
        return {}
    val_start = html.find('[', idx)
    if val_start == -1:
        return {}

    depth = 0
    i = val_start
    while i < len(html):
        c = html[i]
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                try:
                    buckets = json.loads(html[val_start:i+1])
                    result = {}
                    for bucket in buckets:
                        if bucket.get("bucketKey") == "language":
                            for b in bucket.get("buckets", []):
                                key   = b.get("key", "")
                                title = b.get("title", "")
                                if key and title:
                                    result[key] = title
                    return result
                except Exception:
                    return {}
        i += 1
    return {}


# ================== MAIN ==================

def check_all():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n=== District Monitor Run at {now} ===")

    alerted = set(load_json(ALERTED_FILE, []))

    # Discover language keys
    try:
        probe_url = (
            f"https://www.district.in/movies/{MOVIE_SLUG}-{MOVIE_ID}"
            f"?frmtid=rrfdpndypd&fromdate={TARGET_DATES[0]}"
        )
        probe_r   = requests.get(probe_url, headers=HEADERS, timeout=20)
        discovered = discover_language_keys(probe_r.text)
        lang_keys  = {**LANGUAGE_KEYS, **discovered} if discovered else LANGUAGE_KEYS
        print(f"Language keys: {lang_keys}")
    except Exception as e:
        lang_keys = LANGUAGE_KEYS
        print(f"Language discovery error: {e}, using defaults")

    new_hits = []

    for lang_key, lang_name in lang_keys.items():
        for date in TARGET_DATES:
            print(f"\n  Checking {lang_name} / {date} ...")
            cinemas = get_cinemas_for(lang_key, lang_name, date)
            print(f"    Cinemas with sessions: {len(cinemas)}")

            for cinema in cinemas:
                cname = cinema["name"]
                print(f"    -> {cname}")

                if is_target_cinema(cname):
                    key = f"{lang_key}|{cinema['id']}|{date}"
                    if key in alerted:
                        print(f"       Already alerted")
                        continue

                    show_times = [
                        s.get("showTime", "")[11:16]
                        for s in cinema["sessions"]
                    ]
                    new_hits.append({
                        "language":  lang_name,
                        "cinema":    cname,
                        "cinema_id": cinema["id"],
                        "date":      date,
                        "show_times": show_times,
                        "url":       cinema["url"],
                        "key":       key,
                    })
                    print(f"       TARGET HIT! Shows: {show_times}")

    if not new_hits:
        print("\nNo new hits.")
        return

    lines = [f"SPIDER-MAN BOOKING OPEN!\n"]
    for h in new_hits:
        d           = h["date"]
        pretty_date = f"{d[8:10]}/{d[5:7]}/{d[0:4]}"
        times_str   = ", ".join(h["show_times"])
        lines.append(f"Language: {h['language']}")
        lines.append(f"Cinema:   {h['cinema']}")
        lines.append(f"Date:     {pretty_date}")
        lines.append(f"Shows:    {times_str}")
        if h["url"]:
            lines.append(f"Book:     {h['url']}")
        lines.append("")

    if send_telegram_1("\n".join(lines)):
        for h in new_hits:
            alerted.add(h["key"])
        save_json(ALERTED_FILE, sorted(alerted))
        print(f"Saved {len(new_hits)} alerts.")

    
    if send_telegram_2("\n".join(lines)):
        for h in new_hits:
            alerted.add(h["key"])
        save_json(ALERTED_FILE, sorted(alerted))
        print(f"Saved {len(new_hits)} alerts.")


if __name__ == "__main__":
    check_all()
