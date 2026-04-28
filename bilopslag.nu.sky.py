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
FOUND_PLATES_FILE = str(REPO_ROOT / "found_plates.txt")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

BILOPSLAG_COOKIES = json.loads(os.getenv("BILOPSLAG_COOKIES_JSON", "") or "{}")

INSURANCE_URL = "https://data1.nummerplade.net/dmr_forsikring.php?stelnr="
MAX_CONNECTIONS = 20

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

PLADE_REGEX = r"^[A-Z]{2}\d{3,5}$"


def load_existing_data():
    try:
        with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return {}
            if isinstance(data, dict):
                return data
            return {}
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


def hent_plaader_fra_bilopslag():
    i_dag = datetime.now(ZoneInfo("Europe/Copenhagen")).date()
    i_gar = i_dag - timedelta(days=1)
    i_gar_str = i_gar.strftime("%Y-%m-%d")

    base_url = (
        "https://bilopslag.nu/api/advanced_search"
        "?registration_matches=%25%25%25%25%25"
        "&first_registration_date_gteq={dato}"
        "&page={side}"
    )

    plader_og_stel = []

    for page in range(1, 8):
        url = base_url.format(dato=i_gar_str, side=page)
        print(f"\n🔎 Henter side {page}: {url}")

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
                    print(f" - Plade: {plade} | Stelnummer: {vin}")

        except Exception as e:
            print(f"⚠️ Fejl på bilopslag side {page}: {e}")
            break

    print(f"\n🎯 Fandt i alt {len(plader_og_stel)} gyldige plader med stelnummer.")
    return plader_og_stel


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


async def process_plate(session, regnr, stelnr, plates_data, previous_plates, new_plates, semaphore):
    if regnr in previous_plates:
        print(f"Springer over eksisterende plade: {regnr}")
        return

    async with semaphore:
        selskab, oprettet = await get_insurance_info(session, stelnr)

        try:
            dato_obj = datetime.strptime(oprettet, "%d-%m-%Y").date()
            today = datetime.now(ZoneInfo("Europe/Copenhagen")).date()
            yesterday = today - timedelta(days=1)

            if dato_obj not in (today, yesterday):
                print(f"Springer {regnr} over - oprettet dato er {oprettet}")
                return

            dato = dato_obj.strftime("%Y-%m-%d")

        except Exception:
            print(f"Springer {regnr} over - kunne ikke læse oprettet dato: {oprettet}")
            return

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
            return

        plates_data[selskab].append(entry)

        upload_plate_to_supabase(selskab, entry)

        save_new_plate(regnr)
        new_plates.add(regnr)

        print(f"✅ Ny registrering behandlet: {regnr} | {selskab}")


async def check_new_registrations():
    print("Starter check_new_registrations()")

    plates_data = load_existing_data()
    previous_plates = load_previous_plates()
    new_plates = set()

    plader_og_stel = hent_plaader_fra_bilopslag()

    if not plader_og_stel:
        print("Ingen biler fundet, afslutter.")
        return

    connector = aiohttp.TCPConnector(limit=MAX_CONNECTIONS)
    semaphore = asyncio.Semaphore(MAX_CONNECTIONS)

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            process_plate(
                session,
                regnr,
                stelnr,
                plates_data,
                previous_plates,
                new_plates,
                semaphore,
            )
            for regnr, stelnr in plader_og_stel
        ]

        await asyncio.gather(*tasks)

    if new_plates:
        save_to_json(plates_data)
        print(f"[INFO] Fundet og behandlet {len(new_plates)} nye plader.")
    else:
        print("[INFO] Ingen nye plader at gemme.")


if __name__ == "__main__":
    print("Script startet.")
    asyncio.run(check_new_registrations())
    print("Script færdigt.")
