"""WSGI entry point.

Development:  flask --app wsgi run
Production:   waitress-serve --listen=0.0.0.0:8000 wsgi:app   (Windows)
              gunicorn -w 4 -b 0.0.0.0:8000 wsgi:app           (Linux)
"""

from pathlib import Path

from dotenv import load_dotenv

# Only loads a local .env when present; in production, set real environment variables instead.
load_dotenv(Path(__file__).with_name(".env"))

from careflow import create_app  # noqa: E402

app = create_app()
