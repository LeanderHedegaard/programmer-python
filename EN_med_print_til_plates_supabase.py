import asyncio
import aiohttp
import os
import json
import random
import re
import requests

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup


# ============================================================
# KONFIGURATION
# ============================================================

REPO_ROOT = Path(__file__).resolve().parent

JSON_FILE_PATH = Path(
    os.getenv(
        "JSON_FILE_PATH",
        REPO_ROOT / "public" / "plates" / "plates.json",
    )
)

JSON_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv(
    "SUPABASE_SERVICE_ROLE_KEY",
    "",
)

# Valgfrit GitHub Secret med cookies fra nummerplade.net.
#
# Secret-navn:
# NUMMERPLADE_COOKIES_JSON
#
# Eksempel:
# {
#   "PHPSESSID": "...",
#   "_ga": "...",
#   "_fbp": "..."
# }
try:
    NUMMERPLADE_COOKIES = json.loads(
        os.getenv("NUMMERPLADE_COOKIES_JSON", "") or "{}"
    )
except json.JSONDecodeError:
    print(
        "⚠️ NUMMERPLADE_COOKIES_JSON er ikke gyldig JSON. "
        "Fortsætter uden cookies."
    )
    NUMMERPLADE_COOKIES = {}


PREFIX = "EV"

# Kun EV10000 til EV99999
START_NUMBER = int(os.getenv("START_NUMBER", "10000"))
END_NUMBER = int(os.getenv("END_NUMBER", "99999"))

BASE_URL = "https://www.nummerplade.net/nummerplade"

# GitHub bliver lettere rate-limited end din egen computer.
# Start forsigtigt. Kan hæves via GitHub env.
MAX_CONNECTIONS = int(os.getenv("MAX_CONNECTIONS", "10"))

# Hvor mange plader behandles ad gangen.
SCAN_BATCH_SIZE = int(os.getenv("SCAN_BATCH_SIZE", "500"))

# Hvor mange Supabase-rækker sendes i én request.
SUPABASE_BATCH_SIZE = int(
    os.getenv("SUPABASE_BATCH_SIZE", "100")
)

MAX_RETRIES = int(os.getenv("MAX_RETRIES", "2"))

REQUEST_TIMEOUT_SECONDS = int(
    os.getenv("REQUEST_TIMEOUT_SECONDS", "20")
)

COPENHAGEN = ZoneInfo("Europe/Copenhagen")

REQUEST_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "da-DK,da;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.nummerplade.net/",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
}


# ============================================================
# HJÆLPEFUNKTIONER
# ============================================================

def parse_danish_date(value):
    """
    Konverterer eksempelvis 31-05-2026 til datetime.date.

    Returnerer None, hvis værdien mangler eller ikke kan læses.
    """
    if not value:
        return None

    value = value.strip()

    for date_format in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(
                value,
                date_format,
            ).date()
        except ValueError:
            continue

    return None


def normalize_company(value):
    if not value:
        return "Ukendt"

    return " ".join(value.split()).strip()


def chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


# ============================================================
# LOKAL JSON-BACKUP
# ============================================================

def load_existing_data():
    try:
        with open(
            JSON_FILE_PATH,
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

            if isinstance(data, dict):
                return data

            return {}

    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_to_json(data):
    JSON_FILE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(
        JSON_FILE_PATH,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=4,
            sort_keys=True,
        )


# ============================================================
# SUPABASE
# ============================================================

def supabase_headers(prefer=None):
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": (
            f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"
        ),
        "Content-Type": "application/json",
    }

    if prefer:
        headers["Prefer"] = prefer

    return headers


def delete_old_plates_from_supabase():
    """
    Beholder i dag og de to foregående kalenderdatoer.

    Hvis i dag fx er 30-07-2026, slettes datoer før 28-07-2026.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print(
            "⚠️ Mangler SUPABASE_URL eller "
            "SUPABASE_SERVICE_ROLE_KEY. "
            "Springer oprydning over."
        )
        return False

    cutoff_date = (
        datetime.now(COPENHAGEN).date()
        - timedelta(days=2)
    ).isoformat()

    url = (
        f"{SUPABASE_URL}/rest/v1/plates"
        f"?date=lt.{cutoff_date}"
    )

    try:
        response = requests.delete(
            url,
            headers=supabase_headers(),
            timeout=30,
        )

        if response.status_code not in (200, 204):
            print(
                "❌ Supabase-oprydning fejlede: "
                f"{response.status_code} {response.text}"
            )
            return False

        print(
            "🧹 Supabase-oprydning gennemført. "
            f"Rækker med date før {cutoff_date} er slettet."
        )
        return True

    except requests.RequestException as error:
        print(
            f"❌ Netværksfejl ved Supabase-oprydning: {error}"
        )
        return False


def upload_entries_to_supabase(entries):
    """
    Sender flere rækker i samme Supabase-request.

    Eksisterende kombinationer af company + plate ignoreres.
    """
    if not entries:
        return 0

    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print(
            "⚠️ Mangler SUPABASE_URL eller "
            "SUPABASE_SERVICE_ROLE_KEY."
        )
        return 0

    url = (
        f"{SUPABASE_URL}/rest/v1/plates"
        "?on_conflict=company,plate"
    )

    accepted = 0

    for batch in chunks(entries, SUPABASE_BATCH_SIZE):
        try:
            response = requests.post(
                url,
                headers=supabase_headers(
                    "resolution=ignore-duplicates,"
                    "return=minimal"
                ),
                json=batch,
                timeout=30,
            )

            if response.status_code in (200, 201, 204):
                accepted += len(batch)

                print(
                    "✅ Supabase accepterede/ignorerede "
                    f"batch med {len(batch)} plader."
                )
                continue

            if response.status_code == 409:
                # Bør normalt ikke ske med on_conflict +
                # resolution=ignore-duplicates.
                print(
                    "ℹ️ Supabase rapporterede dubletter "
                    "for en batch."
                )
                accepted += len(batch)
                continue

            print(
                "❌ Supabase batch-upload fejlede: "
                f"{response.status_code} {response.text}"
            )

        except requests.RequestException as error:
            print(
                f"❌ Netværksfejl ved Supabase-upload: {error}"
            )

    return accepted


# ============================================================
# NY HTML-STRUKTUR
# ============================================================

def extract_first_registration_date(soup):
    """
    Finder:

    <span>1. registrering</span>
    <b>31-05-2026</b>
    """
    for label in soup.find_all("span"):
        label_text = " ".join(
            label.get_text(" ", strip=True).split()
        ).lower()

        if label_text == "1. registrering":
            parent = label.parent

            if parent:
                value = parent.find("b")

                if value:
                    return parse_danish_date(
                        value.get_text(
                            " ",
                            strip=True,
                        )
                    )

    return None


def extract_current_insurance(soup):
    """
    Førstevalg: aktiv række i forsikringshistorikken:

    <div class="fors-row aktuel">
      <span class="fdato">31-05-2026</span>
      <b>IF SKADEFORSIKRING</b>
    </div>

    Fallback: #kpi-fors-val.
    """
    current_row = soup.select_one(
        ".fors-row.aktuel"
    )

    if current_row:
        date_element = current_row.select_one(
            ".fdato"
        )
        company_element = current_row.find("b")

        company = normalize_company(
            company_element.get_text(
                " ",
                strip=True,
            )
            if company_element
            else ""
        )

        insurance_date = parse_danish_date(
            date_element.get_text(
                " ",
                strip=True,
            )
            if date_element
            else ""
        )

        if company != "Ukendt":
            return company, insurance_date

    company_element = soup.select_one(
        "#kpi-fors-val"
    )

    if company_element:
        company = normalize_company(
            company_element.get_text(
                " ",
                strip=True,
            )
        )

        if company != "Ukendt":
            return company, None

    return "Ukendt", None


def extract_vehicle_data(html, expected_plate):
    """
    Udlæser den nye side.

    Returnerer None, hvis siden ikke ser ud til at være
    den ønskede nummerpladeside.
    """
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    plate_element = soup.select_one(
        ".dny-plade"
    )

    if plate_element:
        plate = plate_element.get_text(
            " ",
            strip=True,
        ).upper()
    else:
        plate = ""

    # Fallback til sidens title.
    if not plate:
        title = (
            soup.title.get_text(
                " ",
                strip=True,
            )
            if soup.title
            else ""
        )

        title_match = re.match(
            r"^([A-Z]{2}\d{5})\b",
            title.upper(),
        )

        if title_match:
            plate = title_match.group(1)

    if plate != expected_plate.upper():
        return None

    vin = None

    vin_element = soup.select_one(
        ".dny-stelnr[data-v]"
    )

    if vin_element:
        vin = (
            vin_element.get("data-v", "")
            .strip()
            .upper()
        )

    # Fallback til meta description.
    if not vin:
        description = soup.find(
            "meta",
            attrs={"name": "description"},
        )

        description_text = (
            description.get("content", "")
            if description
            else ""
        )

        vin_match = re.search(
            r"stelnummer\s+([A-HJ-NPR-Z0-9]{17})",
            description_text,
            re.IGNORECASE,
        )

        if vin_match:
            vin = vin_match.group(1).upper()

    first_registration_date = (
        extract_first_registration_date(soup)
    )

    company, insurance_date = (
        extract_current_insurance(soup)
    )

    return {
        "plate": plate,
        "vin": vin,
        "first_registration_date": (
            first_registration_date
        ),
        "insurance_company": company,
        "insurance_date": insurance_date,
    }


# ============================================================
# HTTP-OPSLAG
# ============================================================

async def get_car_info(
    session,
    regnr,
    semaphore,
):
    url = (
        f"{BASE_URL}/{regnr.lower()}.html"
    )

    async with semaphore:
        for attempt in range(
            1,
            MAX_RETRIES + 2,
        ):
            try:
                timeout = aiohttp.ClientTimeout(
                    total=REQUEST_TIMEOUT_SECONDS
                )

                async with session.get(
                    url,
                    timeout=timeout,
                    allow_redirects=True,
                ) as response:

                    if response.status == 404:
                        return None

                    if response.status in (
                        403,
                        429,
                        500,
                        502,
                        503,
                        504,
                    ):
                        if attempt > MAX_RETRIES:
                            print(
                                f"⚠️ {regnr}: HTTP "
                                f"{response.status} efter "
                                f"{attempt} forsøg."
                            )
                            return None

                        retry_after = (
                            response.headers.get(
                                "Retry-After"
                            )
                        )

                        try:
                            wait_seconds = float(
                                retry_after
                            )
                        except (
                            TypeError,
                            ValueError,
                        ):
                            wait_seconds = (
                                2 ** attempt
                                + random.uniform(
                                    0.2,
                                    1.0,
                                )
                            )

                        await asyncio.sleep(
                            wait_seconds
                        )
                        continue

                    if response.status != 200:
                        return None

                    html = await response.text(
                        errors="ignore"
                    )

                    return extract_vehicle_data(
                        html,
                        regnr,
                    )

            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
            ) as error:
                if attempt > MAX_RETRIES:
                    print(
                        f"⚠️ {regnr}: netværksfejl "
                        f"efter {attempt} forsøg: {error}"
                    )
                    return None

                await asyncio.sleep(
                    2 ** attempt
                    + random.uniform(
                        0.2,
                        1.0,
                    )
                )

    return None


# ============================================================
# BEHANDLING
# ============================================================

def choose_entry_date(vehicle):
    """
    En plade medtages, hvis mindst én af følgende er
    i dag eller i går:

    - første registreringsdato
    - datoen for den aktive forsikring
    """
    today = datetime.now(
        COPENHAGEN
    ).date()

    yesterday = today - timedelta(
        days=1
    )

    recent_dates = {
        today,
        yesterday,
    }

    insurance_date = vehicle.get(
        "insurance_date"
    )

    registration_date = vehicle.get(
        "first_registration_date"
    )

    if insurance_date in recent_dates:
        return insurance_date

    if registration_date in recent_dates:
        return registration_date

    return None


def add_to_local_backup(
    plates_data,
    company,
    entry,
):
    if company not in plates_data:
        plates_data[company] = []

    existing = {
        item.get("plate")
        for item in plates_data[company]
    }

    if entry["plate"] not in existing:
        plates_data[company].append(
            entry
        )


async def scan_batch(
    session,
    semaphore,
    start_number,
    end_number,
):
    tasks = [
        get_car_info(
            session,
            f"{PREFIX}{number:05d}",
            semaphore,
        )
        for number in range(
            start_number,
            end_number + 1,
        )
    ]

    return await asyncio.gather(
        *tasks
    )


async def check_new_registrations():
    print(
        f"Starter scanning af {PREFIX}"
        f"{START_NUMBER:05d}–"
        f"{PREFIX}{END_NUMBER:05d}."
    )

    plates_data = load_existing_data()
    supabase_entries = []

    found_pages = 0
    recent_plates = 0
    missing_company = 0

    connector = aiohttp.TCPConnector(
        limit=MAX_CONNECTIONS,
        ttl_dns_cache=300,
    )

    semaphore = asyncio.Semaphore(
        MAX_CONNECTIONS
    )

    async with aiohttp.ClientSession(
        connector=connector,
        headers=REQUEST_HEADERS,
        cookies=NUMMERPLADE_COOKIES,
    ) as session:

        for batch_start in range(
            START_NUMBER,
            END_NUMBER + 1,
            SCAN_BATCH_SIZE,
        ):
            batch_end = min(
                batch_start
                + SCAN_BATCH_SIZE
                - 1,
                END_NUMBER,
            )

            print(
                f"🔎 Scanner {PREFIX}"
                f"{batch_start:05d}–"
                f"{PREFIX}{batch_end:05d}"
            )

            results = await scan_batch(
                session,
                semaphore,
                batch_start,
                batch_end,
            )

            for vehicle in results:
                if not vehicle:
                    continue

                found_pages += 1

                entry_date = choose_entry_date(
                    vehicle
                )

                if not entry_date:
                    continue

                company = vehicle.get(
                    "insurance_company",
                    "Ukendt",
                )

                if company == "Ukendt":
                    missing_company += 1
                    print(
                        "⚠️ Sen plade uden "
                        "forsikringsselskab: "
                        f"{vehicle['plate']}"
                    )
                    continue

                entry = {
                    "company": company,
                    "plate": vehicle["plate"],
                    "date": entry_date.isoformat(),
                    "checked": False,
                    "premium": 0,
                    "note": "",
                }

                supabase_entries.append(
                    entry
                )

                add_to_local_backup(
                    plates_data,
                    company,
                    {
                        "plate": entry["plate"],
                        "date": entry["date"],
                        "checked": False,
                        "premium": 0,
                        "note": "",
                    },
                )

                recent_plates += 1

                print(
                    "✅ Relevant plade: "
                    f"{entry['plate']} | "
                    f"{company} | "
                    f"{entry['date']}"
                )

            # Lille pause mellem batches.
            # Reducerer risikoen for Cloudflare/rate-limit.
            await asyncio.sleep(
                random.uniform(
                    0.1,
                    0.4,
                )
            )

    # Fjern eventuelle dubletter fundet under samme run.
    unique_entries = {}

    for entry in supabase_entries:
        key = (
            entry["company"],
            entry["plate"],
        )
        unique_entries[key] = entry

    final_entries = list(
        unique_entries.values()
    )

    uploaded = upload_entries_to_supabase(
        final_entries
    )

    if final_entries:
        save_to_json(
            plates_data
        )

    print("")
    print("========== RESULTAT ==========")
    print(
        f"Gyldige køretøjssider fundet: {found_pages}"
    )
    print(
        f"Relevante plader fra i dag/i går: "
        f"{recent_plates}"
    )
    print(
        f"Relevante plader uden selskab: "
        f"{missing_company}"
    )
    print(
        f"Sendt til Supabase: {uploaded}"
    )
    print("==============================")


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    print(f"{PREFIX}-script startet.")

    delete_old_plates_from_supabase()

    asyncio.run(
        check_new_registrations()
    )

    print(f"{PREFIX}-script færdigt.")
