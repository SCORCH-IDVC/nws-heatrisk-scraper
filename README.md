# [NWS HeatRisk GeoTIFF Scraper](https://github.com/SCORCH-IDVC/nws-heatrisk-scraper)

![Hourly Scrape](https://github.com/SCORCH-IDVC/nws-heatrisk-scraper/actions/workflows/hourly_scrape.yml/badge.svg) ![Timestamp Check](https://github.com/SCORCH-IDVC/nws-heatrisk-scraper/actions/workflows/filetimes_check.yml/badge.svg)

[![Changelog](https://img.shields.io/badge/changelog-📖-blue)](CHANGELOG.md) [![Issues](https://img.shields.io/badge/issues-⚠️-yellow)](https://github.com/SCORCH-IDVC/nws-heatrisk-scraper/issues)

A command-line tool and GitHub Actions workflow to:

- Download the daily 7-day HeatRisk GeoTIFF forecasts from NOAA WPC
- Organize them under `forecasts/YYYY/MM/DD/` with the `HHMM` timestamp from
  `FileTimes.js` embedded in each filename
- Automatically upload each day’s batch to Dropbox

---

## Features

- Uses the site’s own JavaScript mapping (`FileTimes.js`) to get **exact**
  forecast dates and the `heatrisk_updated` timestamp
- Robust retry logic and download-integrity validation  
- Verifies each downloaded TIFF’s internal DateTime tag matches its forecast date  
- Configurable via CLI flags (`--base-url`, `--output-dir`, `--days-prefix`, `--verbose`)  
- Runs on your machine or scheduled hourly via GitHub Actions
- Pushes data off-repo into Dropbox (or any other supported storage)
> **Experimental:**
> - Periodically polls the `heatrisk_updated` timestamp from `FileTimes.json` on the heatrisk site via `check_filetimes.py` and logs it (see `.github/workflows/filetimes_check.yml`).
>     - **Note:** When run locally or in CI, `check_filetimes.py` will create a `filetimes_checks/` directory (git-ignored) to store its rolling `updates.txt` log.
>     - **Goal:** Detect if intraday updates of the heatrisk 7-day forecast are occurring. This will inform future updates of the scraper which is currently running hourly.

---

## Prerequisites

To run the scraper locally:

- **Python 3.8+**  
- A clean virtual environment, then:
  ```bash
  pip install -r requirements.txt
  ```
  where `requirements.txt` contains:
  ```
  requests
  beautifulsoup4
  pillow
  ```

> **Note:** The GitHub Actions workflows install their own dependencies at runtime (`requests`, `beautifulsoup4`, `pillow`, `dropbox`) and system tools (`jq`), so you don’t need to install those locally unless you plan to run the CI steps yourself.

---

## Setup

1. **Clone the repo**  
   ```bash
   git clone https://github.com/SCORCH-IDVC/nws-heatrisk-scraper.git
   cd nws-heatrisk-scraper
   ```

2. **Install dependencies**  
   ```bash
   pip install -r requirements.txt
   ```

3. **Run locally**  
   ```bash
   python geotiff_scraper.py --output-dir forecasts
   ```
   **NOTE:** The following steps are to automate the script on GitHub and store the results in the cloud (Dropbox, in this case).

4. **Configure Dropbox**

   4. **Create a Dropbox App**  
      - Go to https://www.dropbox.com/developers/apps and click **Create App**  
      - Select **Scoped access** → **App folder** → **Create App**  
      - On the App’s settings page, under **Permissions**, enable:
        ```
        files.content.read
        files.content.write
        ```

   5. **Obtain an authorization code**  
      - Copy your **App key** and **App secret** from the App’s settings.  
      - In your browser, navigate to:
        ```
        https://www.dropbox.com/oauth2/authorize?client_id=<APP_KEY>&response_type=code&token_access_type=offline
        ```
        replacing `<APP_KEY>` with your App key.  
      - After you click **Allow**, Dropbox will show you a one-time **authorization code**—copy that value.

   6. **Exchange the code for a refresh token**  
      Open PowerShell (you may need to run it “As Administrator” if scripts are disabled) and paste in these three snippets one by one:

      ```powershell
      # 1. replace these with your actual values
      $authCode  = '<YOUR_AUTHORIZATION_CODE>'
      $appKey    = '<YOUR_APP_KEY>'
      $appSecret = '<YOUR_APP_SECRET>'

      # 2. Exchange for a long-lived refresh token
      $response = Invoke-RestMethod `
        -Uri 'https://api.dropboxapi.com/oauth2/token' `
        -Method Post `
        -ContentType 'application/x-www-form-urlencoded' `
        -Body @{
          code               = $authCode
          grant_type         = 'authorization_code'
          client_id          = $appKey
          client_secret      = $appSecret
          token_access_type  = 'offline'
        }

      # 3. Refresh token will appear here after run. Copy it exactly.
      $refreshToken = $response.refresh_token
      Write-Host "Refresh token:`n$refreshToken"
      ```

      > **Tip:** if you see a policy error, run  
      > ```powershell
      > Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
      > ```

   7. **Store your secrets**  
      In your GitHub repo, go to **Settings → Secrets and variables → Actions** and add:
      ```
      DROPBOX_APP_KEY        = <YOUR_APP_KEY>
      DROPBOX_APP_SECRET     = <YOUR_APP_SECRET>
      DROPBOX_REFRESH_TOKEN  = <THE_REFRESH_TOKEN_FROM_ABOVE>
      ```

5. **GitHub Actions**  
   - **Hourly scrape** (`.github/workflows/hourly_scrape.yml`):
     - Run every hour (top of the hour)
     - Install dependencies  
     - Execute the scraper  
     - Refresh Dropbox access token via your `refresh_token`  
     - Upload the results to your Dropbox App folder  

  - **Experimental monitoring** (`.github/workflows/filetimes_check.yml`):
    - Poll `heatrisk_updated` timestamp from `FileTimes.json` located on the heatrisk site every 7 minutes
     - Install dependencies and refresh Dropbox access token  
     - Pull existing `filetimes_checks/updates.txt` from Dropbox, run `check_filetimes.py`, and upload updated log

---

## Usage

```bash
# Show help
python geotiff_scraper.py --help

# Example with all flags
python geotiff_scraper.py \
  --base-url https://www.wpc.ncep.noaa.gov/heatrisk/data.html \
  --output-dir forecasts \
  --days-prefix Day \
  --verbose
```

---

## Repository File Layout

```
nws-heatrisk-scraper/
├── .github/
│   └── workflows/
│       ├── hourly_scrape.yml
│       └── filetimes_check.yml   ← experimental timestamp check
├── geotiff_scraper.py
├── check_filetimes.py            ← experimental monitoring script
├── requirements.txt
└── CHANGELOG.md
```

> **Note:** both `forecasts/` and `filetimes_checks/` are listed in `.gitignore`.

---

*Last updated: June 9, 2025*
