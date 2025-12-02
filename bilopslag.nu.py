import asyncio
import aiohttp
import os
import re
import json
import requests
import subprocess
from datetime import datetime, timedelta

# ---------------------------------
# Environment-detektion
# ---------------------------------
RUNNING_IN_GITHUB = os.getenv("GITHUB_ACTIONS") == "true"

# ---------------------------------
# Notifikation (kun lokalt)
# ---------------------------------
if RUNNING_IN_GITHUB:
    # I GitHub har vi ikke et desktop-miljø → brug dummy
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
# FIL-STIER
# ---------------------------------
FOUND_PLATES_FILE = "found_plates.txt"

# GitHub: brug repo-root; Lokalt: fuld sti til insurance-app
if RUNNING_IN_GITHUB:
    JSON_FILE_PATH = "plates.json"
else:
    JSON_FILE_PATH = r"C:\Users\Leander\Desktop\insurance-app\plates.json"


# ---------------------------------
# API-INFO
# ---------------------------------
insurance_url = "https://data1.nummerplade.net/dmr_forsikring.php?stelnr="
MAX_CONNECTIONS = 20

BILOPSLAG_HEADERS = {
    "accept": "*/*",
    "accept-language": "da,en-US;q=0.9,en;q=0.8",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "referer": "https://bilopslag.nu/avanceret-soegning",
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
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

BILOPSLAG_COOKIES = {}  # ikke nødvendige


PLADE_REGEX = r"^[A-Z]{2}\d{3,5}$"


# ---------------------------------
# DEPLOY-HÅNDTERING
# ---------------------------------
if RUNNING_IN_GITHUB:

    def deploy_site():
        # Deploy håndteres af GitHub Actions workflowet, ikke her
        print("⏭️ Skipper Netlify deploy (GitHub Actions håndterer dette).")

else:
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
# FIL-HÅNDTERING
# ---------------------------------
def load_existing_data():
    try:
        with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_to_json(data):
    """Gemmer JSON både lokalt og i GitHub Actions."""
    dir_name = os.path.dirname(JSON_FILE_PATH)

    # Kun opret mappe hvis der faktisk er en mappe-del (lokalt)
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
# HENT PLADER
# ---------------------------------
def hent_plaader_fra_bilopslag():
    i_dag = datetime.now().date()
    i_gar = i_dag - timedelta(days=1)
    i_gar_str = i_gar.strftime("%Y-%m-%d")

    base_url = (
        "https://bilopslag.nu/api/advanced_search"
        "?registration_matches=%25%25%25%25%25"
        "&first_registration_date_gteq={dato}"
        "&page={side}"
    )

    plader = []

    for page in range(1, 8):
        url = base_url.format(dato=i_gar_str, side=page)
        print(f"\n🔎 Henter side {page}: {url}")

        try:
            r = requests.get(url, headers=BILOPSLAG_HEADERS, timeout=10)
            data = r.json()
            biler = data.get("data", [])

            if not biler:
                break

            for bil in biler:
                reg = bil.get("registration", "").upper()
                vin = bil.get("vin", "").upper()

                if re.match(PLADE_REGEX, reg) and vin:
                    plader.append((reg, vin))
                    print(f" - {reg} | {vin}")

        except Exception as e:
            print(f"Fejl på side {page}: {e}")
            break

    print(f"\n🎯 I alt fundet: {len(plader)} plader")
    return plader


# ---------------------------------
# HENT FORSIKRINGSINFO
# ---------------------------------
async def get_insurance_info(session, stelnr):
    url = f"{insurance_url}{stelnr}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }

    try:
        async with session.get(url, headers=headers) as r:
            if r.status == 200:
                data = await r.json()
                if data.get("status_code") == "1":
                    d = data["carData"]
                    return d.get("selskab", "Ukendt"), d.get("oprettet", "Ukendt")
    except Exception as e:
        print(f"Fejl ved forsikringsopslag: {e}")

    return "Ukendt", "Ukendt"


# ---------------------------------
# PROCESSÉR EN PLADE
# ---------------------------------
async def process_plate(session, regnr, stelnr, plates_data, previous, new_set, sem):
    if regnr in previous:
        return

    async with sem:
        selskab, oprettet = await get_insurance_info(session, stelnr)

    try:
        dato_obj = datetime.strptime(oprettet, "%d-%m-%Y").date()
    except Exception:
        return

    today = datetime.now().date()
    yesterday = today - timedelta(days=1)

    if dato_obj not in (today, yesterday):
        return

    dato = dato_obj.strftime("%Y-%m-%d")

    if selskab not in plates_data:
        plates_data[selskab] = []

    existing = {p["plate"] for p in plates_data[selskab]}
    if regnr in existing:
        return

    entry = {
        "date": dato,
        "plate": regnr,
        "checked": False,
        "premium": 0,
    }

    plates_data[selskab].append(entry)
    save_new_plate(regnr)
    new_set.add(regnr)

    print(f"✅ Ny registrering: {regnr} | {selskab}")


# ---------------------------------
# HOVEDPROGRAM
# ---------------------------------
async def check_new_registrations():
    plates_data = load_existing_data()
    previous = load_previous_plates()
    new_plates = set()

    plader_og_stel = hent_plaader_fra_bilopslag()
    if not plader_og_stel:
        print("Ingen biler fundet, afslutter.")
        return

    connector = aiohttp.TCPConnector(limit=MAX_CONNECTIONS)
    sem = asyncio.Semaphore(MAX_CONNECTIONS)

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            process_plate(session, r, v, plates_data, previous, new_plates, sem)
            for r, v in plader_og_stel
        ]
        await asyncio.gather(*tasks)

    if new_plates:
        save_to_json(plates_data)
        notification.notify(
            title="Nye nummerplader fundet",
            message=f"Fundet {len(new_plates)} nye plader",
            timeout=10,
        )


# ---------------------------------
# MAIN
# ---------------------------------
if __name__ == "__main__":
    asyncio.run(check_new_registrations())
    deploy_site()
