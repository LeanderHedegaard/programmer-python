import asyncio
import aiohttp
import os
import re
import json
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

try:
    BILOPSLAG_COOKIES = json.loads(
        os.getenv(
            "BILOPSLAG_COOKIES_JSON",
            "",
        ) or "{}"
    )
except json.JSONDecodeError:
    print("⚠️ BILOPSLAG_COOKIES_JSON er ugyldig JSON.")
    BILOPSLAG_COOKIES = {}


# Nummerplade.net cookies er VALGFRIE.
# Scriptet kan fungere uden, hvis siden leverer HTML offentligt.
try:
    NUMMERPLADE_COOKIES = json.loads(
        os.getenv(
            "NUMMERPLADE_COOKIES_JSON",
            "",
        ) or "{}"
    )
except json.JSONDecodeError:
    print("⚠️ NUMMERPLADE_COOKIES_JSON er ugyldig JSON.")
    NUMMERPLADE_COOKIES = {}


# ============================================================
# INDSTILLINGER
# ============================================================

COPENHAGEN = ZoneInfo(
    "Europe/Copenhagen"
)

BILOPSLAG_BASE_URL = (
    "https://bilopslag.nu"
)

NUMMERPLADE_BASE_URL = (
    "https://www.nummerplade.net"
)

MAX_CONNECTIONS = int(
    os.getenv(
        "MAX_CONNECTIONS",
        "10",
    )
)

REQUEST_TIMEOUT = int(
    os.getenv(
        "REQUEST_TIMEOUT",
        "20",
    )
)

MAX_RETRIES = int(
    os.getenv(
        "MAX_RETRIES",
        "2",
    )
)

PROGRESS_EVERY = int(
    os.getenv(
        "PROGRESS_EVERY",
        "25",
    )
)

PLADE_REGEX = re.compile(
    r"^[A-Z]{2}\d{3,5}$"
)


# ============================================================
# HEADERS - BILOPSLAG
# ============================================================

BILOPSLAG_HEADERS = {
    "accept": "*/*",

    "accept-language": (
        "da-DK,da;q=0.9,"
        "en-US;q=0.8,"
        "en;q=0.7"
    ),

    "cache-control": "no-cache",

    "pragma": "no-cache",

    "referer": (
        "https://bilopslag.nu/"
        "avanceret-soegning"
    ),

    "sec-fetch-dest": "empty",

    "sec-fetch-mode": "cors",

    "sec-fetch-site": "same-origin",

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
# HEADERS - NUMMERPLADE.NET
# ============================================================

NUMMERPLADE_HEADERS = {
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

    value = str(
        value
    ).strip()

    for fmt in (
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d.%m.%Y",
    ):
        try:
            return datetime.strptime(
                value,
                fmt,
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


def chunks(items, size):
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

            data = json.load(
                file
            )

            if isinstance(
                data,
                dict,
            ):
                return data

    except (
        FileNotFoundError,
        json.JSONDecodeError,
    ):
        pass

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
    """
    Beholder kun:

    - i dag
    - i går

    Alt ældre slettes.
    """

    if (
        not SUPABASE_URL
        or
        not SUPABASE_SERVICE_ROLE_KEY
    ):
        print(
            "⚠️ Mangler Supabase credentials. "
            "Springer oprydning over."
        )

        return False

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

    cutoff_date = (
        yesterday.isoformat()
    )

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
                "❌ Supabase-oprydning fejlede: "
                f"{response.status_code} "
                f"{response.text}"
            )

            return False

        print(
            "🧹 Supabase opryddet."
        )

        print(
            f"   Beholder kun "
            f"{yesterday} og {today}."
        )

        return True

    except Exception as error:
        print(
            "❌ Fejl ved "
            f"Supabase-oprydning: {error}"
        )

        return False


# ============================================================
# HENT ALLEREDE BEHANDLEDE PLADER
# ============================================================

def get_existing_plates_from_supabase():
    """
    Alle plader som allerede findes i Supabase
    springes over INDEN opslag på Nummerplade.net.
    """

    if (
        not SUPABASE_URL
        or
        not SUPABASE_SERVICE_ROLE_KEY
    ):
        print(
            "⚠️ Mangler Supabase credentials."
        )

        return set()

    existing = set()

    limit = 1000
    offset = 0

    print("")
    print(
        "🔎 Henter allerede behandlede "
        "plader fra Supabase..."
    )

    while True:

        url = (
            f"{SUPABASE_URL}"
            "/rest/v1/plates"
            "?select=plate"
            f"&limit={limit}"
            f"&offset={offset}"
        )

        try:
            response = requests.get(
                url,
                headers=supabase_headers(),
                timeout=30,
            )

            if response.status_code != 200:

                print(
                    "⚠️ Supabase GET fejlede: "
                    f"{response.status_code} "
                    f"{response.text}"
                )

                break

            rows = response.json()

            for row in rows:

                plate = str(
                    row.get(
                        "plate",
                        "",
                    )
                ).upper().strip()

                if plate:
                    existing.add(
                        plate
                    )

            if len(rows) < limit:
                break

            offset += limit

        except Exception as error:

            print(
                "⚠️ Fejl ved læsning "
                f"fra Supabase: {error}"
            )

            break

    print(
        f"✅ {len(existing)} plader "
        "er allerede behandlet."
    )

    return existing


# ============================================================
# UPLOAD TIL SUPABASE
# ============================================================

def upload_batch_to_supabase(
    entries,
):

    if not entries:

        print(
            "ℹ️ Ingen nye plader "
            "at sende til Supabase."
        )

        return 0

    if (
        not SUPABASE_URL
        or
        not SUPABASE_SERVICE_ROLE_KEY
    ):
        print(
            "⚠️ Mangler Supabase credentials."
        )

        return 0

    url = (
        f"{SUPABASE_URL}"
        "/rest/v1/plates"
        "?on_conflict=company,plate"
    )

    headers = supabase_headers(
        "resolution=ignore-duplicates,"
        "return=minimal"
    )

    uploaded = 0

    for batch in chunks(
        entries,
        250,
    ):

        try:
            response = requests.post(
                url,
                headers=headers,
                json=batch,
                timeout=45,
            )

            if response.status_code in (
                200,
                201,
                204,
            ):

                uploaded += len(
                    batch
                )

                print(
                    "✅ Supabase batch: "
                    f"{len(batch)} plader."
                )

                continue

            print(
                "❌ Supabase upload fejlede: "
                f"{response.status_code} "
                f"{response.text}"
            )

        except Exception as error:

            print(
                "❌ Supabase upload-fejl: "
                f"{error}"
            )

    return uploaded


# ============================================================
# BILOPSLAG - ADVANCED SEARCH
# ============================================================

def hent_registrerede_koeretoejer():
    """
    Henter biler der er blevet registreret:

    - i dag
    - i går
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

    print("")
    print(
        "=========================================="
    )

    print(
        "HENTER REGISTRERINGER FRA BILOPSLAG"
    )

    print(
        "=========================================="
    )

    print(
        f"Fra: {yesterday.isoformat()}"
    )

    print(
        f"Til: {today.isoformat()}"
    )

    base_url = (
        f"{BILOPSLAG_BASE_URL}"
        "/api/advanced_search"
    )

    base_params = {

        "registration_status_in[]":
            "Registreret",

        "registration_status_updated_at_gteq":
            yesterday.isoformat(),

        "registration_status_updated_at_lteq":
            today.isoformat(),
    }

    vehicles = {}

    page = 1
    total_pages = None

    while True:

        params = {
            **base_params,
            "page": page,
        }

        try:

            print(
                f"🔎 Henter side {page}"
            )

            response = requests.get(
                base_url,
                params=params,
                headers=BILOPSLAG_HEADERS,
                cookies=BILOPSLAG_COOKIES,
                timeout=30,
            )

            print(
                f"HTTP {response.status_code}"
            )

            response.raise_for_status()

            payload = response.json()

            cars = payload.get(
                "data",
                [],
            )

            if total_pages is None:

                total_pages = int(
                    payload.get(
                        "total_pages",
                        1,
                    )
                )

                total_count = int(
                    payload.get(
                        "total_count",
                        len(cars),
                    )
                )

                print(
                    f"📊 {total_count} køretøjer "
                    f"fordelt på "
                    f"{total_pages} sider."
                )

            print(
                f"→ {len(cars)} biler "
                f"på side {page}"
            )

            for car in cars:

                registration = str(
                    car.get(
                        "registration",
                        "",
                    )
                ).upper().strip()

                if not registration:
                    continue

                if not PLADE_REGEX.match(
                    registration
                ):
                    continue

                status = str(
                    car.get(
                        "registration_status",
                        "",
                    )
                ).strip()

                if status != "Registreret":
                    continue

                status_date = parse_date(
                    car.get(
                        "registration_status_updated_at"
                    )
                )

                if not status_date:
                    continue

                if status_date not in (
                    today,
                    yesterday,
                ):
                    continue

                vehicles[
                    registration
                ] = {

                    "registration":
                        registration,

                    "status_date":
                        status_date,

                    "vehicle_id":
                        car.get(
                            "id"
                        ),

                    "vin":
                        str(
                            car.get(
                                "vin",
                                "",
                            )
                        ).upper().strip(),
                }

            has_more = bool(
                payload.get(
                    "has_more",
                    False,
                )
            )

            if not has_more:
                break

            if (
                total_pages is not None
                and
                page >= total_pages
            ):
                break

            page += 1

        except Exception as error:

            print(
                f"❌ Fejl på side {page}: "
                f"{error}"
            )

            break

    result = list(
        vehicles.values()
    )

    print("")

    print(
        f"🎯 Fandt {len(result)} "
        "unikke registrerede køretøjer."
    )

    return result


# ============================================================
# PARSE FORSIKRING FRA NUMMERPLADE.NET HTML
# ============================================================

def extract_insurance_from_html(
    page_html,
):

    soup = BeautifulSoup(
        page_html,
        "html.parser",
    )

    # ========================================================
    # PRIMÆR METODE:
    # detaljeret forsikringskort
    # ========================================================

    insurance_box = soup.select_one(
        "#forsikring-card .bb-dom"
    )

    if insurance_box:

        company_element = (
            insurance_box.select_one(
                ".bb-dom-tx > b"
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

        date_text = (
            date_element.get_text(
                " ",
                strip=True,
            )
            if date_element
            else ""
        )

        insurance_date = parse_date(
            date_text
        )

        box_text = (
            insurance_box.get_text(
                " ",
                strip=True,
            ).lower()
        )

        if "aktiv forsikring" in box_text:
            status = "Aktiv"

        elif "ophørt" in box_text:
            status = "Ophørt"

        else:
            status = "Ukendt"

        if company != "Ukendt":

            return {
                "company":
                    company,

                "status":
                    status,

                "insurance_date":
                    insurance_date,
            }


    # ========================================================
    # FALLBACK:
    # historikrække
    # ========================================================

    history_rows = soup.select(
        "#forsikring-card .fa-rk"
    )

    for row in history_rows:

        company_element = (
            row.find(
                "b"
            )
        )

        date_element = (
            row.select_one(
                ".fa-dato"
            )
        )

        status_element = (
            row.select_one(
                ".fa-stat"
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

        status = (
            status_element.get_text(
                " ",
                strip=True,
            )
            if status_element
            else "Ukendt"
        )

        insurance_date = parse_date(
            date_element.get_text(
                " ",
                strip=True,
            )
            if date_element
            else ""
        )

        if (
            company != "Ukendt"
            and
            status.lower() == "aktiv"
        ):

            return {
                "company":
                    company,

                "status":
                    status,

                "insurance_date":
                    insurance_date,
            }


    # ========================================================
    # SIDSTE FALLBACK:
    # KPI
    # ========================================================

    kpi = soup.select_one(
        "#kpi-fors-val"
    )

    if kpi:

        company = normalize_company(
            kpi.get_text(
                " ",
                strip=True,
            )
        )

        if (
            company
            and
            company not in (
                "Ukendt",
                "—",
                "-",
            )
        ):

            return {
                "company":
                    company,

                "status":
                    "Ukendt",

                "insurance_date":
                    None,
            }

    return None


# ============================================================
# HENT NUMMERPLADE.NET SIDE
# ============================================================

async def get_insurance_info(
    session,
    vehicle,
    semaphore,
):

    regnr = vehicle[
        "registration"
    ]

    url = (
        f"{NUMMERPLADE_BASE_URL}"
        f"/nummerplade/"
        f"{regnr.lower()}.html"
    )

    async with semaphore:

        for attempt in range(
            1,
            MAX_RETRIES + 1,
        ):

            try:

                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(
                        total=REQUEST_TIMEOUT
                    ),
                    allow_redirects=True,
                ) as response:

                    # ========================================
                    # HTTP 200
                    # ========================================

                    if response.status == 200:

                        page_html = (
                            await response.text(
                                errors="ignore"
                            )
                        )

                        insurance = (
                            extract_insurance_from_html(
                                page_html
                            )
                        )

                        if not insurance:

                            return {
                                "success":
                                    False,

                                "plate":
                                    regnr,

                                "error":
                                    "no_insurance_data",
                            }

                        return {

                            "success":
                                True,

                            "plate":
                                regnr,

                            "company":
                                insurance[
                                    "company"
                                ],

                            "insurance_status":
                                insurance[
                                    "status"
                                ],

                            "insurance_date":
                                insurance[
                                    "insurance_date"
                                ],

                            "registration_date":
                                vehicle[
                                    "status_date"
                                ],
                        }


                    # ========================================
                    # 404
                    # ========================================

                    if response.status == 404:

                        return {
                            "success":
                                False,

                            "plate":
                                regnr,

                            "error":
                                "404",
                        }


                    # ========================================
                    # 403
                    # ========================================

                    if response.status == 403:

                        return {
                            "success":
                                False,

                            "plate":
                                regnr,

                            "error":
                                "403",
                        }


                    # ========================================
                    # 429
                    # ========================================

                    if response.status == 429:

                        retry_after = int(
                            response.headers.get(
                                "Retry-After",
                                "3",
                            )
                        )

                        if attempt < MAX_RETRIES:

                            await asyncio.sleep(
                                retry_after
                            )

                            continue

                        return {
                            "success":
                                False,

                            "plate":
                                regnr,

                            "error":
                                "429",
                        }


                    return {
                        "success":
                            False,

                        "plate":
                            regnr,

                        "error":
                            f"http_{response.status}",
                    }


            except asyncio.TimeoutError:

                if attempt < MAX_RETRIES:

                    await asyncio.sleep(
                        1
                    )

                    continue

                return {
                    "success":
                        False,

                    "plate":
                        regnr,

                    "error":
                        "timeout",
                }


            except aiohttp.ClientError as error:

                if attempt < MAX_RETRIES:

                    await asyncio.sleep(
                        1
                    )

                    continue

                return {
                    "success":
                        False,

                    "plate":
                        regnr,

                    "error":
                        f"network:{error}",
                }

    return {
        "success":
            False,

        "plate":
            regnr,

        "error":
            "unknown",
    }


# ============================================================
# PROCESS NUMMERPLADE.NET MED LIVE PROGRESS
# ============================================================

async def process_insurance_requests(
    vehicles,
):

    if not vehicles:
        return []

    connector = aiohttp.TCPConnector(
        limit=MAX_CONNECTIONS,
        ttl_dns_cache=300,
    )

    semaphore = asyncio.Semaphore(
        MAX_CONNECTIONS
    )

    results = []

    error_counts = {}

    async with aiohttp.ClientSession(
        connector=connector,
        headers=NUMMERPLADE_HEADERS,
        cookies=NUMMERPLADE_COOKIES,
    ) as session:

        tasks = [

            asyncio.create_task(
                get_insurance_info(
                    session,
                    vehicle,
                    semaphore,
                )
            )

            for vehicle
            in vehicles
        ]

        total = len(
            tasks
        )

        completed = 0
        successful = 0
        failed = 0

        print("")
        print(
            "=========================================="
        )

        print(
            "NUMMERPLADE.NET FORSIKRINGSOPSLAG"
        )

        print(
            "=========================================="
        )

        print(
            f"Starter opslag for "
            f"{total} NYE nummerplader."
        )

        print("")

        for future in asyncio.as_completed(
            tasks
        ):

            result = await future

            completed += 1

            if (
                result
                and
                result.get(
                    "success"
                )
            ):

                successful += 1

                results.append(
                    result
                )

            else:

                failed += 1

                error = (
                    result.get(
                        "error",
                        "unknown"
                    )
                    if result
                    else "unknown"
                )

                error_counts[
                    error
                ] = (
                    error_counts.get(
                        error,
                        0
                    )
                    +
                    1
                )


            if (
                completed
                %
                PROGRESS_EVERY
                ==
                0
                or
                completed
                ==
                total
            ):

                percent = (
                    completed
                    /
                    total
                    *
                    100
                )

                print(
                    f"⏳ "
                    f"{completed}/{total} "
                    f"({percent:.1f}%) | "
                    f"med forsikring: "
                    f"{successful} | "
                    f"fejl/uden data: "
                    f"{failed}"
                )


    if error_counts:

        print("")
        print(
            "Fejlfordeling:"
        )

        for error, count in sorted(
            error_counts.items()
        ):

            print(
                f" - {error}: {count}"
            )


    return results


# ============================================================
# HOVEDPROGRAM
# ============================================================

async def check_new_registrations():

    # ========================================================
    # 1. BILOPSLAG
    # ========================================================

    vehicles = (
        hent_registrerede_koeretoejer()
    )

    if not vehicles:

        print(
            "Ingen køretøjer fundet."
        )

        return


    # ========================================================
    # 2. SUPABASE EXISTING
    # ========================================================

    existing_plates = (
        get_existing_plates_from_supabase()
    )


    # ========================================================
    # 3. KUN NYE NUMMERPLADER
    # ========================================================

    new_vehicles = [

        vehicle

        for vehicle
        in vehicles

        if (
            vehicle[
                "registration"
            ]
            not in
            existing_plates
        )
    ]


    skipped = (
        len(vehicles)
        -
        len(new_vehicles)
    )


    print("")
    print(
        "=========================================="
    )

    print(
        "FILTRERING"
    )

    print(
        "=========================================="
    )

    print(
        f"Fundet hos Bilopslag: "
        f"{len(vehicles)}"
    )

    print(
        f"Allerede behandlet: "
        f"{skipped}"
    )

    print(
        f"NYE nummerplader: "
        f"{len(new_vehicles)}"
    )


    if not new_vehicles:

        print("")
        print(
            "✅ Ingen nye nummerplader."
        )

        print(
            "Der foretages 0 opslag "
            "på Nummerplade.net."
        )

        return


    # ========================================================
    # 4. NUMMERPLADE.NET
    # ========================================================

    results = (
        await process_insurance_requests(
            new_vehicles
        )
    )


    # ========================================================
    # 5. BYG SUPABASE-DATA
    # ========================================================

    entries = []

    plates_data = (
        load_existing_data()
    )


    for result in results:

        plate = result[
            "plate"
        ]

        company = result[
            "company"
        ]

        registration_date = result[
            "registration_date"
        ]

        insurance_status = result[
            "insurance_status"
        ]

        insurance_date = result[
            "insurance_date"
        ]


        # Kun aktive forsikringer
        if (
            insurance_status
            and
            insurance_status.lower()
            not in (
                "aktiv",
                "ukendt",
            )
        ):

            continue


        entry = {

            "company":
                company,

            "plate":
                plate,

            # Vi bruger datoen fra Bilopslags
            # registreringsstatus som hoveddato.
            "date":
                registration_date.isoformat(),

            "checked":
                False,

            "premium":
                0,

            "note":
                "",
        }


        entries.append(
            entry
        )


        # ================================================
        # LOKAL JSON BACKUP
        # ================================================

        if company not in plates_data:

            plates_data[
                company
            ] = []


        existing_local = {

            item.get(
                "plate"
            )

            for item
            in plates_data[
                company
            ]
        }


        if plate not in existing_local:

            plates_data[
                company
            ].append(
                {

                    "plate":
                        plate,

                    "date":
                        registration_date.isoformat(),

                    "checked":
                        False,

                    "premium":
                        0,

                    "note":
                        "",
                }
            )


        print(
            f"✅ {plate} | "
            f"{company} | "
            f"{insurance_status} | "
            f"forsikring siden: "
            f"{insurance_date or 'ukendt'}"
        )


    # ========================================================
    # 6. DEDUP
    # ========================================================

    unique_entries = {}


    for entry in entries:

        key = (
            entry[
                "company"
            ],
            entry[
                "plate"
            ],
        )

        unique_entries[
            key
        ] = entry


    final_entries = list(
        unique_entries.values()
    )


    # ========================================================
    # 7. SUPABASE
    # ========================================================

    uploaded = (
        upload_batch_to_supabase(
            final_entries
        )
    )


    # ========================================================
    # 8. LOKAL JSON
    # ========================================================

    if final_entries:

        save_to_json(
            plates_data
        )


    # ========================================================
    # RESULTAT
    # ========================================================

    print("")
    print(
        "=========================================="
    )

    print(
        "RESULTAT"
    )

    print(
        "=========================================="
    )

    print(
        f"Bilopslag-resultater: "
        f"{len(vehicles)}"
    )

    print(
        f"Allerede behandlet: "
        f"{skipped}"
    )

    print(
        f"Nye nummerplader: "
        f"{len(new_vehicles)}"
    )

    print(
        f"Nummerplade.net med forsikringsdata: "
        f"{len(results)}"
    )

    print(
        f"Sendt/ignoreret i Supabase: "
        f"{uploaded}"
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    print("")
    print(
        "Bilopslag + Nummerplade.net "
        "scraper startet."
    )

    print(
        "Tidspunkt: "
        f"{datetime.now(COPENHAGEN)}"
    )

    delete_old_plates_from_supabase()

    asyncio.run(
        check_new_registrations()
    )

    print("")
    print(
        "Scraper færdig."
    )
