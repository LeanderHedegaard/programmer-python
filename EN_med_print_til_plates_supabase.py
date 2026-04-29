import asyncio
import aiohttp
import os
import re
import json
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

# ============================================================
# KONFIGURATION
# ============================================================

REPO_ROOT = Path(__file__).resolve().parent
JSON_FILE_PATH = Path(os.getenv("JSON_FILE_PATH", REPO_ROOT / "public" / "plates" / "plates.json"))
JSON_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

PREFIX = "EN"
START_REGNR = PREFIX + "00000"
END_REGNR = PREFIX + "99999"

BASE_URL = "https://www.nummerplade.net/nummerplade/"
INSURANCE_URL = "https://data1.nummerplade.net/dmr_forsikring.php?stelnr="
MAX_CONNECTIONS = 40


def load_existing_data():
    try:
        with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_to_json(data):
    JSON_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(JSON_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4, sort_keys=True)


def delete_old_plates_from_supabase():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("⚠️ Mangler SUPABASE_URL eller SUPABASE_SERVICE_ROLE_KEY. Springer oprydning over.")
        return False

    cutoff_date = (datetime.now(ZoneInfo("Europe/Copenhagen")).date() - timedelta(days=2)).isoformat()
    url = f"{SUPABASE_URL}/rest/v1/plates?date=lt.{cutoff_date}"

    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.delete(url, headers=headers, timeout=20)

        if response.status_code not in (200, 204):
            print(f"❌ Oprydning fejlede: {response.status_code} {response.text}")
            return False

        print(f"🧹 Plader med date før {cutoff_date} er slettet fra Supabase.")
        return True

    except Exception as e:
        print(f"❌ Fejl ved oprydning i Supabase: {e}")
        return False


def upload_plate_to_supabase(company, entry):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("⚠️ Mangler SUPABASE_URL eller SUPABASE_SERVICE_ROLE_KEY. Springer Supabase upload over.")
        return False

    url = f"{SUPABASE_URL}/rest/v1/plates?on_conflict=company,plate"

    payload = {
        "company": company,
        "plate": entry["plate"],
        "date": entry["date"],
        "checked": entry.get("checked", False),
        "premium": entry.get("premium", 0),
        "note": entry.get("note", ""),
    }

    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=ignore-duplicates,return=minimal",
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)

        if response.status_code in (200, 201, 204):
            print(f"✅ Uploadet/ignoreret i Supabase: {company} | {entry['plate']}")
            return True

        if response.status_code == 409:
            print(f"ℹ️ Findes allerede i Supabase: {company} | {entry['plate']}")
            return True

        print(f"❌ Supabase upload fejlede: {response.status_code} {response.text}")
        return False

    except Exception as e:
        print(f"❌ Fejl ved Supabase upload: {e}")
        return False


def extract_last_change_date(html):
    match = re.search(r'id="seneste_aendring">d\. (\d{2}-\d{2}-\d{4})', html)
    return datetime.strptime(match.group(1), "%d-%m-%Y").date() if match else None


def extract_stelnr(html):
    for pattern in [
        r'var\s+search_data\s*=\s*"(\w+)"',
        r'stelnummer\s+(\w+)'
    ]:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return None


async def get_insurance_info(session, stelnr):
    url = f"{INSURANCE_URL}{stelnr.upper()}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": "https://nummerplade.net/",
        "X-Requested-With": "XMLHttpRequest",
    }

    try:
        async with session.get(url, headers=headers, timeout=30) as response:
            if response.status == 200:
                data = await response.json()

                if data.get("status_code") == "1":
                    car_data = data.get("carData", {})
                    selskab = car_data.get("selskab", "Ukendt")
                    oprettet = car_data.get("oprettet", "Ukendt")

                    return str(selskab).strip(), str(oprettet).strip()

            return "Ukendt", "Ukendt"

    except Exception as e:
        print(f"Fejl ved forsikringsinfo for {stelnr}: {e}")
        return "Ukendt", "Ukendt"


async def get_car_info(session, regnr, semaphore):
    url = f"{BASE_URL}{regnr}.html"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    }

    async with semaphore:
        try:
            async with session.get(url, headers=headers, timeout=30) as response:
                if response.status == 200:
                    html = await response.text()

                    last_change = extract_last_change_date(html)
                    today = datetime.now(ZoneInfo("Europe/Copenhagen")).date()
                    yesterday = today - timedelta(days=1)

                    if last_change and last_change in (today, yesterday):
                        return regnr, extract_stelnr(html)

                elif response.status == 429:
                    retry_after = int(response.headers.get("Retry-After", 5))
                    print(f"429 på {regnr}. Venter {retry_after} sekunder.")
                    await asyncio.sleep(retry_after)
                    return await get_car_info(session, regnr, semaphore)

                return None, None

        except Exception as e:
            print(f"Fejl ved {regnr}: {e}")
            return None, None


async def check_new_registrations():
    print(f"Starter {PREFIX}-scriptet.")

    plates_data = load_existing_data()
    processed_plates = set()
    attempted_uploads = 0

    start_num = int(START_REGNR[2:])
    end_num = int(END_REGNR[2:])

    connector = aiohttp.TCPConnector(limit=MAX_CONNECTIONS)
    semaphore = asyncio.Semaphore(MAX_CONNECTIONS)

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            get_car_info(session, f"{PREFIX}{num:05d}", semaphore)
            for num in range(start_num, end_num + 1)
        ]

        for future in asyncio.as_completed(tasks):
            regnr, stelnr = await future

            if not regnr:
                continue

            print(f"Ny/aktiv registrering fundet: {regnr}")

            if not stelnr:
                print(f"Springer {regnr} over - intet stelnummer.")
                continue

            selskab, oprettet = await get_insurance_info(session, stelnr)

            try:
                dato_obj = datetime.strptime(oprettet, "%d-%m-%Y").date()
                today = datetime.now(ZoneInfo("Europe/Copenhagen")).date()
                yesterday = today - timedelta(days=1)

                if dato_obj not in (today, yesterday):
                    print(f"Springer {regnr} over - forsikringsdato er {oprettet}")
                    continue

                dato = dato_obj.strftime("%Y-%m-%d")

            except Exception:
                print(f"Springer {regnr} over - kunne ikke læse forsikringsdato: {oprettet}")
                continue

            entry = {
                "date": dato,
                "plate": regnr,
                "checked": False,
                "premium": 0,
                "note": "",
            }

            if selskab not in plates_data:
                plates_data[selskab] = []

            existing_plates_local = {p.get("plate") for p in plates_data.get(selskab, [])}

            if regnr not in existing_plates_local:
                plates_data[selskab].append(entry)

            attempted_uploads += 1
            uploaded_or_ignored = upload_plate_to_supabase(selskab, entry)

            if uploaded_or_ignored:
                processed_plates.add(regnr)
                print(f"✅ Behandlet: {regnr} | {selskab}")

    if processed_plates:
        save_to_json(plates_data)

    print(f"[INFO] {PREFIX}: Forsøgte Supabase upload/ignore af {attempted_uploads} plader.")
    print(f"[INFO] {PREFIX}: Behandlet {len(processed_plates)} plader.")


if __name__ == "__main__":
    print(f"{PREFIX}-script startet.")
    delete_old_plates_from_supabase()
    asyncio.run(check_new_registrations())
    print(f"{PREFIX}-script færdigt.")
