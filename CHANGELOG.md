## [Unreleased]

### Changed
- Clarified Dropbox refresh token retrieval steps in `README.md`
- Expanded `geotiff_scraper.py` documentation
- Cleaned up `check_filetimes.py` imports and ensured file ends with a newline
- Updated `.gitignore` entries for timestamp checks
- Standardized version numbers in the changelog with a `v` prefix
- Switched `hourly_scrape.yml` scheduled to run at the top of every hour
- Output files now include the `HHMM` run time in the filename derived from
  `FileTimes.js`
- Consolidated all `FileTimes.js` parsing into a single function to share both
  the date mapping and last-updated timestamp

## [v0.1.0-beta.1] – 2025-05-05

### Added
- Initial beta release of the HeatRisk GeoTIFF scraper  
- CLI flags for `--base-url`, `--output-dir`, `--days-prefix`, and `--verbose`  
- HTML parsing and retry logic with exponential back-off  
- Extraction of exact forecast dates from `FileTimes.js`  
- Atomic downloads: stream to a temp file, verify `Content-Length`, then atomic move into `forecasts/YYYY/MM/DD/`  
- Metadata date consistency check for each downloaded TIFF using its embedded DateTime tag  
- GitHub Actions workflow for daily scraping and Dropbox upload

### Experimental
- `check_filetimes.py` script and `.github/workflows/filetimes_check.yml` to poll the `heatrisk_updated` timestamp from `FileTimes.json` on the heatrisk site every ~7 minutes and maintain a rolling log (`updates.txt`) in Dropbox
