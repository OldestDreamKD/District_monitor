
import requests
import os
import json
from datetime import datetime

# ================== CONFIGURATION ==================

MOVIE_ID = "MV176735"
MOVIE_SLUG = "avengers-doomsday-movie-tickets-in-chennai"
MOVIE_NAME = "Avengers: Doomsday"

LANGUAGE_KEYS = {
    "Mm5dpqTqJz": "English",
}

TARGET_DATES = [
    "2026-12-17",
    "2026-12-18",
    "2026-12-19",
    "2026-12-20",
]

# Ignore all cinemas whose names contain these terms
EXCLUDED_CINEMAS = [
    "PVR",
]

# Minimum available seats required before alerting
MIN_AVAILABLE_SEATS = 20

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
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_GROUP_ID")

    if not token or not chat_id:
        print(
            f"[TG] Not configured. "
            f"token_set={bool(token)}, group_id_set={bool(chat_id)}"
        )
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

    blocks = full_message.split("\n\n")
    chunks = []
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

    print(f"[TG] Splitting message into {len(chunks)} chunks")

    all_ok = True

    for i, chunk in enumerate(chunks, 1):
        header = f"(Part {i}/{len(chunks)})\n" if len(chunks) > 1 else ""

        if not send_telegram(header + chunk):
            all_ok = False

    return all_ok


def is_excluded_cinema(name):
    return any(
        term.lower() in name.lower()
        for term in EXCLUDED_CINEMAS
    )


def get_available_seats(session):
    try:
        return int(session.get("avail", 0) or 0)
    except (ValueError, TypeError):
        return 0


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

        val_start = html.find("{", idx)

        if val_start == -1:
            continue

        depth = 0
        i = val_start

        while i < len(html):

            c = html[i]

            if c == "{":
                depth += 1

            elif c == "}":
                depth -= 1

                if depth == 0:
                    try:
                        return json.loads(html[val_start:i + 1])
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
            return None

    except Exception as e:
        print(f"    Request error ({lang_name}/{date}): {e}")
        return None

    data = extract_session_data(r.text, lang_key, date)

    if not data:
        print(f"    No session data for {lang_name}/{date}")
        return None

    page_data = data.get("pageData", {})
    cinemas = []

    for section in ("nearbyCinemas", "farCinemas"):

        for cinema in page_data.get(section, []):

            info = cinema.get("cinemaInfo") or cinema.get("data") or cinema

            name = (
                info.get("name", "")
                or cinema.get("label", "")
            )

            address = info.get("address", "")
            cid = cinema.get("id") or info.get("id")
            entity_url = cinema.get("entityUrl", "")
            all_sessions = cinema.get("sessions", [])

            if not all_sessions:
                continue

            cinemas.append({
                "id": cid,
                "name": name,
                "address": address,
                "sessions": all_sessions,
                "url": (
                    f"https://www.district.in{entity_url}"
                    if entity_url else ""
                ),
            })

    return cinemas


def discover_language_keys(html):

    idx = html.find('"filterBuckets"')

    if idx == -1:
        return {}

    val_start = html.find("[", idx)

    if val_start == -1:
        return {}

    depth = 0
    i = val_start

    while i < len(html):

        c = html[i]

        if c == "[":
            depth += 1

        elif c == "]":
            depth -= 1

            if depth == 0:

                try:
                    buckets = json.loads(html[val_start:i + 1])
                    result = {}

                    for bucket in buckets:

                        if bucket.get("bucketKey") == "language":

                            for b in bucket.get("buckets", []):

                                key = b.get("key", "")
                                title = b.get("title", "")

                                if key and title:
                                    result[key] = title

                    return result

                except Exception:
                    return {}

        i += 1

    return {}


# ================== MAIN MONITOR ==================

def check_all():

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n=== District Monitor Run at {now} ===")
    print(f"Minimum available seats: {MIN_AVAILABLE_SEATS}")
    print(f"Excluded cinemas: {EXCLUDED_CINEMAS}")

    alerted = set(load_json(ALERTED_FILE, []))

    # Discover available languages
    try:

        probe_url = (
            f"https://www.district.in/movies/{MOVIE_SLUG}-{MOVIE_ID}"
            f"?frmtid=rrfdpndypd&fromdate={TARGET_DATES[0]}"
        )

        probe_r = requests.get(
            probe_url,
            headers=HEADERS,
            timeout=20
        )

        discovered = discover_language_keys(probe_r.text)

        lang_keys = LANGUAGE_KEYS
        print(f"Language keys: {lang_keys}")

    except Exception as e:

        lang_keys = LANGUAGE_KEYS
        print(f"Language discovery error: {e}, using defaults")

    new_hits = []

    for lang_key, lang_name in lang_keys.items():

        for date in TARGET_DATES:

            print(f"\n  Checking {lang_name} / {date} ...")

            cinemas = get_cinemas_for(
                lang_key,
                lang_name,
                date
            )

            if cinemas is None:
                print("    Check failed; preserving previous alert state.")
                continue

            print(f"    Cinemas found: {len(cinemas)}")

            for cinema in cinemas:

                cname = cinema["name"]

                # Exclude PVR and any other configured cinema names
                if is_excluded_cinema(cname):
                    print(f"    SKIPPED: {cname}")
                    continue

                print(f"    Checking: {cname}")

                for session in cinema["sessions"]:

                    is_disabled = session.get("disableClick", False)
                    avail = get_available_seats(session)

                    # Only consider active shows with enough seats
                    if is_disabled or avail < MIN_AVAILABLE_SEATS:
                        continue

                    show_time = session.get("showTime", "")
                    time_str = show_time[11:16] if len(show_time) >= 16 else show_time

                    fmt = session.get("scrnFmt", "")
                    audi = session.get("audi", "")

                    # Track each individual show, not the whole cinema
                    key = (
                        f"{lang_key}|{cinema['id']}|{date}|"
                        f"{show_time}|{fmt}|{audi}"
                    )

                    if key in alerted:
                        continue

                    total = session.get("total", 0)

                    new_hits.append({
                        "key": key,
                        "language": lang_name,
                        "cinema": cname,
                        "date": date,
                        "time": time_str,
                        "available": avail,
                        "total": total,
                        "format": fmt,
                        "audi": audi,
                        "url": cinema["url"],
                    })

                    print(
                        f"       NEW SHOW: {time_str} | "
                        f"{avail}/{total} seats"
                    )

    # No new qualifying shows
    if not new_hits:
        print("\nNo new qualifying shows.")
        return

    # Build Telegram alert
    lines = [
        f"🕷️ {MOVIE_NAME} — NEW SHOW / GOOD SEAT AVAILABILITY!\n",
        f"Minimum seats: {MIN_AVAILABLE_SEATS}\n",
    ]

    for h in new_hits:

        d = h["date"]
        pretty_date = f"{d[8:10]}/{d[5:7]}/{d[0:4]}"

        lines.append(f"Cinema:   {h['cinema']}")
        lines.append(f"Language: {h['language']}")
        lines.append(f"Date:     {pretty_date}")
        lines.append(f"Time:     {h['time']}")

        format_text = h["format"] or "Standard"
        lines.append(f"Format:   {format_text}")

        if h["audi"]:
            lines.append(f"Screen:   {h['audi']}")

        lines.append(
            f"Seats:    {h['available']}/{h['total']} available"
        )

        if h["url"]:
            lines.append(f"Book:     {h['url']}")

        lines.append("")

    message = "\n".join(lines)

    if send_telegram_chunked(message):

        for h in new_hits:
            alerted.add(h["key"])

        save_json(ALERTED_FILE, sorted(alerted))

        print(f"\nSaved {len(new_hits)} new show alerts.")

    else:
        print("\nTelegram failed. NOT saving — will retry next run.")


if __name__ == "__main__":
    check_all()
