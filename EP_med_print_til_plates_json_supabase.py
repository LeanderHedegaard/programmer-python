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
# COOKIES
# ============================================================

# Valgfrit GitHub Secret:
#
# NUMMERPLADE_COOKIES_JSON
#
# Eksempel:
#
# {
#   "PHPSESSID": "...",
#   "_ga": "...",
#   "_fbp": "..."
# }

try:
    NUMMERPLADE_COOKIES = json.loads(
        os.getenv(
            "NUMMERPLADE_COOKIES_JSON",
            "",
        ) or "{}"
    )

except json.JSONDecodeError:
    print(
        "⚠️ NUMMERPLADE_COOKIES_JSON er ikke gyldig JSON."
    )

    NUMMERPLADE_COOKIES = {}


# ============================================================
# NUMMERPLADE-INTERVAL
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
# NUMMERPLADE.NET
# ============================================================

BASE_URL = (
    "https://www.nummerplade.net/nummerplade"
)

# Start forholdsvis forsigtigt.
MAX_CONNECTIONS = int(
    os.getenv(
        "MAX_CONNECTIONS",
        "6",
    )
)

SCAN_BATCH_SIZE = int(
    os.getenv(
        "SCAN_BATCH_SIZE",
        "250",
    )
)

MAX_RETRIES = int(
    os.getenv(
        "MAX_RETRIES",
        "2",
    )
)

REQUEST_TIMEOUT_SECONDS = int(
    os.getenv(
        "REQUEST_TIMEOUT_SECONDS",
        "20",
    )
)


# ============================================================
# SUPABASE
# ============================================================

SUPABASE_BATCH_SIZE = int(
    os.getenv(
        "SUPABASE_BATCH_SIZE",
        "100",
    )
)


# ============================================================
# TID
# ============================================================

COPENHAGEN = ZoneInfo(
    "Europe/Copenhagen"
)


# ============================================================
# HEADERS
# ============================================================

REQUEST_HEADERS = {
    "Accept": (
        "text/html,"
        "application/xhtml+xml,"
        "application/xml;q=0.9,"
        "image/avif,"
        "image/webp,"
        "image/apng,"
        "*/*;q=0.8"
    ),

    "Accept-Language": (
        "da-DK,da;q=0.9,"
        "en-US;q=0.8,"
        "en;q=0.7"
    ),

    "Cache-Control": "no-cache",

    "Pragma": "no-cache",

    "Referer": (
        "https://www.nummerplade.net/"
    ),

    "Upgrade-Insecure-Requests": "1",

    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/150.0.0.0 "
        "Safari/537.36"
    ),
}


# ============================================================
# HJÆLPEFUNKTIONER
# ============================================================

def parse_danish_date(value):
    """
    Konverterer fx:

    31-05-2026

    eller:

    2026-05-31

    til datetime.date.
    """

    if not value:
        return None

    value = value.strip()

    for date_format in (
        "%d-%m-%Y",
        "%Y-%m-%d",
    ):

        try:
            return datetime.strptime(
                value,
                date_format,
            ).date()

        except ValueError:
            continue

    return None


def normalize_company(value):
    """
    Fjerner ekstra whitespace.
    """

    if not value:
        return "Ukendt"

    return " ".join(
        value.split()
    ).strip()


def chunks(items, size):
    """
    Deler liste op i mindre batches.
    """

    for index in range(
        0,
        len(items),
        size,
    ):

        yield items[
            index:index + size
        ]


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
# SUPABASE HEADERS
# ============================================================

def supabase_headers(
    prefer=None,
):

    headers = {
        "apikey": (
            SUPABASE_SERVICE_ROLE_KEY
        ),

        "Authorization": (
            f"Bearer "
            f"{SUPABASE_SERVICE_ROLE_KEY}"
        ),

        "Content-Type": (
            "application/json"
        ),
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
            "SUPABASE_SERVICE_ROLE_KEY."
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
                "❌ Supabase-oprydning "
                "fejlede: "
                f"{response.status_code} "
                f"{response.text}"
            )

            return False

        print(
            "🧹 Supabase-oprydning OK. "
            f"Rækker før {cutoff_date} "
            "er slettet."
        )

        return True

    except requests.RequestException as error:

        print(
            "❌ Netværksfejl ved "
            f"Supabase-oprydning: {error}"
        )

        return False


# ============================================================
# UPLOAD TIL SUPABASE
# ============================================================

def upload_entries_to_supabase(
    entries,
):

    if not entries:

        print(
            "ℹ️ Ingen plader at sende "
            "til Supabase."
        )

        return 0

    if (
        not SUPABASE_URL
        or
        not SUPABASE_SERVICE_ROLE_KEY
    ):

        print(
            "⚠️ Mangler Supabase "
            "credentials."
        )

        return 0

    url = (
        f"{SUPABASE_URL}"
        f"/rest/v1/plates"
        "?on_conflict=company,plate"
    )

    accepted = 0

    for batch in chunks(
        entries,
        SUPABASE_BATCH_SIZE,
    ):

        try:

            response = requests.post(
                url,

                headers=supabase_headers(
                    (
                        "resolution="
                        "ignore-duplicates,"
                        "return=minimal"
                    )
                ),

                json=batch,

                timeout=30,
            )

            if response.status_code in (
                200,
                201,
                204,
            ):

                accepted += len(
                    batch
                )

                print(
                    "✅ Supabase batch: "
                    f"{len(batch)} plader."
                )

                continue

            if response.status_code == 409:

                print(
                    "ℹ️ Dubletter i "
                    "Supabase batch."
                )

                accepted += len(
                    batch
                )

                continue

            print(
                "❌ Supabase upload "
                "fejlede: "
                f"{response.status_code} "
                f"{response.text}"
            )

        except requests.RequestException as error:

            print(
                "❌ Netværksfejl ved "
                f"Supabase-upload: {error}"
            )

    return accepted


# ============================================================
# NY HTML-STRUKTUR
# ============================================================

def extract_first_registration_date(
    soup,
):
    """
    Finder:

    <span>1. registrering</span>
    <b>31-05-2026</b>
    """

    for label in soup.find_all(
        "span"
    ):

        label_text = (
            " ".join(
                label.get_text(
                    " ",
                    strip=True,
                ).split()
            )
            .lower()
        )

        if label_text == (
            "1. registrering"
        ):

            parent = label.parent

            if parent:

                value = parent.find(
                    "b"
                )

                if value:

                    return (
                        parse_danish_date(
                            value.get_text(
                                " ",
                                strip=True,
                            )
                        )
                    )

    return None


# ============================================================
# FORSIKRING - NY STRUKTUR
# ============================================================

def extract_current_insurance(
    soup,
):
    """
    NY struktur:

    <div id="forsikring-card">

      <div class="bb-dom ...">

        <b>
          IF SKADEFORSIKRING
        </b>

        <span class="fa-nb">
          31-05-2026
        </span>

      </div>

      <div class="fa-rk">

        <span class="fa-dato">
          31-05-2026
        </span>

        <b>
          IF SKADEFORSIKRING
        </b>

        <span class="fa-stat gron">
          Aktiv
        </span>

      </div>

    </div>
    """

    # --------------------------------------------------------
    # 1. NY AKTIV FORSIKRINGSBOKS
    # --------------------------------------------------------

    insurance_box = soup.select_one(
        "#forsikring-card .bb-dom"
    )

    if insurance_box:

        company_element = (
            insurance_box.find(
                "b"
            )
        )

        date_element = (
            insurance_box.select_one(
                ".fa-nb"
            )
        )

        company = normalize_company(
            company_element.get_text(
                " ",
                strip=True,
            )
            if company_element
            else ""
        )

        insurance_date = (
            parse_danish_date(
                date_element.get_text(
                    " ",
                    strip=True,
                )
                if date_element
                else ""
            )
        )

        if company != "Ukendt":

            return (
                company,
                insurance_date,
            )


    # --------------------------------------------------------
    # 2. FALLBACK: HISTORIK
    # --------------------------------------------------------

    history_rows = soup.select(
        "#forsikring-card .fa-rk"
    )

    for history_row in history_rows:

        status_element = (
            history_row.select_one(
                ".fa-stat"
            )
        )

        status_text = (
            status_element.get_text(
                " ",
                strip=True,
            ).lower()
            if status_element
            else ""
        )

        # Hvis status findes,
        # bruger vi kun aktiv forsikring.
        if (
            status_text
            and
            "aktiv" not in status_text
        ):

            continue

        company_element = (
            history_row.find(
                "b"
            )
        )

        date_element = (
            history_row.select_one(
                ".fa-dato"
            )
        )

        company = normalize_company(
            company_element.get_text(
                " ",
                strip=True,
            )
            if company_element
            else ""
        )

        insurance_date = (
            parse_danish_date(
                date_element.get_text(
                    " ",
                    strip=True,
                )
                if date_element
                else ""
            )
        )

        if company != "Ukendt":

            return (
                company,
                insurance_date,
            )


    # --------------------------------------------------------
    # 3. FALLBACK: KPI
    # --------------------------------------------------------

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

            return (
                company,
                None,
            )

    return (
        "Ukendt",
        None,
    )


# ============================================================
# PARSE HELE BILSIDEN
# ============================================================

def extract_vehicle_data(
    html,
    expected_plate,
):

    soup = BeautifulSoup(
        html,
        "html.parser",
    )


    # --------------------------------------------------------
    # NUMMERPLADE
    # --------------------------------------------------------

    plate_element = soup.select_one(
        ".dny-plade"
    )

    if plate_element:

        plate = (
            plate_element
            .get_text(
                " ",
                strip=True,
            )
            .upper()
        )

    else:

        plate = ""


    # --------------------------------------------------------
    # FALLBACK FRA TITLE
    # --------------------------------------------------------

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

            plate = (
                title_match.group(
                    1
                )
            )


    # --------------------------------------------------------
    # KONTROLLER AT DET ER KORREKT PLADE
    # --------------------------------------------------------

    if (
        plate
        !=
        expected_plate.upper()
    ):

        return None


    # --------------------------------------------------------
    # STELNUMMER
    # --------------------------------------------------------

    vin = None

    vin_element = soup.select_one(
        ".dny-stelnr[data-v]"
    )

    if vin_element:

        vin = (
            vin_element
            .get(
                "data-v",
                "",
            )
            .strip()
            .upper()
        )


    # --------------------------------------------------------
    # FALLBACK FRA META DESCRIPTION
    # --------------------------------------------------------

    if not vin:

        description = soup.find(
            "meta",
            attrs={
                "name": "description"
            },
        )

        description_text = (
            description.get(
                "content",
                "",
            )
            if description
            else ""
        )

        vin_match = re.search(
            (
                r"stelnummer\s+"
                r"([A-HJ-NPR-Z0-9]{17})"
            ),
            description_text,
            re.IGNORECASE,
        )

        if vin_match:

            vin = (
                vin_match.group(
                    1
                ).upper()
            )


    # --------------------------------------------------------
    # FØRSTE REGISTRERING
    # --------------------------------------------------------

    first_registration_date = (
        extract_first_registration_date(
            soup
        )
    )


    # --------------------------------------------------------
    # FORSIKRING
    # --------------------------------------------------------

    company, insurance_date = (
        extract_current_insurance(
            soup
        )
    )


    return {
        "plate": plate,

        "vin": vin,

        "first_registration_date": (
            first_registration_date
        ),

        "insurance_company": (
            company
        ),

        "insurance_date": (
            insurance_date
        ),
    }


# ============================================================
# HTTP OPSLAG
# ============================================================

async def get_car_info(
    session,
    regnr,
    semaphore,
):

    url = (
        f"{BASE_URL}/"
        f"{regnr.lower()}.html"
    )

    async with semaphore:

        for attempt in range(
            1,
            MAX_RETRIES + 2,
        ):

            try:

                timeout = (
                    aiohttp.ClientTimeout(
                        total=(
                            REQUEST_TIMEOUT_SECONDS
                        )
                    )
                )

                async with session.get(
                    url,
                    timeout=timeout,
                    allow_redirects=True,
                ) as response:


                    # ------------------------------------------------
                    # 404 = PLADE FINDES IKKE
                    # ------------------------------------------------

                    if response.status == 404:

                        return None


                    # ------------------------------------------------
                    # 403 = RUNNER AFVISES
                    # ------------------------------------------------

                    if response.status == 403:

                        return {
                            "blocked": True,
                            "plate": regnr,
                            "status": 403,
                        }


                    # ------------------------------------------------
                    # RETRY VED RATE LIMIT / SERVERFEJL
                    # ------------------------------------------------

                    if response.status in (
                        429,
                        500,
                        502,
                        503,
                        504,
                    ):

                        if attempt > MAX_RETRIES:

                            print(
                                f"⚠️ {regnr}: "
                                f"HTTP {response.status} "
                                f"efter {attempt} forsøg."
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
                                +
                                random.uniform(
                                    0.2,
                                    1.0,
                                )
                            )


                        await asyncio.sleep(
                            wait_seconds
                        )

                        continue


                    # ------------------------------------------------
                    # ANDEN HTTP FEJL
                    # ------------------------------------------------

                    if response.status != 200:

                        return None


                    # ------------------------------------------------
                    # HTML
                    # ------------------------------------------------

                    html = await response.text(
                        errors="ignore"
                    )


                    vehicle = (
                        extract_vehicle_data(
                            html,
                            regnr,
                        )
                    )

                    return vehicle


            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
            ) as error:

                if attempt > MAX_RETRIES:

                    print(
                        f"⚠️ {regnr}: "
                        "netværksfejl efter "
                        f"{attempt} forsøg: "
                        f"{error}"
                    )

                    return None


                await asyncio.sleep(
                    2 ** attempt
                    +
                    random.uniform(
                        0.2,
                        1.0,
                    )
                )


    return None


# ============================================================
# BESTEM DATO
# ============================================================

def choose_entry_date(
    vehicle,
):
    """
    Pladen medtages hvis:

    forsikringsdato = i dag/i går

    ELLER

    første registrering = i dag/i går
    """

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

    recent_dates = {
        today,
        yesterday,
    }

    insurance_date = (
        vehicle.get(
            "insurance_date"
        )
    )

    registration_date = (
        vehicle.get(
            "first_registration_date"
        )
    )


    # Forsikringsdato har førsteprioritet
    if insurance_date in recent_dates:

        return insurance_date


    # Derefter første registrering
    if registration_date in recent_dates:

        return registration_date


    return None


# ============================================================
# LOKAL BACKUP
# ============================================================

def add_to_local_backup(
    plates_data,
    company,
    entry,
):

    if company not in plates_data:

        plates_data[
            company
        ] = []


    existing = {
        item.get(
            "plate"
        )
        for item
        in plates_data[
            company
        ]
    }


    if (
        entry["plate"]
        not in existing
    ):

        plates_data[
            company
        ].append(
            entry
        )


# ============================================================
# SCAN EN BATCH
# ============================================================

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


# ============================================================
# HOVEDPROGRAM
# ============================================================

async def check_new_registrations():

    print(
        f"Starter scanning af "
        f"{PREFIX}{START_NUMBER:05d}"
        "–"
        f"{PREFIX}{END_NUMBER:05d}."
    )


    plates_data = (
        load_existing_data()
    )

    supabase_entries = []


    found_pages = 0

    recent_plates = 0

    missing_company = 0

    blocked_count = 0

    checked_count = 0


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
        headers=REQUEST_HEADERS,
        cookies=NUMMERPLADE_COOKIES,
    ) as session:


        # --------------------------------------------------------
        # BATCH LOOP
        # --------------------------------------------------------

        for batch_start in range(
            START_NUMBER,
            END_NUMBER + 1,
            SCAN_BATCH_SIZE,
        ):


            batch_end = min(
                (
                    batch_start
                    +
                    SCAN_BATCH_SIZE
                    -
                    1
                ),
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
                batch_start,
                batch_end,
            )


            batch_blocked = 0


            # ----------------------------------------------------
            # BEHANDL RESULTATER
            # ----------------------------------------------------

            for vehicle in results:

                checked_count += 1


                if not vehicle:

                    continue


                # ------------------------------------------------
                # 403
                # ------------------------------------------------

                if vehicle.get(
                    "blocked"
                ):

                    blocked_count += 1

                    batch_blocked += 1

                    continue


                found_pages += 1


                # ------------------------------------------------
                # ER BILEN RELEVANT?
                # ------------------------------------------------

                entry_date = (
                    choose_entry_date(
                        vehicle
                    )
                )


                if not entry_date:

                    continue


                # ------------------------------------------------
                # FORSIKRINGSSELSKAB
                # ------------------------------------------------

                company = vehicle.get(
                    "insurance_company",
                    "Ukendt",
                )


                if (
                    not company
                    or
                    company == "Ukendt"
                ):

                    missing_company += 1

                    print(
                        "⚠️ Relevant plade "
                        "uden selskab: "
                        f"{vehicle['plate']}"
                    )

                    continue


                # ------------------------------------------------
                # ENTRY
                # ------------------------------------------------

                entry = {
                    "company": company,

                    "plate": (
                        vehicle[
                            "plate"
                        ]
                    ),

                    "date": (
                        entry_date
                        .isoformat()
                    ),

                    "checked": False,

                    "premium": 0,

                    "note": "",
                }


                supabase_entries.append(
                    entry
                )


                # ------------------------------------------------
                # LOKAL JSON
                # ------------------------------------------------

                add_to_local_backup(
                    plates_data,
                    company,
                    {
                        "plate": (
                            entry["plate"]
                        ),

                        "date": (
                            entry["date"]
                        ),

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


            # ----------------------------------------------------
            # STOP VED MASSIV 403
            # ----------------------------------------------------

            batch_size_actual = (
                batch_end
                -
                batch_start
                +
                1
            )


            blocked_threshold = max(
                20,
                int(
                    batch_size_actual
                    *
                    0.25
                ),
            )


            if (
                batch_blocked
                >=
                blocked_threshold
            ):

                print("")

                print(
                    "⛔ Stopper scanning."
                )

                print(
                    f"{batch_blocked} "
                    "requests i denne batch "
                    "fik HTTP 403."
                )

                print(
                    "Siden afviser denne "
                    "runner."
                )

                break


            # ----------------------------------------------------
            # PAUSE MELLEM BATCHES
            # ----------------------------------------------------

            await asyncio.sleep(
                random.uniform(
                    0.3,
                    0.8,
                )
            )


    # ============================================================
    # FJERN DUBLETTER FRA DETTE RUN
    # ============================================================

    unique_entries = {}


    for entry in supabase_entries:

        key = (
            entry["company"],
            entry["plate"],
        )

        unique_entries[
            key
        ] = entry


    final_entries = list(
        unique_entries.values()
    )


    # ============================================================
    # SUPABASE UPLOAD
    # ============================================================

    uploaded = (
        upload_entries_to_supabase(
            final_entries
        )
    )


    # ============================================================
    # LOKAL JSON
    # ============================================================

    if final_entries:

        save_to_json(
            plates_data
        )


    # ============================================================
    # RESULTAT
    # ============================================================

    print("")

    print(
        "========== RESULTAT =========="
    )

    print(
        f"Requests kontrolleret: "
        f"{checked_count}"
    )

    print(
        f"Gyldige køretøjssider: "
        f"{found_pages}"
    )

    print(
        f"HTTP 403-afvisninger: "
        f"{blocked_count}"
    )

    print(
        "Relevante plader "
        "fra i dag/i går: "
        f"{recent_plates}"
    )

    print(
        "Relevante plader "
        "uden selskab: "
        f"{missing_company}"
    )

    print(
        f"Sendt til Supabase: "
        f"{uploaded}"
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
