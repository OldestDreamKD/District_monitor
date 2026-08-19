import requests
import os
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

TARGET_DATES = ["2026-07-30", "2026-07-31", "2026-08-07"]

TARGET_CINEMAS = [
    "Express Avenue",
    "Palazzo",
    "Nexus Vijaya",
    "Vijaya Mall",
    "Forum Vijaya",
    "PVR"
]

ALERTED_FILE = "district_alerted.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.district.in/",
    "Cache-Control": "no-cache",
}

TG_MAX_LEN = 3800


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


def send_telegram(message):
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_GROUP_ID")

    if not token or not chat_id:
        print(f"[TG] Not configured. token_set={bool(token)}, group_id_set={bool(chat_id)}")
        return False

    chat_id = chat_id.strip().strip('"').strip("'")

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": message},
            timeout=15,
        )
        print(f"[TG] Status: {r.status_code} | Response: {r.text[:200]}")
        return r.status_code == 200
    except Exception as e:
        print(f"[TG] Error: {e}")
        return False


def send_telegram_chunked(full_message):
    if len(full_message) <= TG_MAX_LEN:
        return send_telegram(full_message)

    blocks   = full_message.split("\n\n")
    chunks   = []
    current  = ""

    for block in blocks:
        if len(current) + len(block) + 2 > TG_MAX_LEN:
            if current:
                chunks.append(current.rstrip())
            current = block + "\n\n"
        else:
            current += block + "\n\n"

    if current.strip():
        chunks.append(current.rstrip())

    print(f"[TG] Splitting message into {len(chunks)} chunks")
    all_ok = True
    for i, chunk in enumerate(chunks, 1):
        header = f"(Part {i}/{len(chunks)})\n" if len(chunks) > 1 else ""
        if not send_telegram(header + chunk):
            all_ok = False

    return all_ok


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
    cinemas   = []

    for section in ("nearbyCinemas", "farCinemas"):
        for cinema in page_data.get(section, []):
            info       = cinema.get("cinemaInfo") or cinema.get("data") or cinema
            name       = info.get("name", "") or cinema.get("label", "")
            address    = info.get("address", "")
            cid        = cinema.get("id") or info.get("id")
            entity_url = cinema.get("entityUrl", "")
            all_sessions = cinema.get("sessions", [])

            if not all_sessions:
                continue

            # Filter to only bookable sessions
            active_sessions = []
            skipped_reasons = []
            for s in all_sessions:
                is_disabled = s.get("disableClick", False)
                avail_seats = s.get("avail", 0)
                status      = s.get("statusColor", "")

                if not is_disabled and avail_seats > 0:
                    active_sessions.append(s)
                else:
                    skipped_reasons.append(
                        f"{s.get('showTime','?')[11:16]}(disabled={is_disabled},"
                        f"avail={avail_seats},status={status})"
                    )

            if not active_sessions:
                if is_target_cinema(name):
                    print(f"    → {name} — ALL SESSIONS INACTIVE, skipping")
                    print(f"      Details: {skipped_reasons}")
                continue

            cinemas.append({
                "id":       cid,
                "name":     name,
                "address":  address,
                "sessions": active_sessions,
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
                    result  = {}
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

    try:
        probe_url = (
            f"https://www.district.in/movies/{MOVIE_SLUG}-{MOVIE_ID}"
            f"?frmtid=rrfdpndypd&fromdate={TARGET_DATES[0]}"
        )
        probe_r    = requests.get(probe_url, headers=HEADERS, timeout=20)
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
            print(f"    Bookable cinemas: {len(cinemas)}")

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
                        "language":     lang_name,
                        "cinema":       cname,
                        "cinema_id":    cinema["id"],
                        "date":         date,
                        "show_times":   show_times,
                        "sessions_raw": cinema["sessions"],
                        "url":          cinema["url"],
                        "key":          key,
                    })
                    print(f"       TARGET HIT! Bookable shows: {show_times}")

    if not new_hits:
        print("\nNo new hits.")
        return

    lines = [f"🕷️ {MOVIE_NAME} — BOOKING OPEN!\n"]
    for h in new_hits:
        d           = h["date"]
        pretty_date = f"{d[8:10]}/{d[5:7]}/{d[0:4]}"

        show_lines = []
        for s in h["sessions_raw"]:
            time_str = s.get("showTime", "")[11:16]
            avail    = s.get("avail", 0)
            total    = s.get("total", 0)
            fmt      = s.get("scrnFmt", "")
            audi     = s.get("audi", "")
            fmt_str  = f" ({fmt})" if fmt else ""
            audi_str = f" — {audi}" if audi else ""
            show_lines.append(f"  {time_str}{fmt_str} — {avail}/{total} seats{audi_str}")

        lines.append(f"Language: {h['language']}")
        lines.append(f"Cinema:   {h['cinema']}")
        lines.append(f"Date:     {pretty_date}")
        lines.append("Shows:")
        lines.extend(show_lines)
        if h["url"]:
            lines.append(f"Book:     {h['url']}")
        lines.append("")

    message = "\n".join(lines)

    if send_telegram_chunked(message):
        for h in new_hits:
            alerted.add(h["key"])
        save_json(ALERTED_FILE, sorted(alerted))
        print(f"Saved {len(new_hits)} alerts.")
    else:
        print("Telegram failed. NOT saving — will retry next run.")


if __name__ == "__main__":
    check_all()
