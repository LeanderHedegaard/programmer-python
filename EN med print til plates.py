import asyncio
import aiohttp
import os
import re
import json
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

# ---------------------------------
# Environment-detektion
# ---------------------------------
RUNNING_IN_GITHUB = os.getenv("GITHUB_ACTIONS") == "true"

# ---------------------------------
# Notifikation (kun lokalt)
# ---------------------------------
if RUNNING_IN_GITHUB:
    class DummyNotification:
        def notify(self, *args, **kwargs):
            pass
    notification = DummyNotification()
else:
    try:
        from plyer import notification
    except Exception:
        class DummyNotification:
            def notify(self, *args, **kwargs):
                pass
        notification = DummyNotification()

# ---------------------------------
# Stier
# ---------------------------------
FOUND_PLATES_FILE = "found_plates.txt"

if RUNNING_IN_GITHUB:
    JSON_FILE_PATH = "insurance-app/plates.json"
else:
    JSON_FILE_PATH = r"C:\Users\Leander\Desktop\insurance-app\plates.json"

# Nummerplade-range
start_regnr = "EN00000"
end_regnr = "EN99999"

# API'er
BASE_URL = "https://www.nummerplade.net/nummerplade/"
INSURANCE_URL = "https://data1.nummerplade.net/dmr_forsikring.php?stelnr="

MAX_CONNECTIONS = 40

# ---------------------------------
# Fil-funktioner
# ---------------------------------
def load_existing_data():
    try:
        with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_to_json(data):
    dir_name = os.path.dirname(JSON_FILE_PATH)
    if dir_name and not os.path.exists(dir_name):
        os.makedirs(dir_name, exist_ok=True)

    with open(JSON_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4, sort_keys=True)


def load_previous_plates():
    if os.path.exists(FOUND_PLATES_FILE):
        with open(FOUND_PLATES_FILE, "r") as f:
            return set(f.read().splitlines())
    return set()


def save_new_plate(plate):
    with open(FOUND_PLATES_FILE, "a") as f:
        f.write(f"{plate}\n")

# ---------------------------------
# HTML parsing helpers
# ---------------------------------
def extract_last_change_date(html: str):
    m = re.search(r'id="seneste_aendring">d\.\s*(\d{2}-\d{2}-\d{4})', html)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d-%m-%Y").date()
    except ValueError:
        return None


def extract_stelnr(html: str):
    m = re.search(r'var\s+search_data\s*=\s*"(\w+)"', html)
    if m:
        return m.group(1).upper()

    m = re.search(r"stelnummer\s+([A-Z0-9]+)", html, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    m = re.search(r"(VF[A-Z0-9]{10,}|WVW[A-Z0-9]{10,}|[A-HJ-NPR-Z0-9]{17})", text)
    if m:
        return m.group(1).upper()

    return None

# ---------------------------------
# API kald
# ---------------------------------
async def get_insurance_info(session, stelnr: str):
    url = f"{INSURANCE_URL}{stelnr.upper()}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": "https://nummerplade.net/",
        "X-Requested-With": "XMLHttpRequest",
    }

    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("status_code") == "1":
                    car_data = data.get("carData", {})
                    return (
                        car_data.get("selskab", "Ukendt").strip(),
                        car_data.get("oprettet", "Ukendt"),
                    )
    except Exception as e:
        print(f"Fejl ved forsikringsopslag ({stelnr}): {e}")

    return "Ukendt", "Ukendt"


async def get_car_info(session, regnr: str, semaphore: asyncio.Semaphore):
    url = f"{BASE_URL}{regnr}.html"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html,application/xhtml+xml",
    }

    async with semaphore:
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return None, None

                html = await resp.text()

                last_change = extract_last_change_date(html)
                if not last_change:
                    return None, None

                today = datetime.now().date()
                yesterday = today - timedelta(days=1)
                if last_change not in (today, yesterday):
                    return None, None

                stelnr = extract_stelnr(html)
                if not stelnr:
                    return None, None

                return regnr, stelnr

        except Exception as e:
            print(f"Fejl ved {regnr}: {e}")
            return None, None

# ---------------------------------
# Hovedprocessen
# ---------------------------------
async def check_new_registrations():
    plates_data = load_existing_data()
    previous_plates = load_previous_plates()
    new_plates = set()

    regnr_start = int(start_regnr[2:])
    regnr_slut = int(end_regnr[2:])

    connector = aiohttp.TCPConnector(limit=MAX_CONNECTIONS)
    semaphore = asyncio.Semaphore(MAX_CONNECTIONS)

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            get_car_info(session, f"EN{num:05d}", semaphore)
            for num in range(regnr_start, regnr_slut + 1)
        ]

        results = await asyncio.gather(*tasks)

        for regnr, stelnr in results:
            if not regnr or not stelnr:
                continue

            if regnr in previous_plates:
                continue

            print(f"Ny registrering: {regnr}")
            save_new_plate(regnr)
            new_plates.add(regnr)

            selskab, oprettet = await get_insurance_info(session, stelnr)

            try:
                dato_obj = datetime.strptime(oprettet, "%d-%m-%Y").date()
                today = datetime.now().date()
                yesterday = today - timedelta(days=1)
                if dato_obj not in (today, yesterday):
                    continue
                dato = dato_obj.strftime("%Y-%m-%d")
            except Exception:
                continue

            entry = {
                "date": dato,
                "plate": regnr,
                "checked": False,
                "premium": 0,
            }

            if selskab not in plates_data:
                plates_data[selskab] = []
            else:
                existing_plates = {p["plate"] for p in plates_data[selskab]}
                if regnr in existing_plates:
                    continue

            plates_data[selskab].append(entry)

    if new_plates:
        save_to_json(plates_data)
        notification.notify(
            title="Nye Nummerplader",
            message=f"Fundet {len(new_plates)} nye plader",
            timeout=10,
        )

# ---------------------------------
# MAIN
# ---------------------------------
if __name__ == "__main__":
    asyncio.run(check_new_registrations())
