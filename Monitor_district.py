import requests
import os
import json
import random
import time
from datetime import datetime

# ================== CONFIGURATION ==================

MOVIE_ID   = "MV194537"
MOVIE_SLUG = "spider-man-brand-new-day-movie-tickets-in-chennai"
MOVIE_NAME = "Spider-Man: Brand New Day"

LANGUAGE_KEYS = {
    "hMPG2XHyKL": "Tamil",   # ← Tamil only now
}

TARGET_DATES = ["2026-08-20", "2026-08-21"]

TARGET_CINEMAS = [
    "Express Avenue",
    "Nexus Vijaya Mall",
]

ALERTED_FILE = "district_alerted.json"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
]

TG_MAX_LEN = 3800


def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.district.in/",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Ch-Ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Connection": "keep-alive",
    }


# Persistent session for cookies
_session = None

def get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        # Warm-up: visit homepage first to get cookies
        try:
            _session.get(
                "https://www.district.in/",
                headers=get_headers(),
                timeout=15,
            )
            time.sleep(random.uniform(1, 3))
        except Exception as e:
            print(f"Warmup failed: {e}")
    return _session


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
        print(f"[TG] Not configured.")
        return False

    chat_id = chat_id.strip().strip('"').strip("'")

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": message},
            timeout=15,
        )
        print(f"[TG] Status: {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        print(f"[TG] Error: {e}")
        return False


def send_telegram_chunked(full_message):
    if len(full_message) <= TG_MAX_LEN:
        return send_telegram(full_message)

    blocks  = full_message.split("\n\n")
    chunks  = []
    current = ""

    for block in blocks:
        if len(current) + len(block) + 2 > TG_MAX_LEN:
            if current:
                chunks.append(current.rstrip())
            current = block + "\n\n"
        else:
            current += block + "\n\n"

    if current.strip():
        chunks.append(current.rstrip())

    print(f"[TG] Splitting into {len(chunks)} chunks")
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
    
    session = get_session()
    
    # Random delay between requests (2-5 sec) to look human
    time.sleep(random.uniform(2, 5))
    
    try:
        r = session.get(url, headers=get_headers(), timeout=20)
        
        if r.status_code == 403:
            print(f"    HTTP 403 for {lang_name}/{date} — retrying after delay...")
            time.sleep(random.uniform(10, 20))
            r = session.get(url, headers=get_headers(), timeout=20)
            if r.status_code == 403:
                print(f"    Retry also 403. Skipping.")
                return []
        
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
                        f"{s.get('showTime','?')[11:16]}(d={is_disabled},"
                        f"a={avail_seats},s={status})"
                    )

            if not active_sessions:
                if is_target_cinema(name):
                    print(f"    → {name} — ALL INACTIVE, skipping")
                    print(f"      {skipped_reasons}")
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


# ================== MAIN ==================

def check_all():
    # Random 0-60 sec delay to avoid predictable timing
    initial_delay = random.uniform(0, 60)
    print(f"Sleeping {initial_delay:.1f}s to randomize timing...")
    time.sleep(initial_delay)
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n=== District Monitor Run at {now} ===")

    alerted = set(load_json(ALERTED_FILE, []))

    # Skip auto-discovery, use fixed LANGUAGE_KEYS
    lang_keys = LANGUAGE_KEYS
    print(f"Language keys (fixed): {lang_keys}")

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
