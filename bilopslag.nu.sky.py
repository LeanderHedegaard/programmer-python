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

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
BILOPSLAG_COOKIES = json.loads(os.getenv("BILOPSLAG_COOKIES_JSON", "") or "{}")

INSURANCE_URL = "https://data1.nummerplade.net/dmr_forsikring.php?stelnr="
MAX_CONNECTIONS = 20
MAX_PAGES = 50

PLADE_REGEX = r"^[A-Z]{2}\d{3,5}$"

BILOPSLAG_HEADERS = {
    "accept": "*/*",
    "accept-language": "da,en-US;q=0.9,en;q=0.8,es;q=0.7",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "referer": "https://bilopslag.nu/avanceret-soegning",
    "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
}


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


def hent_plaader_fra_bilopslag():
    today = datetime.now(ZoneInfo("Europe/Copenhagen")).date()
    today_str = today.strftime("%Y-%m-%d")

    base_url = (
        "https://bilopslag.nu/api/advanced_search"
        "?registration_matches=%25%25%25%25%25"
        "&first_registration_date_gteq={dato}"
        "&page={side}"
    )

    plader_og_stel = []

    for page in range(1, MAX_PAGES + 1):
        url = base_url.format(dato=today_str, side=page)
        print(f"\n🔎 Henter bilopslag side {page}: {url}")

        try:
            resp = requests.get(
                url,
                headers=BILOPSLAG_HEADERS,
                cookies=BILOPSLAG_COOKIES,
                timeout=20,
            )

            print(f"HTTP status bilopslag side {page}: {resp.status_code}")
            resp.raise_for_status()

            data = resp.json()
            biler = data.get("data", [])

            print(f"→ Antal biler på side {page}: {len(biler)}")

            if not biler:
                print("Ingen flere biler, stopper bilopslag-søgning.")
                break

            for bil in biler:
                plade = str(bil.get("registration", "")).upper().strip()
                vin = str(bil.get("vin", "")).upper().strip()

                if not vin or not plade:
                    continue

                if re.match(PLADE_REGEX, plade):
                    plader_og_stel.append((plade, vin))

        except Exception as e:
            print(f"⚠️ Fejl på bilopslag side {page}: {e}")
            break

    unique = {}
    for plade, vin in plader_og_stel:
        unique[plade] = vin

    result = list(unique.items())

    print(f"\n🎯 Bilopslag fandt {len(result)} unikke gyldige plader med stelnummer.")
    return result


async def get_insurance_info(session, stelnr):
    url = f"{INSURANCE_URL}{stelnr}"

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
        print(f"Fejl ved forsikringsopslag for stelnummer {stelnr}: {e}")
        return "Ukendt", "Ukendt"


async def process_plate(session, regnr, stelnr, plates_data, uploaded_plates, semaphore):
    async with semaphore:
        selskab, oprettet = await get_insurance_info(session, stelnr)

        if not selskab or selskab == "Ukendt":
            print(f"Springer {regnr} over - intet forsikringsselskab fundet.")
            return

        today = datetime.now(ZoneInfo("Europe/Copenhagen")).date()
        dato = today.strftime("%Y-%m-%d")

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

        ok = upload_plate_to_supabase(selskab, entry)

        if ok:
            uploaded_plates.add(regnr)
            print(f"✅ Behandlet: {regnr} | {selskab} | forsikringsdato: {oprettet}")


async def check_new_registrations():
    print("Starter bilopslag-scriptet.")

    plates_data = load_existing_data()
    uploaded_plates = set()

    plader_og_stel = hent_plaader_fra_bilopslag()

    if not plader_og_stel:
        print("Ingen biler fundet i bilopslag, afslutter.")
        return

    print(f"Starter forsikringsopslag for {len(plader_og_stel)} plader.")

    connector = aiohttp.TCPConnector(limit=MAX_CONNECTIONS)
    semaphore = asyncio.Semaphore(MAX_CONNECTIONS)

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            process_plate(session, regnr, stelnr, plates_data, uploaded_plates, semaphore)
            for regnr, stelnr in plader_og_stel
        ]

        await asyncio.gather(*tasks)

    if uploaded_plates:
        save_to_json(plates_data)

    print(f"[INFO] Bilopslag: Forsøgte at behandle {len(plader_og_stel)} plader.")
    print(f"[INFO] Bilopslag: Uploadet/ignoreret i Supabase: {len(uploaded_plates)} plader.")


if __name__ == "__main__":
    print("Bilopslag-script startet.")
    delete_old_plates_from_supabase()
    asyncio.run(check_new_registrations())
    print("Bilopslag-script færdigt.")
