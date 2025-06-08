#!/usr/bin/env python3
"""
check_filetimes.py

Fetch the 'heatrisk_updated' timestamp from NOAA HeatRisk FileTimes.json
and record it in a single rolling log file under 'filetimes_checks/'.

Usage:
    python check_filetimes.py [--output-dir DIR]

Requires:
    pip install requests
"""

import argparse
import logging
import os
import sys
import requests

URL = "https://www.wpc.ncep.noaa.gov/heatrisk/data/FileTimes.json"


def fetch_heatrisk_timestamp(url: str) -> str:
    """GET the JSON and return the 'heatrisk_updated' field."""
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    ts = data.get("heatrisk_updated")
    if not ts:
        raise KeyError("'heatrisk_updated' not found in JSON payload")
    return ts


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="Check NOAA HeatRisk FileTimes.json updated timestamp"
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="filetimes_checks",
        help="Local folder to store updates.txt"
    )
    args = parser.parse_args()

    # 1) Fetch the timestamp
    try:
        ts = fetch_heatrisk_timestamp(URL)
        logging.info("Fetched timestamp: %s", ts)
    except Exception as e:
        logging.error("Failed to fetch timestamp: %s", e)
        sys.exit(1)

    # 2) Ensure output directory exists
    try:
        os.makedirs(args.output_dir, exist_ok=True)
    except OSError as e:
        logging.error("Cannot create output directory %r: %s", args.output_dir, e)
        sys.exit(1)

    # 3) Record in a single rolling log (filetimes_checks/updates.txt)
    logpath = os.path.join(args.output_dir, "updates.txt")

    #    a) read any existing entries
    seen = set()
    if os.path.exists(logpath):
        with open(logpath, "r") as f:
            seen = { line.strip() for line in f }

    #    b) append only if this timestamp is new
    if ts in seen:
        logging.info("Timestamp %r already recorded; nothing to do.", ts)
    else:
        with open(logpath, "a") as f:
            f.write(ts + "\n")
        logging.info("Appended new timestamp %r to %s", ts, logpath)

    # 4) Echo the path so the workflow can pick it up
    print(logpath)


if __name__ == "__main__":
    main()
