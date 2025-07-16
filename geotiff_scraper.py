"""GeoTIFF HeatRisk scraper
===========================

This script downloads the seven daily HeatRisk GeoTIFF forecasts from the
NOAA Weather Prediction Center.  It is designed to run either manually or
as part of a scheduled GitHub Actions workflow.

**Key steps**
1. Fetch the HeatRisk data page and read ``FileTimes.js`` to obtain the exact
   forecast dates for labels such as ``"Day 1"`` or ``"Day 2"``.
2. Parse the HTML for links that match those labels.
3. Stream each GeoTIFF to a temporary file, verifying its byte count and
   ensuring a partial download is never kept.
4. Inspect the TIFFTAG_DATETIME metadata in each file to confirm it matches the
   expected forecast date.
5. Rename the verified file into ``forecasts/<year>/<month>/<day>/`` using a
   consistent ``<issue_date>_<HHMM>_<forecast_date>_DayN.tif`` naming scheme
   where ``HHMM`` comes from the ``last_updated`` timestamp in
   ``FileTimes.js``.

The script exposes a small set of CLI flags (``--base-url``, ``--output-dir``,
``--days-prefix`` and ``--verbose``) to customise where data is fetched from and
where it is stored.  All network calls include simple retry logic and logging so
that failures can be diagnosed easily.

Example usage::

    python geotiff_scraper.py --output-dir forecasts --verbose

Dependencies
------------
- ``requests``
- ``beautifulsoup4``
- ``Pillow`` (for TIFF metadata inspection)
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
    """Configure the root logger.

    Parameters
    ----------
    level : int
        Logging level such as ``logging.INFO`` or ``logging.DEBUG``.

    The logger prints timestamps and severity so that other functions can
    simply call ``logging.info`` or ``logging.error``.
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
    """Retrieve HTML from ``url`` with basic retry logic.

    Parameters
    ----------
    session : requests.Session
        Session object with any preconfigured headers.
    url : str
        The page to download.
    retries : int, optional
        How many attempts to make before aborting.
    backoff : float, optional
        Seconds to wait between attempts (multiplied by the attempt number).

    Returns
    -------
    str
        The response text.

    The program exits after the final failed attempt.
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
    """Download ``url`` to ``dest_path`` with validation.

    Parameters
    ----------
    session : requests.Session
        Requests session used for the HTTP GET.
    url : str
        Address of the GeoTIFF file.
    dest_path : Path
        Final location on disk.
    retries : int, optional
        Number of attempts before failing.
    timeout : int, optional
        Request timeout in seconds.
    chunk : int, optional
        Size of the streaming chunks in bytes.

    The file is first written to a temporary location. If the number of bytes
    written does not match ``Content-Length`` (when provided) or any other
    error occurs, the temporary file is removed and the download retried.
    Only after a successful download is the file moved atomically into
    ``dest_path``.
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


def fetch_filetimes(
    session: requests.Session,
    base_url: str,
) -> Tuple[dict[int, date], str]:
    """Retrieve ``FileTimes.js`` once and return the parsed contents.

    Parameters
    ----------
    session : requests.Session
        Session used for the HTTP request.
    base_url : str
        URL of the HeatRisk data page (used to derive the JS location).

    Returns
    -------
    Tuple[dict[int, date], str]
        Mapping of day number to ``datetime.date`` objects and the ``HHMM``
        portion of the ``last_updated`` timestamp.

    The program exits if the file cannot be retrieved or parsed.
    """
    base_path = base_url.rsplit('/', 1)[0]
    url = f"{base_path}/data/FileTimes.js"
    try:
        resp = session.get(url, timeout=10)
        resp.raise_for_status()

        js_text = resp.text

        # file_times mapping
        match = re.search(r"file_times\s*=\s*(\{.*?\});", js_text, re.DOTALL)
        if not match:
            raise ValueError('file_times object not found')
        times = json.loads(match.group(1))
        mapping = {
            int(k): datetime.strptime(v, '%m/%d/%Y').date() for k, v in times.items()
        }

        # last_updated timestamp -> HHMM
        umatch = re.search(r"last_updated\s*=\s*['\"](.*?)['\"]", js_text)
        if not umatch:
            raise ValueError('last_updated not found in FileTimes.js')
        ts = umatch.group(1)
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M UTC")
        run_time = f"{dt.hour:02d}{dt.minute:02d}"

        return mapping, run_time

    except Exception as e:
        logging.error("Error loading FileTimes.js: %s; aborting.", e)
        sys.exit(1)

def parse_geotiff_links(
    html: str,
    file_times: dict,
    prefix: str = 'Day'
) -> List[Tuple[int, date, str]]:
    """Extract GeoTIFF links from the HeatRisk HTML page.

    Parameters
    ----------
    html : str
        Raw HTML of the data page.
    file_times : dict
        Mapping from day number to ``datetime.date`` objects.
    prefix : str, optional
        Text prefix used to identify forecast links.

    Returns
    -------
    List[Tuple[int, date, str]]
        Tuples containing the day number, forecast date and href.

    If a matching date cannot be found for a day label the program exits.
    """
    soup = BeautifulSoup(html, 'html.parser')

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
    """Validate the DateTime metadata of a GeoTIFF.

    Parameters
    ----------
    file_path : Path
        Path to the downloaded file.
    expected_date : date
        Forecast date the file should correspond to.

    The function reads TIFFTAG_DATETIME (tag 306) and compares only the
    date portion. Formats ``YYYY:MM:DD`` and ``YYYY-MM-DD`` are both
    accepted. The program exits if the metadata does not match
    ``expected_date``.
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
    """Run the full scraping workflow.

    Parameters
    ----------
    base_url : str
        HeatRisk page containing the forecast links.
    output_dir : Path
        Root directory where output will be organised.
    days_prefix : str, optional
        Prefix used in link text (``"Day"`` by default).

    The function determines the issue date, downloads each GeoTIFF, verifies
    its metadata and arranges the files into ``output_dir``.
    """
    __version__ = "0.1.0-beta.1"
    session = requests.Session()
    session.headers.update({
    "User-Agent": f"GeoTIFF-Scraper/{__version__}"})


    # Retrieve FileTimes.js once for both date mapping and update time
    file_times, run_time = fetch_filetimes(session, base_url)
    issue_date = file_times[1]
    issue_str = issue_date.isoformat()

    # Build nested year/month/day directory. Each filename embeds the
    # ``last_updated`` ``HHMM`` timestamp so individual runs remain
    # distinguishable.
    year, month, day = issue_str.split('-')

    html = fetch_page(session, base_url)
    links = parse_geotiff_links(html, file_times, prefix=days_prefix)

    issue_dir = output_dir / year / month / day
    issue_dir.mkdir(parents=True, exist_ok=True)

    base_path = base_url.rsplit('/', 1)[0]
    for day_num, forecast_date, href in links:
        file_url = href if href.startswith('http') else f"{base_path}/{href.lstrip('/')}"

        # Compute the final filename including update time
        final_name = (
            f"{issue_str}-{run_time}_{forecast_date.isoformat()}_Day{day_num}.tif"
        )
        final_path = issue_dir / final_name

        # Skip entirely if the final file already exists
        if final_path.exists():
            logging.info("File already exists, skipping download: %s", final_name)
            continue

        # 1) Download into a simple temp placeholder (no final name yet)
        tmp_path = issue_dir / f"Day{day_num}.part"
        download_file(session, file_url, tmp_path)

        # 2) Verify the TIFF’s internal DateTime vs. the expected forecast_date
        verify_geotiff_date(tmp_path, forecast_date)

        # 3) Rename into place now that it’s verified
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
