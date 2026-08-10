import asyncio
import aiohttp
import os
import re
import json
import html
import requests

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from bs4 import BeautifulSoup


# ============================================================
# KONFIGURATION
# ============================================================

REPO_ROOT = Path(__file__).resolve().parent

JSON_FILE_PATH = Path(
    os.getenv(
        "JSON_FILE_PATH",
        REPO_ROOT / "public" / "plates" / "plates.json"
    )
)

JSON_FILE_PATH.parent.mkdir(
    parents=True,
    exist_ok=True
)

SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    ""
).rstrip("/")

SUPABASE_SERVICE_ROLE_KEY = os.getenv(
    "SUPABASE_SERVICE_ROLE_KEY",
    ""
)


# ============================================================
# BILOPSLAG COOKIES
# ============================================================

try:
    BILOPSLAG_COOKIES = json.loads(
        os.getenv(
            "BILOPSLAG_COOKIES_JSON",
            ""
        ) or "{}"
    )

except json.JSONDecodeError:
    print(
        "⚠️ BILOPSLAG_COOKIES_JSON er ikke gyldig JSON."
    )

    BILOPSLAG_COOKIES = {}


# ============================================================
# INDSTILLINGER
# ============================================================

COPENHAGEN = ZoneInfo(
    "Europe/Copenhagen"
)

BILOPSLAG_BASE_URL = (
    "https://bilopslag.nu"
)

MAX_CONNECTIONS = int(
    os.getenv(
        "MAX_CONNECTIONS",
        "8"
    )
)

MAX_PAGES = int(
    os.getenv(
        "MAX_PAGES",
        "50"
    )
)

REQUEST_TIMEOUT = int(
    os.getenv(
        "REQUEST_TIMEOUT",
        "25"
    )
)

PLADE_REGEX = r"^[A-Z]{2}\d{3,5}$"


# ============================================================
# BILOPSLAG HEADERS
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

    for fmt in (
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d.%m.%Y",
    ):
        try:
            return datetime.strptime(
                value,
                fmt
            ).date()

        except ValueError:
            continue

    return None


def normalize_company(value):
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
            encoding="utf-8"
        ) as f:

            data = json.load(f)

            if isinstance(data, dict):
                return data

            return {}

    except (
        FileNotFoundError,
        json.JSONDecodeError
    ):
        return {}


def save_to_json(data):
    JSON_FILE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(
        JSON_FILE_PATH,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=4,
            sort_keys=True
        )


# ============================================================
# SUPABASE
# ============================================================

def supabase_headers(prefer=None):
    headers = {
        "apikey":
            SUPABASE_SERVICE_ROLE_KEY,

        "Authorization":
            f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",

        "Content-Type":
            "application/json",
    }

    if prefer:
        headers["Prefer"] = prefer

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
            "⚠️ Mangler SUPABASE_URL eller "
            "SUPABASE_SERVICE_ROLE_KEY. "
            "Springer oprydning over."
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
            timeout=30
        )

        if response.status_code not in (
            200,
            204
        ):
            print(
                "❌ Supabase-oprydning fejlede: "
                f"{response.status_code} "
                f"{response.text}"
            )

            return False

        print(
            "🧹 Plader med date før "
            f"{cutoff_date} er slettet."
        )

        return True

    except Exception as e:
        print(
            "❌ Fejl ved Supabase-oprydning: "
            f"{e}"
        )

        return False


# ============================================================
# UPLOAD TIL SUPABASE
# ============================================================

def upload_plate_to_supabase(
    company,
    entry
):
    if (
        not SUPABASE_URL
        or
        not SUPABASE_SERVICE_ROLE_KEY
    ):
        print(
            "⚠️ Mangler Supabase credentials."
        )

        return False

    url = (
        f"{SUPABASE_URL}"
        "/rest/v1/plates"
        "?on_conflict=company,plate"
    )

    payload = {
        "company": company,
        "plate": entry["plate"],
        "date": entry["date"],
        "checked": entry.get(
            "checked",
            False
        ),
        "premium": entry.get(
            "premium",
            0
        ),
        "note": entry.get(
            "note",
            ""
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
            timeout=20
        )

        if response.status_code in (
            200,
            201,
            204
        ):
            print(
                "✅ Uploadet/ignoreret i Supabase: "
                f"{company} | "
                f"{entry['plate']}"
            )

            return True

        if response.status_code == 409:
            print(
                "ℹ️ Findes allerede i Supabase: "
                f"{company} | "
                f"{entry['plate']}"
            )

            return True

        print(
            "❌ Supabase upload fejlede: "
            f"{response.status_code} "
            f"{response.text}"
        )

        return False

    except Exception as e:
        print(
            "❌ Fejl ved Supabase-upload: "
            f"{e}"
        )

        return False


# ============================================================
# HENT DAGENS NUMMERPLADER FRA BILOPSLAG
# ============================================================

def hent_plaader_fra_bilopslag():
    today = datetime.now(
        COPENHAGEN
    ).date()

    today_str = today.strftime(
        "%Y-%m-%d"
    )

    base_url = (
        "https://bilopslag.nu/api/"
        "advanced_search"
        "?registration_matches=%25%25%25%25%25"
        "&first_registration_date_gteq={dato}"
        "&page={side}"
    )

    plader = []

    for page in range(
        1,
        MAX_PAGES + 1
    ):
        url = base_url.format(
            dato=today_str,
            side=page
        )

        print(
            f"\n🔎 Henter Bilopslag side {page}"
        )

        try:
            resp = requests.get(
                url,
                headers=BILOPSLAG_HEADERS,
                cookies=BILOPSLAG_COOKIES,
                timeout=20
            )

            print(
                "HTTP status Bilopslag "
                f"side {page}: "
                f"{resp.status_code}"
            )

            resp.raise_for_status()

            data = resp.json()

            biler = data.get(
                "data",
                []
            )

            print(
                f"→ Antal biler på side {page}: "
                f"{len(biler)}"
            )

            if not biler:
                print(
                    "Ingen flere biler. "
                    "Stopper Bilopslag-søgningen."
                )

                break

            for bil in biler:
                plade = str(
                    bil.get(
                        "registration",
                        ""
                    )
                ).upper().strip()

                if not plade:
                    continue

                if re.match(
                    PLADE_REGEX,
                    plade
                ):
                    plader.append(
                        plade
                    )

        except Exception as e:
            print(
                f"⚠️ Fejl på Bilopslag "
                f"side {page}: {e}"
            )

            break

    # Fjern dubletter, men behold rækkefølge
    plader = list(
        dict.fromkeys(
            plader
        )
    )

    print(
        "\n🎯 Bilopslag fandt "
        f"{len(plader)} "
        "unikke nummerplader fra i dag."
    )

    return plader


# ============================================================
# FIND VEHICLE ID FRA BILOPSLAG-SIDEN
# ============================================================

def extract_vehicle_data_from_html(
    page_html,
    expected_plate
):
    soup = BeautifulSoup(
        page_html,
        "html.parser"
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
        raw_vehicle = html.unescape(
            raw_vehicle
        )

        vehicle = json.loads(
            raw_vehicle
        )

    except Exception as e:
        print(
            f"⚠️ Kunne ikke parse vehicle-data "
            f"for {expected_plate}: {e}"
        )

        return None

    registration = str(
        vehicle.get(
            "registration",
            ""
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
# HENT VEHICLE DATA FRA BILOPSLAG
# ============================================================

async def get_vehicle_data(
    session,
    regnr
):
    url = (
        f"{BILOPSLAG_BASE_URL}"
        f"/nummerplade/"
        f"{regnr.upper()}"
    )

    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(
                total=REQUEST_TIMEOUT
            ),
            allow_redirects=True
        ) as response:

            if response.status == 404:
                print(
                    f"ℹ️ {regnr}: "
                    "ikke fundet på Bilopslag."
                )

                return None

            if response.status == 403:
                print(
                    f"⛔ {regnr}: "
                    "Bilopslag gav HTTP 403."
                )

                return None

            if response.status == 429:
                print(
                    f"⚠️ {regnr}: "
                    "Bilopslag gav HTTP 429."
                )

                return None

            if response.status != 200:
                print(
                    f"⚠️ {regnr}: "
                    f"HTTP {response.status}"
                )

                return None

            page_html = await response.text(
                errors="ignore"
            )

            return extract_vehicle_data_from_html(
                page_html,
                regnr
            )

    except (
        aiohttp.ClientError,
        asyncio.TimeoutError
    ) as e:

        print(
            f"⚠️ Fejl ved Bilopslag-side "
            f"for {regnr}: {e}"
        )

        return None


# ============================================================
# HENT FORSIKRING FRA BILOPSLAGS DMR-ENDPOINT
# ============================================================

async def get_insurance_info(
    session,
    vehicle_id,
    regnr
):
    url = (
        f"{BILOPSLAG_BASE_URL}"
        f"/api/statistics/vehicles/"
        f"{vehicle_id}/dmr"
    )

    try:
        async with session.get(
            url,
            headers={
                "Accept":
                    "application/json",

                "X-Requested-With":
                    "XMLHttpRequest",

                "Referer":
                    (
                        f"{BILOPSLAG_BASE_URL}"
                        f"/nummerplade/"
                        f"{regnr}"
                    ),
            },
            timeout=aiohttp.ClientTimeout(
                total=REQUEST_TIMEOUT
            )
        ) as response:

            if response.status == 403:
                print(
                    f"⛔ {regnr}: "
                    "Bilopslag DMR gav HTTP 403."
                )

                return (
                    "Ukendt",
                    None,
                    "Ukendt"
                )

            if response.status == 429:
                print(
                    f"⚠️ {regnr}: "
                    "Bilopslag DMR gav HTTP 429."
                )

                return (
                    "Ukendt",
                    None,
                    "Ukendt"
                )

            if response.status != 200:
                print(
                    f"⚠️ {regnr}: "
                    "Bilopslag DMR gav HTTP "
                    f"{response.status}"
                )

                return (
                    "Ukendt",
                    None,
                    "Ukendt"
                )

            data = await response.json(
                content_type=None
            )

            dmr_data = (
                data.get(
                    "dmr_data",
                    {}
                )
                or {}
            )

            selskab = normalize_company(
                dmr_data.get(
                    "insurance_company"
                )
            )

            status = str(
                dmr_data.get(
                    "insurance_status",
                    "Ukendt"
                )
            ).strip()

            forsikringsdato = parse_date(
                dmr_data.get(
                    "insurance_created_at"
                )
            )

            return (
                selskab,
                forsikringsdato,
                status
            )

    except (
        aiohttp.ClientError,
        asyncio.TimeoutError,
        ValueError
    ) as e:

        print(
            f"⚠️ Forsikringsopslag fejlede "
            f"for {regnr}: {e}"
        )

        return (
            "Ukendt",
            None,
            "Ukendt"
        )


# ============================================================
# BEHANDL ÉN NUMMERPLADE
# ============================================================

async def process_plate(
    session,
    regnr,
    plates_data,
    uploaded_plates,
    semaphore
):
    async with semaphore:

        # ----------------------------------------------------
        # FIND VEHICLE-ID
        # ----------------------------------------------------

        vehicle = await get_vehicle_data(
            session,
            regnr
        )

        if not vehicle:
            return

        vehicle_id = vehicle.get(
            "id"
        )

        if not vehicle_id:
            print(
                f"⚠️ {regnr}: "
                "Bilopslag returnerede intet vehicle ID."
            )

            return


        # ----------------------------------------------------
        # FORSIKRING FRA BILOPSLAG
        # ----------------------------------------------------

        (
            selskab,
            forsikringsdato,
            forsikringsstatus
        ) = await get_insurance_info(
            session,
            vehicle_id,
            regnr
        )

        if (
            not selskab
            or
            selskab == "Ukendt"
        ):
            print(
                f"Springer {regnr} over - "
                "intet forsikringsselskab fundet."
            )

            return


        # ----------------------------------------------------
        # DATO
        # ----------------------------------------------------

        # Nummerpladen kommer allerede fra:
        #
        # first_registration_date_gteq=i dag
        #
        # Derfor gemmes den som dags dato.

        today = datetime.now(
            COPENHAGEN
        ).date()

        dato = today.isoformat()


        # ----------------------------------------------------
        # ENTRY
        # ----------------------------------------------------

        entry = {
            "date": dato,
            "plate": regnr,
            "checked": False,
            "premium": 0,
            "note": "",
        }


        # ----------------------------------------------------
        # LOKAL JSON
        # ----------------------------------------------------

        if selskab not in plates_data:
            plates_data[
                selskab
            ] = []

        existing_plates = {
            p.get(
                "plate"
            )
            for p
            in plates_data.get(
                selskab,
                []
            )
        }

        if (
            regnr
            not in existing_plates
        ):
            plates_data[
                selskab
            ].append(
                entry
            )


        # ----------------------------------------------------
        # SUPABASE
        # ----------------------------------------------------

        ok = upload_plate_to_supabase(
            selskab,
            entry
        )

        if ok:
            uploaded_plates.add(
                regnr
            )

            if forsikringsdato:
                forsikringsdato_text = (
                    forsikringsdato.isoformat()
                )
            else:
                forsikringsdato_text = (
                    "ukendt"
                )

            print(
                f"✅ Behandlet: "
                f"{regnr} | "
                f"{selskab} | "
                f"status: {forsikringsstatus} | "
                "forsikringsdato: "
                f"{forsikringsdato_text}"
            )


# ============================================================
# HOVEDPROGRAM
# ============================================================

async def check_new_registrations():
    print(
        "Starter Bilopslag-scriptet."
    )

    plates_data = (
        load_existing_data()
    )

    uploaded_plates = set()

    plader = (
        hent_plaader_fra_bilopslag()
    )

    if not plader:
        print(
            "Ingen biler fundet i Bilopslag."
        )

        return

    print(
        "\nStarter Bilopslag "
        "forsikringsopslag for "
        f"{len(plader)} plader."
    )

    connector = aiohttp.TCPConnector(
        limit=MAX_CONNECTIONS,
        ttl_dns_cache=300
    )

    semaphore = asyncio.Semaphore(
        MAX_CONNECTIONS
    )

    async with aiohttp.ClientSession(
        connector=connector,
        headers=BILOPSLAG_HEADERS,
        cookies=BILOPSLAG_COOKIES
    ) as session:

        tasks = [
            process_plate(
                session,
                regnr,
                plates_data,
                uploaded_plates,
                semaphore
            )
            for regnr in plader
        ]

        await asyncio.gather(
            *tasks
        )

    if uploaded_plates:
        save_to_json(
            plates_data
        )

    print("")
    print(
        "========== RESULTAT =========="
    )

    print(
        "Bilopslag fandt: "
        f"{len(plader)} "
        "nummerplader fra i dag."
    )

    print(
        "Uploadet/ignoreret i "
        "Supabase: "
        f"{len(uploaded_plates)}"
    )

    print(
        "=============================="
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    print(
        "Bilopslag-script startet."
    )

    delete_old_plates_from_supabase()

    asyncio.run(
        check_new_registrations()
    )

    print(
        "Bilopslag-script færdigt."
    )
