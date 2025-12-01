import asyncio
import aiohttp
import os
import re
import json
import requests
import subprocess
from datetime import datetime, timedelta
from plyer import notification

# ---------------------------------
# Stier
# ---------------------------------
FOUND_PLATES_FILE = "found_plates.txt"
JSON_FILE_PATH = r"C:\Users\Leander\Desktop\insurance-app\plates.json"

# API'er
insurance_url = "https://data1.nummerplade.net/dmr_forsikring.php?stelnr="

# Maksimale forbindelser til API
MAX_CONNECTIONS = 20

# HEADERS til bilopslag.nu API
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
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    ),
}

# Cookies – du kan sætte dem rigtigt lokalt, men de er ikke nødvendige i GitHub
BILOPSLAG_COOKIES = {
    "CookieConsent": "din_cookie_her",
    "_bilopslag_session": "din_session_her",
    "_ga": "din_ga_her",
    "_ga_68VDRB1B8D": "din_ga2_her",
}

PLADE_REGEX = r"^[A-Z]{2}\d{3,5}$"

# ---------------------------------
# GitHub / lokal deploy-opsætning
# ---------------------------------
RUNNING_IN_GITHUB = os.getenv("GITHUB_ACTIONS") == "true"

if RUNNING_IN_GITHUB:
    # I GitHub Actions håndteres deploy af workflow-filen
    def deploy_site():
        print("⏭️ Skipper Netlify deploy i bilopslag.nu.py (håndteres af GitHub Actions).")
else:
    # Lokale stier til din egen maskine
    NETLIFY_CWD = r"C:\Users\Leander\Desktop"
    DEPLOY_DIR = r"C:\Users\Leander\Desktop\insurance-app"

    def deploy_site():
        print("\n🚀 Udfører Netlify deploy (lokalt)...")

        cmd = f'npx netlify deploy --dir="{DEPLOY_DIR}" --prod'

        result = subprocess.run(
            cmd,
            shell=True,
            cwd=NETLIFY_CWD,
            capture_output=True,
            text=True,
        )

        print("---- STDOUT ----")
        print(result.stdout)
        print("---- STDERR ----")
        print(result.stderr)

        if result.returncode == 0:
            print("🎉 Deploy gennemført!")
        else:
            print("❌ Deploy fejlede:", result.returncode)


# ---------------------------------
# Hjælpefunktioner til filer
# ---------------------------------
def load_existing_data():
    try:
        with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
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


# ---------------------------------
# Hent plader fra bilopslag.nu
# ---------------------------------
def hent_plaader_fra_bilopslag():
    """Henter nummerplader og stelnumre fra bilopslag.nu API."""
    i_dag = datetime.now().date()
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
                timeout=10,
            )
            data = resp.json()
            biler = data.get("data", [])
            print(f"→ Antal biler på side {page}: {len(biler)}")

            if not biler:
                print("Ingen flere biler, stopper.")
                break

            for bil in biler:
                plade = bil.get("registration", "").upper()
                vin = bil.get("vin", "").upper()

                if not vin or not plade:
                    continue

                if re.match(PLADE_REGEX, plade):
                    plader_og_stel.append((plade, vin))
                    print(f" - Plade: {plade} | Stelnummer: {vin}")

        except Exception as e:
            print(f"⚠️ Fejl på side {page}: {e}")
            break

    print(f"\n🎯 Fandt i alt {len(plader_og_stel)} gyldige plader med stelnummer.")
    return plader_og_stel


# ---------------------------------
# Forsikringsopslag
# ---------------------------------
async def get_insurance_info(session, stelnr):
    url = f"{insurance_url}{stelnr}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": "https://nummerplade.net/",
        "X-Requested-With": "XMLHttpRequest",
    }

    try:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                if data.get("status_code") == "1":
                    car_data = data.get("carData", {})
                    return (
                        car_data.get("selskab", "Ukendt").strip(),
                        car_data.get("oprettet", "Ukendt"),
                    )
            return "Ukendt", "Ukendt"
    except Exception as e:
        print(f"Fejl ved forsikringsopslag: {e}")
        return "Ukendt", "Ukendt"


# ---------------------------------
# Hovedproces
# ---------------------------------
async def check_new_registrations():
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
        tasks = (
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
        )

        await asyncio.gather(*tasks)

    if new_plates:
        save_to_json(plates_data)
        notification.notify(
            title="Nye Nummerplader",
            message=f"Fundet {len(new_plates)} nye plader",
            timeout=10,
        )


async def process_plate(
    session, regnr, stelnr, plates_data, previous_plates, new_plates, semaphore
):
    if regnr in previous_plates:
        return

    async with semaphore:
        selskab, oprettet = await get_insurance_info(session, stelnr)

        try:
            dato_obj = datetime.strptime(oprettet, "%d-%m-%Y").date()
            today = datetime.now().date()
            yesterday = today - timedelta(days=1)

            if dato_obj not in (today, yesterday):
                return

            dato = dato_obj.strftime("%Y-%m-%d")
        except Exception:
            return

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
                return

        plates_data[selskab].append(entry)
        save_new_plate(regnr)
        new_plates.add(regnr)
        print(f"✅ Ny registrering: {regnr} | {selskab}")


# ---------------------------------
# Main
# ---------------------------------
if __name__ == "__main__":
    asyncio.run(check_new_registrations())
    deploy_site()
