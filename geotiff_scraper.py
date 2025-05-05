"""
geotiff_scraper.py

A command-line tool to download GeoTIFF heat risk forecasts from the NOAA Weather Prediction Center.

Features:
- Fetches the HeatRisk data page and extracts forecast dates from the `FileTimes.js` mapping.
- Parses links labeled "Day N" and uses the `file_times` mapping for exact dates, aborting on any missing mapping.
- Retries network calls with exponential back-off and validates every
  download (temp file, byte-count check, atomic move) so no partial GeoTIFFs
  are kept.
- Organizes downloads into nested year/month/day folders, each day folder
  holding its 7 GeoTIFFs named with the issue date, forecast date, and day
  offset.
- Verifies each downloaded TIFF’s internal DateTime tag matches the filename’s forecast date.
- Configurable via CLI flags.

Dependencies:
- requests
- beautifulsoup4
- PIL

Usage:
    python geotiff_scraper.py [--base-url URL] [--output-dir DIR] [--days-prefix PREFIX] [--verbose]

Examples:
    # Basic usage
    python geotiff_scraper.py

    # Specify custom output directory and enable debug logging
    python geotiff_scraper.py --output-dir /data/forecasts --verbose
"""
import argparse
import tempfile
import shutil
import logging
import os
import re
import sys
import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import List, Tuple

import requests
from bs4 import BeautifulSoup
from PIL import Image 


def setup_logger(level: int = logging.INFO) -> None:
    """
    Configure the root logger format and level.
    """
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(message)s",
        level=level,
    )


def fetch_page(
    session: requests.Session,
    url: str,
    retries: int = 3,
    backoff: float = 1.0
) -> str:
    """
    Retrieve HTML content from a URL with retry logic. Aborts on repeated failures.
    """
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=10)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            logging.warning("Attempt %d: Failed to fetch %s: %s", attempt, url, e)
            if attempt < retries:
                time.sleep(backoff * attempt)
    logging.error("Exceeded retries fetching %s; aborting.", url)
    sys.exit(1)


def download_file(
    session: requests.Session,
    url: str,
    dest_path: Path,
    retries: int = 3,
    timeout: int = 30,
    chunk: int = 64 * 1024,      # 64 KB chunks
) -> None:
    """
    Stream url to a temporary file, verify byte-count, then atomically move
    it to dest_path.

    A partial download is never left in forecasts/. If any check fails,
    the temp file is deleted and the function retries (up to retries times).
    """
    for attempt in range(1, retries + 1):
        tmp_file = None
        try:
            with session.get(url, stream=True, timeout=timeout) as resp:
                resp.raise_for_status()
                expected = int(resp.headers.get("Content-Length", 0))

                # Write to a temp file first
                with tempfile.NamedTemporaryFile(delete=False, suffix=".tif") as tmp:
                    tmp_file = Path(tmp.name)
                    written = 0
                    for piece in resp.iter_content(chunk_size=chunk):
                        if piece:               # skip keep-alive chunks
                            tmp.write(piece)
                            written += len(piece)

                # Byte-count check (skipped if header absent)
                if expected and written != expected:
                    raise IOError(
                        f"incomplete download: {written}/{expected} bytes"
                    )

                # Passed all checks – move into place atomically
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(tmp_file, dest_path)
                logging.info("✓ Downloaded %s (%d bytes)", dest_path.name, written)
                return

        except (requests.RequestException, IOError) as exc:
            logging.warning(
                "Attempt %d/%d failed for %s – %s",
                attempt, retries, url, exc
            )
            if tmp_file and tmp_file.exists():
                tmp_file.unlink(missing_ok=True)
            if attempt < retries:
                time.sleep(attempt)        # simple back-off
            else:
                logging.error("Giving up on %s after %d retries", url, retries)
                raise


def extract_file_times(
    session: requests.Session,
    base_url: str
) -> dict:
    """
    Fetch FileTimes.js and parse the `file_times` mapping: day => MM/DD/YYYY string.
    Aborts if the file cannot be parsed.
    """
    base_path = base_url.rsplit('/', 1)[0]
    url = f"{base_path}/data/FileTimes.js"
    try:
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        match = re.search(r"file_times\s*=\s*(\{.*?\});", resp.text, re.DOTALL)
        if not match:
            raise ValueError('file_times object not found')
        times = json.loads(match.group(1))
        return {int(k): datetime.strptime(v, '%m/%d/%Y').date() for k, v in times.items()}
    except Exception as e:
        logging.error("Error loading FileTimes.js: %s; aborting.", e)
        sys.exit(1)

def parse_geotiff_links(
    session: requests.Session,
    base_url: str,
    html: str,
    prefix: str = 'Day'
) -> List[Tuple[int, date, str]]:
    """
    Parse HTML to extract GeoTIFF links labeled with "Day N" and map to forecast dates via file_times.
    Aborts if any expected mapping is missing.
    """
    soup = BeautifulSoup(html, 'html.parser')
    file_times = extract_file_times(session, base_url)

    geotiffs: List[Tuple[int, date, str]] = []
    pattern = re.compile(rf"^{prefix}\s*(\d+)", re.IGNORECASE)

    for link in soup.find_all('a', href=True):
        href = link['href']
        label = ' '.join(link.stripped_strings)

        if not href.lower().endswith('.tif'):
            continue

        m = pattern.match(label)
        if not m:
            continue

        day_num = int(m.group(1))
        if day_num not in file_times:
            logging.error("No date mapping for Day %d; aborting.", day_num)
            sys.exit(1)

        forecast_date = file_times[day_num]
        geotiffs.append((day_num, forecast_date, href))

    return geotiffs

def verify_geotiff_date(file_path: Path, expected_date: date) -> None:
    """
    Open the GeoTIFF and check its DateTime tag (ID 306) matches expected_date.
    Supports both 'YYYY:MM:DD hh:mm:ss' and 'YYYY-MM-DD' tag values.
    Exits if they differ.
    """
    try:
        with Image.open(file_path) as img:
            tags = img.tag_v2
            raw = tags.get(306)  # TIFFTAG_DATETIME
            if not raw:
                logging.warning(
                    "No DateTime tag in %s; skipping metadata check",
                    file_path.name
                )
                return

            # decode bytes if needed, strip whitespace
            dt_str = raw.decode() if isinstance(raw, bytes) else raw
            dt_str = dt_str.strip().split()[0]  # take only the date part

            # choose the right format
            if '-' in dt_str:
                fmt = "%Y-%m-%d"
            else:
                fmt = "%Y:%m:%d"

            meta_date = datetime.strptime(dt_str, fmt).date()

            if meta_date != expected_date:
                logging.error(
                    "Metadata date mismatch for %s: saw %s, expected %s",
                    file_path.name, meta_date, expected_date
                )
                sys.exit(1)

            logging.info(
                "Metadata date OK for %s: %s",
                file_path.name, meta_date
            )

    except Exception as e:
        logging.error("Failed to verify metadata for %s: %s", file_path.name, e)
        sys.exit(1)

def main(
    base_url: str,
    output_dir: Path,
    days_prefix: str = 'Day'
) -> None:
    """
    Orchestrate fetching page, parsing links, and downloading GeoTIFFs into nested folders.
    """
    __version__ = "0.1.0-beta.1"
    session = requests.Session()
    session.headers.update({
    "User-Agent": f"GeoTIFF-Scraper/{__version__}"})

    # Determine run (issue) date
    file_times = extract_file_times(session, base_url)
    issue_date = file_times[1]
    issue_str = issue_date.isoformat()
    
    # Build nested year/month/day directory for organization
    year, month, day = issue_str.split('-')

    html = fetch_page(session, base_url)
    links = parse_geotiff_links(session, base_url, html, prefix=days_prefix)

    issue_dir = output_dir / year / month / day
    issue_dir.mkdir(parents=True, exist_ok=True)

    base_path = base_url.rsplit('/', 1)[0]
    for day_num, forecast_date, href in links:
        file_url = href if href.startswith('http') else f"{base_path}/{href.lstrip('/')}"

        # 1) Download into a simple temp placeholder (no final name yet)
        tmp_path = issue_dir / f"Day{day_num}.part"
        download_file(session, file_url, tmp_path)

        # 2) Verify the TIFF’s internal DateTime vs. the expected forecast_date
        verify_geotiff_date(tmp_path, forecast_date)

        # 3) Compute the final filename (issue date, forecast date, and day number)
        final_name = f"{issue_str}_{forecast_date.isoformat()}_Day{day_num}.tif"
        final_path = issue_dir / final_name

        # If it already exists, remove the temp and skip
        if final_path.exists():
            logging.info("Final file already exists, removing temp and skipping: %s", final_name)
            tmp_path.unlink(missing_ok=True)
            continue

        # 4) Rename into place now that it’s verified
        tmp_path.replace(final_path)
        logging.info("Saved verified file as %s", final_name)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Download NWS HeatRisk GeoTIFF forecasts')
    parser.add_argument('--base-url', default='https://www.wpc.ncep.noaa.gov/heatrisk/data.html')
    parser.add_argument('--output-dir', type=Path, default=Path(os.getcwd()) / 'forecasts')
    parser.add_argument('--days-prefix', default='Day')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()

    setup_logger(logging.DEBUG if args.verbose else logging.INFO)
    main(args.base_url, args.output_dir, args.days_prefix)
