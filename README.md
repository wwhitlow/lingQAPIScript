# LingQ Daily Import Helper

This project includes a script to:
- pull article content from a web page URL,
- extract readable text,
- generate LingQ-ready lesson payload files,
- optionally upload the lesson directly to LingQ.

## Credential Retrieval

The API Key for your LingQ account can be found at this url: https://www.lingq.com/en/accounts/apikey/

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Basic usage (prepare files only)

```bash
python lingq_daily_import.py --url "https://example.com/article"
```

Output files are saved in `./imports/`:
- `YYYYMMDD-<title>.txt`
- `YYYYMMDD-<title>.payload.json`

## Direct LingQ upload

Set your credentials:

```bash
export LINGQ_API_KEY="your-token"
export LINGQ_LANGUAGE="en"
export LINGQ_COLLECTION_ID="123456"
```

Run upload:

```bash
python lingq_daily_import.py --url "https://example.com/article" --upload
```

Optional flags:
- `--title "Custom Lesson Title"`
- `--collection 123456`
- `--language en`
- `--source-lang es`
- `--accept-language "es-ES,es;q=0.9"`
- `--out-dir ./imports`
- `--min-words 120`

## iBreviary Spanish example

For iBreviary, force Spanish both in query params and request headers:

```bash
python lingq_daily_import.py \
  --url "https://www.ibreviary.com/m2/breviario.php?s=ufficio_delle_letture&b=1" \
  --source-lang es \
  --accept-language "es-ES,es;q=0.9" \
  --title "Oficio de Lecturas - Diario"
```

This makes the fetch URL include `lang=es`, which iBreviary supports.

## Daily automation examples

### Cron (every day at 7:00 AM)

```bash
0 7 * * * cd {DIRECTORY}/lingQAutoScrapper && {DIRECTORY}/lingQAutoScrapper/.venv/bin/python lingq_daily_import.py --url "https://example.com/daily-reading" --upload >> /tmp/lingq_daily_import.log 2>&1
```

### Launchd (macOS)

You can wrap the same command in a LaunchAgent plist if you prefer system-managed scheduling.

## Notes

- Some websites block scraping or require auth/cookies.
- If extraction quality is poor for a site, we can add site-specific selectors.
- If LingQ API returns validation errors, keep the generated payload file and share the error so we can tune field names quickly.
