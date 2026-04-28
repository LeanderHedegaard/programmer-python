import asyncio
import aiohttp
import os
import re
import json
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
JSON_FILE_PATH = Path(os.getenv("JSON_FILE_PATH", REPO_ROOT / "public" / "plates" / "plates.json"))
JSON_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
FOUND_PLATES_FILE = str(REPO_ROOT / "found_plates.txt")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

PREFIX = "EP"
START_REGNR = f"{PREFIX}00000"
END_REGNR = f"{PREFIX}99999"

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


def load_previous_plates():
    if os.path.exists(FOUND_PLATES_FILE):
        with open(FOUND_PLATES_FILE, "r", encoding="utf-8") as f:
            return set(f.read().splitlines())
    return set()


def save_new_plate(plate):
    with open(FOUND_PLATES_FILE, "a", encoding="utf-8") as f:
        f.write(f"{plate}\n")


def upload_plate_to_supabase(company, entry):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("⚠️ Mangler SUPABASE_URL eller SUPABASE_SERVICE_ROLE_KEY. Springer Supabase upload over.")
        return False

    url = f"{SUPABASE_URL}/rest/v1/plates"
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
        "Prefer": "resolution=ignore-duplicates",
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        if response.status_code not in (200, 201, 204):
            print(f"❌ Supabase upload fejlede: {response.status_code} {response.text}")
            return False
        print(f"✅ Uploadet til Supabase: {company} | {entry['plate']}")
        return True
    except Exception as e:
        print(f"❌ Fejl ved Supabase upload: {e}")
        return False


def extract_last_change_date(html):
    match = re.search(r'id="seneste_aendring">d\. (\d{2}-\d{2}-\d{4})', html)
    return datetime.strptime(match.group(1), "%d-%m-%Y").date() if match else None


def extract_stelnr(html):
    for pattern in [r'var\s+search_data\s*=\s*"(\w+)"', r'stelnummer\s+(\w+)']:
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
                    return (
                        str(car_data.get("selskab", "Ukendt")).strip(),
                        str(car_data.get("oprettet", "Ukendt")).strip(),
                    )
            return "Ukendt", "Ukendt"
    except Exception as e:
        print(f"Fejl ved forsikringsinfo for {stelnr}: {e}")
        return "Ukendt", "Ukendt"


async def get_car_info(session, regnr, semaphore):
    url = f"{BASE_URL}{regnr}.html"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    async with semaphore:
        try:
            async with session.get(url, headers=headers, timeout=30) as response:
                if response.status == 200:
                    html = await response.text()
                    last_change = extract_last_change_date(html)
                    if last_change and last_change >= datetime.now(ZoneInfo("Europe/Copenhagen")).date() - timedelta(days=1):
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
    previous_plates = load_previous_plates()
    new_plates = set()

    start_num = int(START_REGNR[2:])
    end_num = int(END_REGNR[2:])

    connector = aiohttp.TCPConnector(limit=MAX_CONNECTIONS)
    semaphore = asyncio.Semaphore(MAX_CONNECTIONS)

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = (
            get_car_info(session, f"{PREFIX}{num:05d}", semaphore)
            for num in range(start_num, end_num + 1)
        )

        for future in asyncio.as_completed(tasks):
            regnr, stelnr = await future

            if not regnr:
                continue

            if regnr in previous_plates:
                print(f"Springer over eksisterende plade: {regnr}")
                continue

            print(f"Ny registrering: {regnr}")
            save_new_plate(regnr)
            new_plates.add(regnr)

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
                print(f"Springer {regnr} over - kunne ikke læse dato: {oprettet}")
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

            existing_plates = {p.get("plate") for p in plates_data.get(selskab, [])}
            if regnr in existing_plates:
                print(f"Springer duplicate over i lokal JSON: {regnr}")
                continue

            plates_data[selskab].append(entry)
            upload_plate_to_supabase(selskab, entry)

            print(f"✅ Ny registrering behandlet: {regnr} | {selskab}")

    if new_plates:
        save_to_json(plates_data)
        print(f"[INFO] {PREFIX}: Fundet og behandlet {len(new_plates)} nye plader.")
    else:
        print(f"[INFO] {PREFIX}: Ingen nye plader at gemme.")


if __name__ == "__main__":
    print("Script startet.")
    asyncio.run(check_new_registrations())
    print("Script færdigt.")
