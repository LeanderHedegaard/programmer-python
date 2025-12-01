import asyncio
import aiohttp
import os
import re
import json
import requests
import subprocess
from datetime import datetime, timedelta
from plyer import notification

# -----------------------------
#      FILSTIER
# -----------------------------
FOUND_PLATES_FILE = "found_plates.txt"
JSON_FILE_PATH = r"C:\Users\Leander\Desktop\insurance-app\plates.json"

# Netlify deploy opsætning
NETLIFY_CWD = r"C:\Users\Leander\Desktop"
DEPLOY_DIR = r"C:\Users\Leander\Desktop\insurance-app"

# -----------------------------
#      HEADERS / COOKIES
# -----------------------------
BILOPSLAG_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "da,en-US;q=0.9,en;q=0.8",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
}

BILOPSLAG_COOKIES = {
    "CookieConsent": "din_cookie_her",
    "_bilopslag_session": "din_session_her",
    "_ga": "din_ga_her",
    "_ga_68VDRB1B8D": "din_ga2_her",
}

PLADE_REGEX = r"^[A-Z]{2}\d{3,5}$"

# -----------------------------
#      DATA FUNKTIONER
# -----------------------------
def load_existing_data():
    try:
        with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_to_json(data):
    os.makedirs(os.path.dirname(JSON_FILE_PATH), exist_ok=True)
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

# -----------------------------
#      HENT PLADELISTE
# -----------------------------
def hent_plaader_fra_bilopslag():
    print("\n🔎 Henter nye registreringer fra bilopslag.nu...")

    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    y_str = yesterday.strftime("%Y-%m-%d")

    url_template = (
        "https://bilopslag.nu/api/advanced_search"
        "?registration_matches=%25%25%25%25%25"
        "&first_registration_date_gteq={dato}"
        "&page={side}"
    )

    plader = []

    for page in range(1, 8):
        url = url_template.format(dato=y_str, side=page)
        print(f"Henter side {page}: {url}")

        try:
            response = requests.get(url, headers=BILOPSLAG_HEADERS, cookies=BILOPSLAG_COOKIES, timeout=10)
            data = response.json()
            biler = data.get("data", [])

            if not biler:
                break

            for bil in biler:
                plade = bil.get("registration", "").upper()
                vin = bil.get("vin", "").upper()
                if re.match(PLADE_REGEX, plade) and vin:
                    plader.append((plade, vin))
                    print(f"→ Fundet: {plade} - VIN: {vin}")

        except Exception as e:
            print("Fejl:", e)
            break

    print(f"🎯 Færdig: {len(plader)} plader fundet\n")
    return plader

# -----------------------------
#      API: FORSIKRINGSDATA
# -----------------------------
async def get_insurance_info(session, stelnr):
    url = f"https://data1.nummerplade.net/dmr_forsikring.php?stelnr={stelnr}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }

    try:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                if data.get("status_code") == "1":
                    car = data.get("carData", {})
                    return car.get("selskab", "Ukendt"), car.get("oprettet", "Ukendt")
    except:
        pass

    return "Ukendt", "Ukendt"

# -----------------------------
#      BEHANDL EN PLADE
# -----------------------------
async def process_plate(session, regnr, stelnr, plates_data, previous, new_set, sem):
    if regnr in previous:
        return

    async with sem:
        selskab, oprettet = await get_insurance_info(session, stelnr)

        try:
            date_obj = datetime.strptime(oprettet, "%d-%m-%Y").date()
        except:
            return

        today = datetime.now().date()
        yesterday = today - timedelta(days=1)

        if date_obj not in (today, yesterday):
            return

        entry = {
            "date": date_obj.strftime("%Y-%m-%d"),
            "plate": regnr,
            "checked": False,
            "premium": 0,
        }

        if selskab not in plates_data:
            plates_data[selskab] = []

        if regnr not in {p["plate"] for p in plates_data[selskab]}:
            plates_data[selskab].append(entry)
            save_new_plate(regnr)
            new_set.add(regnr)
            print(f"✅ Ny registrering: {regnr} hos {selskab}")

# -----------------------------
#      HOVEDPROCESSEN
# -----------------------------
async def check_new_registrations():
    plates_data = load_existing_data()
    previous = load_previous_plates()
    new_set = set()

    plader_og_stel = hent_plaader_fra_bilopslag()
    if not plader_og_stel:
        print("Ingen nye plader fundet.")
        return

    sem = asyncio.Semaphore(30)
    connector = aiohttp.TCPConnector(limit=30)

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            process_plate(session, reg, vin, plates_data, previous, new_set, sem)
            for reg, vin in plader_og_stel
        ]
        await asyncio.gather(*tasks)

    if new_set:
        save_to_json(plates_data)
        notification.notify(
            title="Nye nummerplader",
            message=f"Fundet {len(new_set)} nye plader.",
            timeout=10
        )

# -----------------------------
#      NETLIFY DEPLOY (AUTOMATISK)
# -----------------------------
def deploy_site():
    print("\n🚀 Udfører Netlify deploy...")

    cmd = f'npx netlify deploy --dir="{DEPLOY_DIR}" --prod'

    result = subprocess.run(
        cmd,
        shell=True,
        cwd=NETLIFY_CWD,       # ← Matcher hvordan du selv deployer manuelt
        capture_output=True,
        text=True
    )

    print("---- STDOUT ----")
    print(result.stdout)
    print("---- STDERR ----")
    print(result.stderr)

    if result.returncode == 0:
        print("🎉 Deploy gennemført!")
    else:
        print("❌ Deploy fejlede:", result.returncode)

# -----------------------------
#      MAIN
# -----------------------------
if __name__ == "__main__":
    asyncio.run(check_new_registrations())
    deploy_site()
