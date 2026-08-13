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
        "⚠️ BILOPSLAG_COOKIES_JSON er ugyldig JSON."
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

TJEKBIL_BASE_URL = (
    "https://www.tjekbil.dk"
)

# Moderat concurrency med vilje.
# 6 er langt mere skånsomt end 20-40 samtidige requests.
MAX_CONNECTIONS = int(
    os.getenv(
        "MAX_CONNECTIONS",
        "6",
    )
)

REQUEST_TIMEOUT = int(
    os.getenv(
        "REQUEST_TIMEOUT",
        "20",
    )
)

PROGRESS_EVERY = int(
    os.getenv(
        "PROGRESS_EVERY",
        "25",
    )
)

# Lille pause mellem batches.
BATCH_PAUSE_SECONDS = float(
    os.getenv(
        "BATCH_PAUSE_SECONDS",
        "0.5",
    )
)

# Hvis Tjekbil begynder at afvise mange requests,
# stopper vi i stedet for at fortsætte blindt.
MAX_HTTP_ERRORS_BEFORE_ABORT = int(
    os.getenv(
        "MAX_HTTP_ERRORS_BEFORE_ABORT",
        "30",
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
# HEADERS - TJEKBIL
# ============================================================

TJEKBIL_HEADERS = {
    "accept": "*/*",
    "accept-language": (
        "da-DK,da;q=0.9,"
        "en-US;q=0.8,"
        "en;q=0.7"
    ),
    "source": "web",
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
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
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
            (
                f"Bearer "
                f"{SUPABASE_SERVICE_ROLE_KEY}"
            ),

        "Content-Type":
            "application/json",
    }

    if prefer:
        headers[
            "Prefer"
        ] = prefer

    return headers


# ============================================================
# SLET GAMLE PLADER FRA SUPABASE
# ============================================================

def delete_old_plates_from_supabase():
    """
    Beholder kun:
    - i dag
    - i går
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
        today - timedelta(days=1)
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
            f"❌ Supabase-oprydningsfejl: "
            f"{error}"
        )
        return False


# ============================================================
# HENT ALLEREDE BEHANDLEDE PLADER
# ============================================================

def get_existing_plates_from_supabase():
    """
    Henter alle plader som allerede findes i Supabase.

    De springes over før Tjekbil-kaldet.
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
# SUPABASE BATCH UPLOAD
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
                f"❌ Supabase upload-fejl: "
                f"{error}"
            )

    return uploaded


# ============================================================
# BILOPSLAG ADVANCED SEARCH
# ============================================================

def hent_registrerede_koeretoejer():
    """
    Henter alle biler med status Registreret
    fra i dag og i går.
    """

    today = datetime.now(
        COPENHAGEN
    ).date()

    yesterday = (
        today - timedelta(days=1)
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
# HENT FORSIKRING FRA TJEKBIL
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
        f"{TJEKBIL_BASE_URL}"
        f"/api/v3/dmr/regnr/{regnr}"
    )

    headers = {
        **TJEKBIL_HEADERS,

        "referer": (
            f"{TJEKBIL_BASE_URL}"
            f"/nummerplade/{regnr}/overblik"
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

                if response.status != 200:
                    body = await response.text(
                        errors="ignore"
                    )

                    return {
                        "success": False,
                        "plate": regnr,
                        "error": (
                            f"http_{response.status}"
                        ),
                        "body": body[:200],
                    }

                try:
                    payload = await response.json(
                        content_type=None
                    )

                except Exception:
                    body = await response.text(
                        errors="ignore"
                    )

                    return {
                        "success": False,
                        "plate": regnr,
                        "error": "invalid_json",
                        "body": body[:200],
                    }

                extended = (
                    payload.get(
                        "extended",
                        {},
                    )
                    or {}
                )

                insurance = (
                    extended.get(
                        "insurance",
                        {},
                    )
                    or {}
                )

                company = normalize_company(
                    insurance.get(
                        "selskab"
                    )
                )

                status = str(
                    insurance.get(
                        "status",
                        "",
                    )
                ).strip()

                insurance_date = parse_date(
                    insurance.get(
                        "oprettet"
                    )
                )

                if (
                    not company
                    or
                    company == "Ukendt"
                ):
                    return {
                        "success": False,
                        "plate": regnr,
                        "error": "no_company",
                    }

                return {
                    "success": True,
                    "plate": regnr,
                    "company": company,
                    "insurance_status":
                        status,
                    "insurance_date":
                        insurance_date,
                    "registration_date":
                        vehicle[
                            "status_date"
                        ],
                }

        except asyncio.TimeoutError:
            return {
                "success": False,
                "plate": regnr,
                "error": "timeout",
            }

        except aiohttp.ClientError as error:
            return {
                "success": False,
                "plate": regnr,
                "error": (
                    f"network:{error}"
                ),
            }

        except Exception as error:
            return {
                "success": False,
                "plate": regnr,
                "error": (
                    f"{type(error).__name__}: "
                    f"{error}"
                ),
            }


# ============================================================
# TJEKBIL PROCESSERING MED LIVE STATUS
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

    total = len(
        vehicles
    )

    completed = 0
    successful = 0
    failed = 0
    http_error_total = 0

    print("")
    print(
        "=========================================="
    )
    print(
        "TJEKBIL FORSIKRINGSOPSLAG"
    )
    print(
        "=========================================="
    )

    print(
        f"Starter opslag for "
        f"{total} NYE nummerplader."
    )

    print(
        f"Concurrency: {MAX_CONNECTIONS}"
    )

    print("")

    async with aiohttp.ClientSession(
        connector=connector,
    ) as session:

        # Kør i små batches, så vi ikke sender
        # tusindvis af requests på én gang.
        batch_size = (
            MAX_CONNECTIONS * 4
        )

        for batch_number, batch in enumerate(
            chunks(
                vehicles,
                batch_size,
            ),
            start=1,
        ):
            tasks = [
                asyncio.create_task(
                    get_insurance_info(
                        session,
                        vehicle,
                        semaphore,
                    )
                )
                for vehicle in batch
            ]

            batch_results = (
                await asyncio.gather(
                    *tasks
                )
            )

            for result in batch_results:
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
                            "unknown",
                        )
                        if result
                        else "unknown"
                    )

                    error_counts[
                        error
                    ] = (
                        error_counts.get(
                            error,
                            0,
                        )
                        + 1
                    )

                    if str(
                        error
                    ).startswith(
                        "http_"
                    ):
                        http_error_total += 1

            if (
                completed % PROGRESS_EVERY == 0
                or
                completed >= total
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

            if (
                http_error_total
                >=
                MAX_HTTP_ERRORS_BEFORE_ABORT
                and
                successful == 0
            ):
                print("")
                print(
                    "⛔ Stopper Tjekbil-opslag."
                )

                print(
                    "Der er kommet for mange "
                    "HTTP-fejl uden ét eneste "
                    "vellykket opslag."
                )

                break

            # Skånsom pause mellem batches.
            if (
                completed < total
            ):
                await asyncio.sleep(
                    BATCH_PAUSE_SECONDS
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
    # 1. FIND REGISTRERINGER
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
    # 2. FIND ALLEREDE BEHANDLEDE
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
            "Der foretages 0 "
            "Tjekbil-opslag."
        )
        return


    # ========================================================
    # 4. FORSIKRING VIA TJEKBIL
    # ========================================================

    results = (
        await process_insurance_requests(
            new_vehicles
        )
    )


    # ========================================================
    # 5. BYG SUPABASE DATA
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

        insurance_status = (
            result[
                "insurance_status"
            ]
        )

        insurance_date = (
            result[
                "insurance_date"
            ]
        )

        registration_date = (
            result[
                "registration_date"
            ]
        )

        # Kun aktive forsikringer.
        if (
            insurance_status
            and
            insurance_status.lower()
            !=
            "aktiv"
        ):
            continue

        entry = {
            "company":
                company,

            "plate":
                plate,

            # Brug registreringsstatus-datoen
            # fra Bilopslag som date.
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

        # --------------------------------------------
        # LOKAL JSON
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
        f"Tjekbil med forsikringsdata: "
        f"{len(results)}"
    )

    print(
        f"Aktive forsikringer klar "
        f"til Supabase: "
        f"{len(final_entries)}"
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
        "Bilopslag + Tjekbil scraper startet."
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
