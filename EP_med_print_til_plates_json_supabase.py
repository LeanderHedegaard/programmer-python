import asyncio
import aiohttp
import os
import json
import html
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

JSON_FILE_PATH.parent.mkdir(
    parents=True,
    exist_ok=True,
)

SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    "",
).rstrip("/")

SUPABASE_SERVICE_ROLE_KEY = os.getenv(
    "SUPABASE_SERVICE_ROLE_KEY",
    "",
)


# ============================================================
# BILOPSLAG COOKIES
# ============================================================

try:
    BILOPSLAG_COOKIES = json.loads(
        os.getenv(
            "BILOPSLAG_COOKIES_JSON",
            "",
        ) or "{}"
    )

except json.JSONDecodeError:

    print(
        "⚠️ BILOPSLAG_COOKIES_JSON "
        "er ikke gyldig JSON."
    )

    BILOPSLAG_COOKIES = {}


# ============================================================
# NUMMERPLADER
# ============================================================

PREFIX = "EW"

START_NUMBER = int(
    os.getenv(
        "START_NUMBER",
        "10000",
    )
)

END_NUMBER = int(
    os.getenv(
        "END_NUMBER",
        "99999",
    )
)


# ============================================================
# BILOPSLAG
# ============================================================

BILOPSLAG_BASE_URL = (
    "https://bilopslag.nu"
)

MAX_CONNECTIONS = int(
    os.getenv(
        "MAX_CONNECTIONS",
        "8",
    )
)

SCAN_BATCH_SIZE = int(
    os.getenv(
        "SCAN_BATCH_SIZE",
        "250",
    )
)

REQUEST_TIMEOUT = int(
    os.getenv(
        "REQUEST_TIMEOUT",
        "25",
    )
)

COPENHAGEN = ZoneInfo(
    "Europe/Copenhagen"
)


# ============================================================
# HEADERS
# ============================================================

BILOPSLAG_HEADERS = {

    "accept": (
        "text/html,"
        "application/xhtml+xml,"
        "application/xml;q=0.9,"
        "image/avif,"
        "image/webp,"
        "image/apng,"
        "*/*;q=0.8"
    ),

    "accept-language": (
        "da-DK,da;q=0.9,"
        "en-US;q=0.8,"
        "en;q=0.7"
    ),

    "cache-control": "no-cache",

    "pragma": "no-cache",

    "referer": (
        "https://bilopslag.nu/"
    ),

    "upgrade-insecure-requests": "1",

    "user-agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/151.0.0.0 "
        "Safari/537.36"
    ),
}


# ============================================================
# HJÆLPEFUNKTIONER
# ============================================================

def parse_date(value):

    if not value:
        return None

    value = str(value).strip()

    formats = (
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d.%m.%Y",
    )

    for date_format in formats:

        try:

            return datetime.strptime(
                value,
                date_format,
            ).date()

        except ValueError:

            continue

    return None


def clean_company(value):

    if not value:
        return "Ukendt"

    return " ".join(
        str(value).split()
    ).strip()


# ============================================================
# LOKAL JSON
# ============================================================

def load_existing_data():

    try:

        with open(
            JSON_FILE_PATH,
            "r",
            encoding="utf-8",
        ) as file:

            data = json.load(file)

            if isinstance(
                data,
                dict,
            ):

                return data

            return {}

    except (
        FileNotFoundError,
        json.JSONDecodeError,
    ):

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

def supabase_headers(
    prefer=None,
):

    headers = {

        "apikey":
            SUPABASE_SERVICE_ROLE_KEY,

        "Authorization":
            f"Bearer "
            f"{SUPABASE_SERVICE_ROLE_KEY}",

        "Content-Type":
            "application/json",
    }

    if prefer:

        headers[
            "Prefer"
        ] = prefer

    return headers


# ============================================================
# SLET GAMLE PLADER
# ============================================================

def delete_old_plates_from_supabase():

    if (
        not SUPABASE_URL
        or
        not SUPABASE_SERVICE_ROLE_KEY
    ):

        print(
            "⚠️ Mangler Supabase "
            "credentials."
        )

        return False

    cutoff_date = (
        datetime.now(
            COPENHAGEN
        ).date()
        -
        timedelta(
            days=2
        )
    ).isoformat()

    url = (
        f"{SUPABASE_URL}"
        f"/rest/v1/plates"
        f"?date=lt.{cutoff_date}"
    )

    try:

        response = requests.delete(
            url,
            headers=supabase_headers(),
            timeout=30,
        )

        if response.status_code not in (
            200,
            204,
        ):

            print(
                "❌ Oprydning fejlede: "
                f"{response.status_code} "
                f"{response.text}"
            )

            return False

        print(
            "🧹 Plader før "
            f"{cutoff_date} "
            "er slettet."
        )

        return True

    except Exception as error:

        print(
            "❌ Supabase "
            f"oprydningsfejl: {error}"
        )

        return False


# ============================================================
# UPLOAD ÉN PLADE
# ============================================================

def upload_plate_to_supabase(
    company,
    entry,
):

    if (
        not SUPABASE_URL
        or
        not SUPABASE_SERVICE_ROLE_KEY
    ):

        print(
            "⚠️ Mangler Supabase "
            "credentials."
        )

        return False

    url = (
        f"{SUPABASE_URL}"
        "/rest/v1/plates"
        "?on_conflict=company,plate"
    )

    payload = {

        "company":
            company,

        "plate":
            entry["plate"],

        "date":
            entry["date"],

        "checked":
            entry.get(
                "checked",
                False,
            ),

        "premium":
            entry.get(
                "premium",
                0,
            ),

        "note":
            entry.get(
                "note",
                "",
            ),
    }

    headers = supabase_headers(
        "resolution=ignore-duplicates,"
        "return=minimal"
    )

    try:

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=20,
        )

        if response.status_code in (
            200,
            201,
            204,
        ):

            return True

        if response.status_code == 409:

            return True

        print(
            "❌ Supabase fejl "
            f"{entry['plate']}: "
            f"{response.status_code} "
            f"{response.text}"
        )

        return False

    except Exception as error:

        print(
            "❌ Supabase fejl "
            f"{entry['plate']}: "
            f"{error}"
        )

        return False


# ============================================================
# FIND VEHICLE-DATA I BILOPSLAG HTML
# ============================================================

def extract_vehicle_data_from_html(
    page_html,
    expected_plate,
):

    soup = BeautifulSoup(
        page_html,
        "html.parser",
    )

    vehicle_element = soup.select_one(
        "[data-vehicle]"
    )

    if not vehicle_element:

        return None

    raw_vehicle = vehicle_element.get(
        "data-vehicle"
    )

    if not raw_vehicle:

        return None

    try:

        # BeautifulSoup decoder normalt allerede HTML entities,
        # men html.unescape gør funktionen robust.
        raw_vehicle = html.unescape(
            raw_vehicle
        )

        vehicle = json.loads(
            raw_vehicle
        )

    except Exception as error:

        print(
            f"⚠️ Kunne ikke læse "
            f"vehicle JSON for "
            f"{expected_plate}: {error}"
        )

        return None

    registration = str(
        vehicle.get(
            "registration",
            "",
        )
    ).upper().strip()

    if (
        registration
        !=
        expected_plate.upper()
    ):

        return None

    return vehicle


# ============================================================
# HENT BIL FRA BILOPSLAG
# ============================================================

async def get_vehicle(
    session,
    regnr,
    semaphore,
):

    url = (
        f"{BILOPSLAG_BASE_URL}"
        f"/nummerplade/"
        f"{regnr.upper()}"
    )

    async with semaphore:

        try:

            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(
                    total=REQUEST_TIMEOUT
                ),
                allow_redirects=True,
            ) as response:

                # Pladen findes ikke
                if response.status == 404:

                    return None

                if response.status == 403:

                    print(
                        f"⛔ {regnr}: "
                        "Bilopslag gav HTTP 403."
                    )

                    return {
                        "blocked": True,
                        "registration": regnr,
                    }

                if response.status == 429:

                    print(
                        f"⚠️ {regnr}: "
                        "Bilopslag rate-limit "
                        "(HTTP 429)."
                    )

                    return None

                if response.status != 200:

                    return None

                page_html = (
                    await response.text(
                        errors="ignore"
                    )
                )

                return (
                    extract_vehicle_data_from_html(
                        page_html,
                        regnr,
                    )
                )

        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as error:

            print(
                f"⚠️ {regnr}: "
                f"{error}"
            )

            return None


# ============================================================
# HENT DMR/FORSIKRING FRA BILOPSLAG
# ============================================================

async def get_insurance_info(
    session,
    vehicle_id,
):

    url = (
        f"{BILOPSLAG_BASE_URL}"
        f"/api/statistics/vehicles/"
        f"{vehicle_id}/dmr"
    )

    try:

        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(
                total=REQUEST_TIMEOUT
            ),
            headers={
                "Accept": "application/json",
                "X-Requested-With":
                    "XMLHttpRequest",
            },
        ) as response:

            if response.status == 403:

                return (
                    "Ukendt",
                    None,
                    "403",
                )

            if response.status == 429:

                return (
                    "Ukendt",
                    None,
                    "429",
                )

            if response.status != 200:

                return (
                    "Ukendt",
                    None,
                    str(
                        response.status
                    ),
                )

            data = await response.json(
                content_type=None
            )

            dmr_data = (
                data.get(
                    "dmr_data",
                    {},
                )
                or {}
            )

            company = clean_company(
                dmr_data.get(
                    "insurance_company"
                )
            )

            status = str(
                dmr_data.get(
                    "insurance_status",
                    "",
                )
            ).strip()

            created_at = parse_date(
                dmr_data.get(
                    "insurance_created_at"
                )
            )

            return (
                company,
                created_at,
                status,
            )

    except Exception as error:

        print(
            "⚠️ Forsikringsopslag "
            f"fejlede for vehicle "
            f"{vehicle_id}: {error}"
        )

        return (
            "Ukendt",
            None,
            "Fejl",
        )


# ============================================================
# PROCESS ÉN NUMMERPLADE
# ============================================================

async def process_plate(
    session,
    regnr,
    plates_data,
    processed_plates,
    semaphore,
):

    vehicle = await get_vehicle(
        session,
        regnr,
        semaphore,
    )

    if not vehicle:

        return

    if vehicle.get(
        "blocked"
    ):

        return "blocked"


    # ========================================================
    # VEHICLE ID
    # ========================================================

    vehicle_id = vehicle.get(
        "id"
    )

    if not vehicle_id:

        print(
            f"⚠️ {regnr}: "
            "intet vehicle ID."
        )

        return


    # ========================================================
    # FØRSTE REGISTRERING
    # ========================================================

    registration_date = parse_date(
        vehicle.get(
            "first_registration_date"
        )
    )


    # ========================================================
    # FORSIKRING
    # ========================================================

    (
        company,
        insurance_date,
        insurance_status,
    ) = await get_insurance_info(
        session,
        vehicle_id,
    )


    if (
        not company
        or
        company == "Ukendt"
    ):

        print(
            f"⚠️ {regnr}: "
            "intet forsikringsselskab."
        )

        return


    # ========================================================
    # KUN AKTIV FORSIKRING
    # ========================================================

    if (
        insurance_status
        and
        insurance_status.lower()
        != "aktiv"
    ):

        print(
            f"ℹ️ {regnr}: "
            "forsikring er ikke aktiv "
            f"({insurance_status})."
        )

        return


    # ========================================================
    # DATO
    # ========================================================

    today = datetime.now(
        COPENHAGEN
    ).date()

    yesterday = (
        today
        -
        timedelta(
            days=1
        )
    )

    valid_dates = {
        today,
        yesterday,
    }


    # Forsikringsdato foretrækkes.
    if (
        insurance_date
        in valid_dates
    ):

        entry_date = (
            insurance_date
        )

    elif (
        registration_date
        in valid_dates
    ):

        entry_date = (
            registration_date
        )

    else:

        return


    # ========================================================
    # ENTRY
    # ========================================================

    entry = {

        "date":
            entry_date.isoformat(),

        "plate":
            regnr,

        "checked":
            False,

        "premium":
            0,

        "note":
            "",
    }


    # ========================================================
    # LOKAL JSON
    # ========================================================

    if company not in plates_data:

        plates_data[
            company
        ] = []

    existing = {

        plate.get(
            "plate"
        )

        for plate
        in plates_data[
            company
        ]
    }

    if regnr not in existing:

        plates_data[
            company
        ].append(
            entry
        )


    # ========================================================
    # SUPABASE
    # ========================================================

    ok = upload_plate_to_supabase(
        company,
        entry,
    )

    if ok:

        processed_plates.add(
            regnr
        )

        print(
            "✅ "
            f"{regnr} | "
            f"{company} | "
            f"{entry_date} | "
            f"{insurance_status}"
        )


# ============================================================
# SCAN BATCH
# ============================================================

async def scan_batch(
    session,
    semaphore,
    plates_data,
    processed_plates,
    start_number,
    end_number,
):

    tasks = [

        process_plate(
            session,
            f"{PREFIX}{number:05d}",
            plates_data,
            processed_plates,
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


# ============================================================
# HOVEDPROGRAM
# ============================================================

async def check_new_registrations():

    print(
        f"Starter Bilopslag-scanning: "
        f"{PREFIX}{START_NUMBER:05d}"
        "–"
        f"{PREFIX}{END_NUMBER:05d}"
    )

    plates_data = (
        load_existing_data()
    )

    processed_plates = set()

    connector = (
        aiohttp.TCPConnector(
            limit=MAX_CONNECTIONS,
            ttl_dns_cache=300,
        )
    )

    semaphore = (
        asyncio.Semaphore(
            MAX_CONNECTIONS
        )
    )


    async with aiohttp.ClientSession(
        connector=connector,
        headers=BILOPSLAG_HEADERS,
        cookies=BILOPSLAG_COOKIES,
    ) as session:


        for batch_start in range(
            START_NUMBER,
            END_NUMBER + 1,
            SCAN_BATCH_SIZE,
        ):

            batch_end = min(
                batch_start
                +
                SCAN_BATCH_SIZE
                -
                1,
                END_NUMBER,
            )

            print(
                f"🔎 Scanner "
                f"{PREFIX}{batch_start:05d}"
                "–"
                f"{PREFIX}{batch_end:05d}"
            )


            results = await scan_batch(
                session,
                semaphore,
                plates_data,
                processed_plates,
                batch_start,
                batch_end,
            )


            blocked = sum(
                1
                for result in results
                if result == "blocked"
            )


            if blocked >= 20:

                print(
                    "⛔ Mange HTTP 403 "
                    "fra Bilopslag. "
                    "Stopper dette run."
                )

                break


            await asyncio.sleep(
                0.5
            )


    if processed_plates:

        save_to_json(
            plates_data
        )


    print("")
    print(
        "========== RESULTAT =========="
    )

    print(
        "Behandlede plader: "
        f"{len(processed_plates)}"
    )

    print(
        "=============================="
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    print(
        f"{PREFIX}-script startet."
    )

    delete_old_plates_from_supabase()

    asyncio.run(
        check_new_registrations()
    )

    print(
        f"{PREFIX}-script færdigt."
    )
