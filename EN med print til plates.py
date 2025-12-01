import asyncio
import aiohttp
import os
import re
import json
import subprocess
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from plyer import notification

# Stier
FOUND_PLATES_FILE = "found_plates.txt"
JSON_FILE_PATH = r"C:\Users\Leander\Desktop\insurance-app\plates.json"

# Netlify / deploy
NETLIFY_CWD = r"C:\Users\Leander\Desktop"
DEPLOY_DIR = r"C:\Users\Leander\Desktop\insurance-app"

# Nummerplade-rækkevidde
start_regnr = "EN00000"
end_regnr = "EN99999"

# API'er
base_url = "https://www.nummerplade.net/nummerplade/"
insurance_url = "https://data1.nummerplade.net/dmr_forsikring.php?stelnr="

# Opdateret PROXY_LIST med gratis, arbejdende proxyer (pr. juli 2024)
PROXY_LIST = [
    "http://45.61.139.48:8000",        # USA - verificeret
    "http://194.31.162.81:8080",       # Tyskland - verificeret
    "http://185.199.229.156:7492",     # Canada - verificeret
    "http://38.154.241.146:999",       # USA - verificeret
    "http://8.213.128.6:8080",         # Holland - verificeret
    "http://103.169.255.171:8080",     # Indonesien - backup
    "http://103.175.237.123:3128",     # Indien - backup
]

# Tilføj denne funktion for at validerer proxyer
async def is_proxy_working(proxy):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://httpbin.org/ip",
                proxy=proxy,
                timeout=5
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    print(f"Proxy {proxy} virker - IP: {data.get('origin')}")
                    return True
    except Exception as e:
        print(f"Proxy {proxy} fejlede: {str(e)}")
        return False

# (Tidligere proxy-version af get_car_info – overskrives nedenfor af den rigtige version)
# async def get_car_info(session, regnr, semaphore):
#     url = f"{base_url}{regnr}.html"
#     headers = get_random_headers()
#     ...

# Maksimale forbindelser til API
MAX_CONNECTIONS = 40

def load_existing_data():
    """Indlæser eksisterende data fra JSON-filen"""
    try:
        with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_to_json(data):
    """Gemmer kombineret data i JSON-filen"""
    os.makedirs(os.path.dirname(JSON_FILE_PATH), exist_ok=True)
    with open(JSON_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4, sort_keys=True)

def load_previous_plates():
    """Indlæser tidligere fundne nummerplader fra fil"""
    if os.path.exists(FOUND_PLATES_FILE):
        with open(FOUND_PLATES_FILE, "r") as f:
            return set(f.read().splitlines())
    return set()

def save_new_plate(plate):
    """Gemmer ny fundet nummerplade i fil"""
    with open(FOUND_PLATES_FILE, "a") as f:
        f.write(f"{plate}\n")

def extract_last_change_date(html):
    """Udtrækker dato for sidste ændring fra HTML"""
    match = re.search(r'id="seneste_aendring">d\. (\d{2}-\d{2}-\d{4})', html)
    return datetime.strptime(match.group(1), "%d-%m-%Y").date() if match else None

def extract_stelnr(html):
    """Finder stelnummer i HTML"""
    for pattern in [
        r'var\s+search_data\s*=\s*"(\w+)"',
        r'stelnummer\s+(\w+)'
    ]:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return None

async def get_insurance_info(session, stelnr):
    """Henter forsikringsdata fra API"""
    url = f"{insurance_url}{stelnr.upper()}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": "https://nummerplade.net/",
        "X-Requested-With": "XMLHttpRequest"
    }

    try:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                if data.get("status_code") == "1":
                    car_data = data.get("carData", {})
                    return (
                        car_data.get("selskab", "Ukendt").strip(),
                        car_data.get("oprettet", "Ukendt")
                    )
            return "Ukendt", "Ukendt"
    except Exception as e:
        print(f"Fejl ved forsikringsinfo: {e}")
        return "Ukendt", "Ukendt"

async def get_car_info(session, regnr, semaphore):
    """Henter bilinformation"""
    url = f"{base_url}{regnr}.html"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    async with semaphore:
        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    html = await response.text()
                    if (last_change := extract_last_change_date(html)) and \
                       last_change >= datetime.now().date() - timedelta(days=1):
                        return regnr, extract_stelnr(html)
                elif response.status == 429:
                    await asyncio.sleep(int(response.headers.get('Retry-After', 5)))
                    return await get_car_info(session, regnr, semaphore)
                return None, None
        except Exception as e:
            print(f"Fejl ved {regnr}: {e}")
            return None, None

async def check_new_registrations():
    """Hovedprocessen"""
    plates_data = load_existing_data()
    previous_plates = load_previous_plates()
    new_plates = set()

    regnr_int = int(start_regnr[2:])
    end_regnr_int = int(end_regnr[2:])

    connector = aiohttp.TCPConnector(limit=MAX_CONNECTIONS)
    semaphore = asyncio.Semaphore(MAX_CONNECTIONS)

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = (get_car_info(session, f"EN{num:05d}", semaphore) 
                 for num in range(regnr_int, end_regnr_int + 1))

        for future in asyncio.as_completed(tasks):
            regnr, stelnr = await future

            if regnr and regnr not in previous_plates:
                print(f"Ny registrering: {regnr}")
                save_new_plate(regnr)
                new_plates.add(regnr)

                if stelnr:
                    selskab, oprettet = await get_insurance_info(session, stelnr)
                    
                    # Tjek om datoen er i dag eller i går
                    try:
                        dato_obj = datetime.strptime(oprettet, "%d-%m-%Y").date()
                        today = datetime.now().date()
                        yesterday = today - timedelta(days=1)
                        
                        if dato_obj not in (today, yesterday):
                            continue  # Spring over hvis ikke i dag/i går
                            
                        dato = dato_obj.strftime("%Y-%m-%d")
                    except:
                        continue  # Spring over ugyldige datoer

                    # Opret entry med standardværdier
                    entry = {
                        "date": dato,
                        "plate": regnr,
                        "checked": False,
                        "premium": 0
                    }
                    
                    # Tjek for eksisterende plader i kategorien
                    if selskab not in plates_data:
                        plates_data[selskab] = []
                    else:
                        existing_plates = {p["plate"] for p in plates_data[selskab]}
                        if regnr in existing_plates:
                            continue  # Spring over dubletter

                    plates_data[selskab].append(entry)

    if new_plates:
        save_to_json(plates_data)
        notification.notify(
            title="Nye Nummerplader",
            message=f"Fundet {len(new_plates)} nye plader",
            timeout=10
        )

# -----------------------------
#   Netlify deploy direkte fra Python
# -----------------------------
def deploy_site():
    print("\n🚀 Udfører Netlify deploy for EN-scriptet...")

    cmd = f'npx netlify deploy --dir="{DEPLOY_DIR}" --prod'

    result = subprocess.run(
        cmd,
        shell=True,
        cwd=NETLIFY_CWD,   # Samme mappe som når du deployer manuelt
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
#   MAIN
# -----------------------------
if __name__ == "__main__":
    asyncio.run(check_new_registrations())
    deploy_site()
