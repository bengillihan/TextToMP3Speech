# Railway Operations

## Web Service

Railway can use the included `Procfile`:

```sh
gunicorn -c gunicorn.conf.py main:app
```

`.python-version` pins deploys to Python 3.12, and `nixpacks.toml` sets the same start command. Nixpacks provides `ffmpeg-headless` for the audio combiner at runtime.

The default Gunicorn settings favor low idle cost:

- `WEB_CONCURRENCY=1`
- `GUNICORN_THREADS=2`
- `LOG_LEVEL=info`

Increase `WEB_CONCURRENCY` only if request traffic needs it. Each extra worker adds memory usage.

## Required Environment Variables

- `DATABASE_URL`: Railway Postgres connection URL
- `SESSION_SECRET`: Flask session signing secret
- `OPENAI_API_KEY`: OpenAI API key
- `GOOGLE_OAUTH_CLIENT_ID`: Google OAuth client ID
- `GOOGLE_OAUTH_CLIENT_SECRET`: Google OAuth client secret
- `OAUTH_REDIRECT_DOMAIN`: Public app domain without protocol, for example `your-app.up.railway.app`

## Optional Environment Variables

- `CONVERSION_RETENTION_DAYS`: Conversion retention window. Defaults to `90`.
- `AUTO_CREATE_TABLES`: Set to `false` on Railway after the database schema exists.
- `DIAGNOSTICS_ENABLED`: Set to `true` only when diagnostics are needed.
- `DIAGNOSTIC_ADMIN_EMAILS`: Comma-separated admin emails allowed to use diagnostics.

## Scheduled Cleanup

Run this command from a Railway cron/scheduled job, typically once per day:

```sh
python -m flask --app main cleanup-conversions
```

This deletes expired conversions, related logs/metrics, and generated audio files. Keeping this as a scheduled job avoids doing cleanup work during normal web requests.
