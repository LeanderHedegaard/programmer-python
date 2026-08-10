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

try:
    BILOPSLAG_COOKIES = json.loads(
        os.getenv(
            "BILOPSLAG_COOKIES_JSON",
            "",
        ) or "{}"
    )
except json.JSONDecodeError:
    print("⚠️ BILOPSLAG_COOKIES_JSON er ikke gyldig JSON.")
    BILOPSLAG_COOKIES = {}


# ============================================================
# INDSTILLINGER
# ============================================================

COPENHAGEN = ZoneInfo("Europe/Copenhagen")

BILOPSLAG_BASE_URL = "https://bilopslag.nu"

MAX_CONNECTIONS = int(
    os.getenv(
        "MAX_CONNECTIONS",
        "12",
    )
)

REQUEST_TIMEOUT = int(
    os.getenv(
        "REQUEST_TIMEOUT",
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
    "accept-language": "da-DK,da;q=0.9,en-US;q=0.8,en;q=0.7",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "referer": "https://bilopslag.nu/avanceret-soegning",
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

    value = str(value).strip()

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

            if isinstance(data, dict):
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
    Beholder i dag + de to foregående kalenderdage.

    Eksempel:
    Hvis i dag er 10/8:
    behold 8/8, 9/8 og 10/8.
    Slet alt før 8/8.
    """

    if (
        not SUPABASE_URL
        or not SUPABASE_SERVICE_ROLE_KEY
    ):
        print(
            "⚠️ Mangler Supabase credentials. "
            "Springer oprydning over."
        )
        return False

    today = datetime.now(
        COPENHAGEN
    ).date()

    cutoff_date = (
        today - timedelta(days=2)
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
                "❌ Supabase-oprydning fejlede: "
                f"{response.status_code} "
                f"{response.text}"
            )
            return False

        print(
            f"🧹 Supabase: slettet plader før {cutoff_date}."
        )

        return True

    except Exception as error:
        print(
            f"❌ Fejl ved Supabase-oprydning: {error}"
        )

        return False


def upload_batch_to_supabase(entries):
    """
    Sender alle fundne plader i én batch.

    Dubletter på company + plate ignoreres.
    """

    if not entries:
        print(
            "ℹ️ Ingen nye/relevante plader at sende."
        )
        return 0

    if (
        not SUPABASE_URL
        or not SUPABASE_SERVICE_ROLE_KEY
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

    try:
        response = requests.post(
            url,
            headers=headers,
            json=entries,
            timeout=45,
        )

        if response.status_code in (
            200,
            201,
            204,
        ):
            print(
                f"✅ Supabase accepterede/ignorerede "
                f"{len(entries)} plader."
            )

            return len(entries)

        print(
            "❌ Supabase batch-upload fejlede: "
            f"{response.status_code} "
            f"{response.text}"
        )

        return 0

    except Exception as error:
        print(
            f"❌ Supabase upload-fejl: {error}"
        )

        return 0


# ============================================================
# BILOPSLAG ADVANCED SEARCH
# ============================================================

def hent_registrerede_koeretoejer():
    """
    Henter biler med registration_status=Registreret
    fra i dag og to dage tilbage.

    Datoerne beregnes automatisk hver gang programmet kører.
    """

    today = datetime.now(
        COPENHAGEN
    ).date()

    from_date = (
        today - timedelta(days=2)
    )

    print("")
    print("==========================================")
    print("HENTER REGISTRERINGER FRA BILOPSLAG")
    print("==========================================")
    print(f"Fra: {from_date.isoformat()}")
    print(f"Til: {today.isoformat()}")

    base_url = (
        f"{BILOPSLAG_BASE_URL}"
        "/api/advanced_search"
    )

    base_params = {
        "registration_status_in[]": "Registreret",
        "registration_status_updated_at_gteq": (
            from_date.isoformat()
        ),
        "registration_status_updated_at_lteq": (
            today.isoformat()
        ),
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
                f"\n🔎 Henter side {page}"
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
                    f"fordelt på {total_pages} sider."
                )

            print(
                f"→ {len(cars)} biler på side {page}"
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

                vehicles[
                    registration
                ] = {
                    "id": vehicle_id,
                    "registration": registration,
                    "vin": str(
                        car.get(
                            "vin",
                            "",
                        )
                    ).upper().strip(),
                    "registration_status_updated_at": (
                        status_date
                    ),
                    "first_registration_date": (
                        parse_date(
                            car.get(
                                "first_registration_date"
                            )
                        )
                    ),
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
                and page >= total_pages
            ):
                break

            page += 1

        except Exception as error:
            print(
                f"❌ Fejl på side {page}: {error}"
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
# FORSIKRING FRA BILOPSLAG
# ============================================================

async def get_insurance_info(
    session,
    vehicle,
    semaphore,
):
    """
    Bruger vehicle.id direkte fra advanced_search.

    Dermed behøver vi IKKE åbne /nummerplade/... først.
    """

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
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": (
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

                if response.status == 403:
                    print(
                        f"⛔ {regnr}: DMR HTTP 403"
                    )
                    return None

                if response.status == 429:
                    print(
                        f"⚠️ {regnr}: DMR HTTP 429"
                    )
                    return None

                if response.status != 200:
                    print(
                        f"⚠️ {regnr}: "
                        f"DMR HTTP {response.status}"
                    )
                    return None

                payload = await response.json(
                    content_type=None
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

                if (
                    not company
                    or company == "Ukendt"
                ):
                    return None

                return {
                    "plate": regnr,
                    "company": company,
                    "insurance_status": (
                        insurance_status
                    ),
                    "insurance_date": (
                        insurance_date
                    ),
                    "registration_status_date": (
                        vehicle[
                            "registration_status_updated_at"
                        ]
                    ),
                }

        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            ValueError,
        ) as error:

            print(
                f"⚠️ Forsikringsfejl "
                f"for {regnr}: {error}"
            )

            return None


# ============================================================
# HOVEDPROGRAM
# ============================================================

async def check_new_registrations():
    vehicles = (
        hent_registrerede_koeretoejer()
    )

    if not vehicles:
        print(
            "Ingen køretøjer fundet."
        )
        return

    print("")
    print(
        f"Starter forsikringsopslag "
        f"for {len(vehicles)} køretøjer."
    )

    connector = aiohttp.TCPConnector(
        limit=MAX_CONNECTIONS,
        ttl_dns_cache=300,
    )

    semaphore = asyncio.Semaphore(
        MAX_CONNECTIONS
    )

    async with aiohttp.ClientSession(
        connector=connector,
        headers=BILOPSLAG_HEADERS,
        cookies=BILOPSLAG_COOKIES,
    ) as session:

        tasks = [
            get_insurance_info(
                session,
                vehicle,
                semaphore,
            )
            for vehicle in vehicles
        ]

        results = await asyncio.gather(
            *tasks
        )

    plates_data = load_existing_data()

    entries = []

    for result in results:
        if not result:
            continue

        plate = result[
            "plate"
        ]

        company = result[
            "company"
        ]

        registration_date = result[
            "registration_status_date"
        ]

        insurance_date = result[
            "insurance_date"
        ]

        insurance_status = result[
            "insurance_status"
        ]

        # Gem registration_status_updated_at som hoveddato.
        # Det matcher den søgning, vi har brugt.
        entry_date = registration_date

        entry = {
            "company": company,
            "plate": plate,
            "date": entry_date.isoformat(),
            "checked": False,
            "premium": 0,
            "note": "",
        }

        entries.append(
            entry
        )

        # Lokal JSON backup
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

        if plate not in existing:
            plates_data[
                company
            ].append(
                {
                    "plate": plate,
                    "date": (
                        entry_date.isoformat()
                    ),
                    "checked": False,
                    "premium": 0,
                    "note": "",
                }
            )

        print(
            f"✅ {plate} | "
            f"{company} | "
            f"registreringsændring: "
            f"{entry_date} | "
            f"forsikring: "
            f"{insurance_status} | "
            f"forsikringsdato: "
            f"{insurance_date or 'ukendt'}"
        )

    # Fjern dubletter inden upload
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

    uploaded = (
        upload_batch_to_supabase(
            final_entries
        )
    )

    if final_entries:
        save_to_json(
            plates_data
        )

    print("")
    print("==========================================")
    print("RESULTAT")
    print("==========================================")

    print(
        f"Køretøjer fra advanced search: "
        f"{len(vehicles)}"
    )

    print(
        f"Med forsikringsselskab: "
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
    print(
        "Bilopslag scraper startet."
    )

    delete_old_plates_from_supabase()

    asyncio.run(
        check_new_registrations()
    )

    print(
        "Bilopslag scraper færdig."
    )
