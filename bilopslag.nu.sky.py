import asyncio
import aiohttp
import os
import re
import json
import requests

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path


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

# Antal samtidige forsikringsopslag
MAX_CONNECTIONS = int(
    os.getenv(
        "MAX_CONNECTIONS",
        "20",
    )
)

REQUEST_TIMEOUT = int(
    os.getenv(
        "REQUEST_TIMEOUT",
        "20",
    )
)

# Vis status efter hver 25 behandlede
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
# HEADERS
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
    Beholder KUN:

    - i dag
    - i går

    Hvis programmet fx kører 10. august:

    behold:
        9. august
        10. august

    slet:
        alt før 9. august
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
            f"   Beholder kun {yesterday} "
            f"og {today}."
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
    Henter nummerplader som allerede findes
    i Supabase.

    Disse plader springes over FØR
    forsikringsopslag.

    Det er afgørende for hastigheden.
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
        "nummerplader fra Supabase..."
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
                    "⚠️ Kunne ikke hente "
                    "eksisterende plader: "
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

    # Send højst 250 ad gangen
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
                "❌ Supabase batch-upload "
                "fejlede: "
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
# ADVANCED SEARCH
# ============================================================

def hent_registrerede_koeretoejer():
    """
    Henter registreringer fra:

    I DAG
    +
    I GÅR

    Datoerne beregnes automatisk.
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

            # --------------------------------------------
            # FØRSTE SIDE
            # --------------------------------------------

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

            # --------------------------------------------
            # BILER
            # --------------------------------------------

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

                vehicle_id = car.get(
                    "id"
                )

                if not vehicle_id:
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

                # Ekstra sikkerhed:
                # Accepter kun i dag/i går.

                if status_date not in (
                    today,
                    yesterday,
                ):
                    continue

                vehicles[
                    registration
                ] = {

                    "id":
                        vehicle_id,

                    "registration":
                        registration,

                    "vin":
                        str(
                            car.get(
                                "vin",
                                "",
                            )
                        ).upper().strip(),

                    "registration_status_updated_at":
                        status_date,

                    "first_registration_date":
                        parse_date(
                            car.get(
                                "first_registration_date"
                            )
                        ),
                }

            # --------------------------------------------
            # PAGINATION
            # --------------------------------------------

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
        "unikke registrerede køretøjer "
        "fra i dag/i går."
    )

    return result


# ============================================================
# FORSIKRING
# ============================================================

async def get_insurance_info(
    session,
    vehicle,
    semaphore,
):

    regnr = vehicle[
        "registration"
    ]

    vehicle_id = vehicle[
        "id"
    ]

    url = (
        f"{BILOPSLAG_BASE_URL}"
        f"/api/statistics/vehicles/"
        f"{vehicle_id}/dmr"
    )

    headers = {

        "Accept":
            "application/json",

        "X-Requested-With":
            "XMLHttpRequest",

        "Referer":
            (
                f"{BILOPSLAG_BASE_URL}"
                f"/nummerplade/{regnr}"
            ),
    }

    async with semaphore:

        try:

            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(
                    total=REQUEST_TIMEOUT
                ),
            ) as response:

                # ----------------------------------------
                # HTTP FEJL
                # ----------------------------------------

                if response.status != 200:

                    return {
                        "success": False,

                        "plate":
                            regnr,

                        "http_status":
                            response.status,
                    }

                # ----------------------------------------
                # JSON
                # ----------------------------------------

                payload = (
                    await response.json(
                        content_type=None
                    )
                )

                dmr_data = (
                    payload.get(
                        "dmr_data",
                        {},
                    )
                    or {}
                )

                company = normalize_company(
                    dmr_data.get(
                        "insurance_company"
                    )
                )

                insurance_status = str(
                    dmr_data.get(
                        "insurance_status",
                        "",
                    )
                ).strip()

                insurance_date = parse_date(
                    dmr_data.get(
                        "insurance_created_at"
                    )
                )

                return {

                    "success":
                        (
                            company
                            !=
                            "Ukendt"
                        ),

                    "plate":
                        regnr,

                    "company":
                        company,

                    "insurance_status":
                        insurance_status,

                    "insurance_date":
                        insurance_date,

                    "registration_status_date":
                        vehicle[
                            "registration_status_updated_at"
                        ],
                }

        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            ValueError,
        ):

            return {
                "success": False,

                "plate":
                    regnr,

                "http_status":
                    "error",
            }


# ============================================================
# FORSIKRINGSOPSLAG MED LIVE STATUS
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

    async with aiohttp.ClientSession(
        connector=connector,
        headers=BILOPSLAG_HEADERS,
        cookies=BILOPSLAG_COOKIES,
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
            "FORSIKRINGSOPSLAG"
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

            # --------------------------------------------
            # LIVE PROGRESS
            # --------------------------------------------

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
                    f"fejl/uden selskab: "
                    f"{failed}"
                )

    return results


# ============================================================
# HOVEDPROGRAM
# ============================================================

async def check_new_registrations():

    # ========================================================
    # 1. ADVANCED SEARCH
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
    # 2. HENT ALLEREDE BEHANDLEDE
    # ========================================================

    existing_plates = (
        get_existing_plates_from_supabase()
    )


    # ========================================================
    # 3. FJERN ALLEREDE BEHANDLEDE
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


    # ========================================================
    # STATUS
    # ========================================================

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


    # ========================================================
    # INTET NYT?
    # ========================================================

    if not new_vehicles:

        print("")

        print(
            "✅ Ingen nye nummerplader."
        )

        print(
            "Der foretages derfor "
            "0 forsikringsrequests."
        )

        return


    # ========================================================
    # 4. FORSIKRING
    # ========================================================

    results = (
        await process_insurance_requests(
            new_vehicles
        )
    )


    # ========================================================
    # 5. BYG DATA
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

        entry_date = result[
            "registration_status_date"
        ]


        entry = {

            "company":
                company,

            "plate":
                plate,

            "date":
                entry_date.isoformat(),

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


        # --------------------------------------------
        # LOKAL JSON BACKUP
        # --------------------------------------------

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


        if (
            plate
            not in
            existing_local
        ):

            plates_data[
                company
            ].append(
                {

                    "plate":
                        plate,

                    "date":
                        entry_date.isoformat(),

                    "checked":
                        False,

                    "premium":
                        0,

                    "note":
                        "",
                }
            )


    # ========================================================
    # 6. FJERN DUBLETTER
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
        f"Med forsikringsselskab: "
        f"{len(final_entries)}"
    )

    print(
        f"Uploadet/ignoreret i Supabase: "
        f"{uploaded}"
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    print("")
    print(
        "Bilopslag scraper startet."
    )

    print(
        f"Tidspunkt: "
        f"{datetime.now(COPENHAGEN)}"
    )

    # Fjern data ældre end i går
    delete_old_plates_from_supabase()

    # Kør
    asyncio.run(
        check_new_registrations()
    )

    print("")
    print(
        "Bilopslag scraper færdig."
    )
