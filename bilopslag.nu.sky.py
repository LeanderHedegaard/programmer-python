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


# ============================================================
# SUPABASE
# ============================================================

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
# NUMMERPLADE.NET COOKIES - VALGFRIT
# ============================================================

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
# INDSTILLINGER
# ============================================================

COPENHAGEN = ZoneInfo(
    "Europe/Copenhagen"
)

NUMMERPLADE_BASE_URL = (
    "https://www.nummerplade.net/nummerplade"
)

MAX_CONNECTIONS = int(
    os.getenv(
        "MAX_CONNECTIONS",
        "8",
    )
)

MAX_PAGES = int(
    os.getenv(
        "MAX_PAGES",
        "50",
    )
)

PLADE_REGEX = r"^[A-Z]{2}\d{3,5}$"


# ============================================================
# BILOPSLAG HEADERS
# ============================================================

BILOPSLAG_HEADERS = {
    "accept": "*/*",

    "accept-language": (
        "da,en-US;q=0.9,"
        "en;q=0.8,"
        "es;q=0.7"
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
        "Chrome/150.0.0.0 "
        "Safari/537.36"
    ),
}


# ============================================================
# NUMMERPLADE.NET HEADERS
# ============================================================

NUMMERPLADE_HEADERS = {
    "Accept": (
        "text/html,"
        "application/xhtml+xml,"
        "application/xml;q=0.9,"
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

def parse_date(value):
    if not value:
        return None

    value = value.strip()

    for fmt in (
        "%d-%m-%Y",
        "%Y-%m-%d",
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
        value.split()
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
        ) as f:

            data = json.load(f)

            return (
                data
                if isinstance(data, dict)
                else {}
            )

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
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=4,
            sort_keys=True,
        )


# ============================================================
# SLET GAMLE PLADER FRA SUPABASE
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


    try:

        response = requests.delete(
            url,
            headers=headers,
            timeout=20,
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
            "🧹 Plader med date før "
            f"{cutoff_date} "
            "er slettet fra Supabase."
        )

        return True


    except Exception as e:

        print(
            "❌ Fejl ved oprydning "
            f"i Supabase: {e}"
        )

        return False


# ============================================================
# UPLOAD TIL SUPABASE
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
            "⚠️ Mangler SUPABASE_URL "
            "eller SUPABASE_SERVICE_ROLE_KEY."
        )

        return False


    url = (
        f"{SUPABASE_URL}"
        "/rest/v1/plates"
        "?on_conflict=company,plate"
    )


    payload = {
        "company": company,

        "plate": (
            entry["plate"]
        ),

        "date": (
            entry["date"]
        ),

        "checked": (
            entry.get(
                "checked",
                False,
            )
        ),

        "premium": (
            entry.get(
                "premium",
                0,
            )
        ),

        "note": (
            entry.get(
                "note",
                "",
            )
        ),
    }


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

        "Prefer": (
            "resolution=ignore-duplicates,"
            "return=minimal"
        ),
    }


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

            print(
                "✅ Uploadet/ignoreret i "
                f"Supabase: {company} | "
                f"{entry['plate']}"
            )

            return True


        if response.status_code == 409:

            print(
                "ℹ️ Findes allerede i "
                f"Supabase: {company} | "
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
            "❌ Fejl ved Supabase upload: "
            f"{e}"
        )

        return False


# ============================================================
# HENT NUMMERPLADER FRA BILOPSLAG
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


    plader_og_stel = []


    for page in range(
        1,
        MAX_PAGES + 1,
    ):

        url = base_url.format(
            dato=today_str,
            side=page,
        )


        print(
            f"\n🔎 Henter Bilopslag "
            f"side {page}"
        )


        try:

            resp = requests.get(
                url,
                headers=BILOPSLAG_HEADERS,
                cookies=BILOPSLAG_COOKIES,
                timeout=20,
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
                [],
            )


            print(
                "→ Antal biler på "
                f"side {page}: "
                f"{len(biler)}"
            )


            if not biler:

                print(
                    "Ingen flere biler. "
                    "Stopper Bilopslag."
                )

                break


            for bil in biler:

                plade = str(
                    bil.get(
                        "registration",
                        "",
                    )
                ).upper().strip()


                vin = str(
                    bil.get(
                        "vin",
                        "",
                    )
                ).upper().strip()


                if (
                    not plade
                    or
                    not vin
                ):
                    continue


                if re.match(
                    PLADE_REGEX,
                    plade,
                ):

                    plader_og_stel.append(
                        (
                            plade,
                            vin,
                        )
                    )


        except Exception as e:

            print(
                f"⚠️ Fejl på Bilopslag "
                f"side {page}: {e}"
            )

            break


    # Fjern dubletter
    unique = {}

    for plade, vin in plader_og_stel:

        unique[
            plade
        ] = vin


    result = list(
        unique.items()
    )


    print(
        "\n🎯 Bilopslag fandt "
        f"{len(result)} "
        "unikke gyldige plader."
    )


    return result


# ============================================================
# PARSE FORSIKRING FRA DEN NYE NUMMERPLADE.NET SIDE
# ============================================================

def extract_insurance_from_html(
    html,
):

    soup = BeautifulSoup(
        html,
        "html.parser",
    )


    # ========================================================
    # 1. NY FORSIKRINGSBOKS
    # ========================================================

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
            parse_date(
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


    # ========================================================
    # 2. FALLBACK TIL FORSIKRINGSHISTORIK
    # ========================================================

    history_rows = soup.select(
        "#forsikring-card .fa-rk"
    )


    for row in history_rows:

        status_element = (
            row.select_one(
                ".fa-stat"
            )
        )


        status = (
            status_element.get_text(
                " ",
                strip=True,
            ).lower()
            if status_element
            else ""
        )


        # Brug den aktive forsikring
        if (
            status
            and
            "aktiv" not in status
        ):
            continue


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


        company = normalize_company(
            company_element.get_text(
                " ",
                strip=True,
            )
            if company_element
            else ""
        )


        insurance_date = (
            parse_date(
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


    # ========================================================
    # 3. FALLBACK TIL KPI-BOKSEN
    # ========================================================

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
# NY GET_INSURANCE_INFO
# ============================================================

async def get_insurance_info(
    session,
    regnr,
):

    url = (
        f"{NUMMERPLADE_BASE_URL}/"
        f"{regnr.lower()}.html"
    )


    try:

        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(
                total=25
            ),
            allow_redirects=True,
        ) as response:


            if response.status == 404:

                print(
                    f"ℹ️ {regnr}: "
                    "ikke fundet på "
                    "nummerplade.net."
                )

                return (
                    "Ukendt",
                    None,
                )


            if response.status == 403:

                print(
                    f"⛔ {regnr}: "
                    "nummerplade.net "
                    "returnerede HTTP 403."
                )

                return (
                    "Ukendt",
                    None,
                )


            if response.status == 429:

                print(
                    f"⚠️ {regnr}: "
                    "HTTP 429 rate limit."
                )

                return (
                    "Ukendt",
                    None,
                )


            if response.status != 200:

                print(
                    f"⚠️ {regnr}: "
                    f"HTTP {response.status}"
                )

                return (
                    "Ukendt",
                    None,
                )


            html = await response.text(
                errors="ignore"
            )


            return (
                extract_insurance_from_html(
                    html
                )
            )


    except (
        aiohttp.ClientError,
        asyncio.TimeoutError,
    ) as e:

        print(
            "⚠️ Fejl ved "
            "forsikringsopslag for "
            f"{regnr}: {e}"
        )

        return (
            "Ukendt",
            None,
        )


# ============================================================
# PROCESS PLATE
# ============================================================

async def process_plate(
    session,
    regnr,
    stelnr,
    plates_data,
    uploaded_plates,
    semaphore,
):

    async with semaphore:

        # VIGTIG ÆNDRING:
        #
        # Vi sender nu REGNR og ikke stelnr,
        # fordi forsikringen hentes direkte
        # fra nummerpladens HTML-side.

        selskab, forsikringsdato = (
            await get_insurance_info(
                session,
                regnr,
            )
        )


        if (
            not selskab
            or
            selskab == "Ukendt"
        ):

            print(
                f"Springer {regnr} over - "
                "intet forsikringsselskab "
                "fundet."
            )

            return


        # Bilopslag-queryen indeholder allerede
        # first_registration_date_gteq=i dag,
        # så vi bruger dags dato på vores entry.

        today = datetime.now(
            COPENHAGEN
        ).date()


        dato = today.strftime(
            "%Y-%m-%d"
        )


        entry = {
            "date": dato,

            "plate": regnr,

            "checked": False,

            "premium": 0,

            "note": "",
        }


        if selskab not in plates_data:

            plates_data[
                selskab
            ] = []


        existing_plates_local = {
            p.get(
                "plate"
            )
            for p in plates_data.get(
                selskab,
                [],
            )
        }


        if (
            regnr
            not in existing_plates_local
        ):

            plates_data[
                selskab
            ].append(
                entry
            )


        ok = upload_plate_to_supabase(
            selskab,
            entry,
        )


        if ok:

            uploaded_plates.add(
                regnr
            )


            insurance_date_text = (
                forsikringsdato.isoformat()
                if forsikringsdato
                else "ukendt"
            )


            print(
                f"✅ Behandlet: "
                f"{regnr} | "
                f"{selskab} | "
                "forsikringsdato: "
                f"{insurance_date_text}"
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


    plader_og_stel = (
        hent_plaader_fra_bilopslag()
    )


    if not plader_og_stel:

        print(
            "Ingen biler fundet "
            "i Bilopslag."
        )

        return


    print(
        "Starter forsikringsopslag "
        f"for {len(plader_og_stel)} "
        "plader."
    )


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
        headers=NUMMERPLADE_HEADERS,
        cookies=NUMMERPLADE_COOKIES,
    ) as session:


        tasks = [

            process_plate(
                session,
                regnr,
                stelnr,
                plates_data,
                uploaded_plates,
                semaphore,
            )

            for regnr, stelnr
            in plader_og_stel
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
        f"{len(plader_og_stel)} plader."
    )


    print(
        "Uploadet/ignoreret "
        "i Supabase: "
        f"{len(uploaded_plates)} plader."
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
