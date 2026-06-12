"""WSGI entry point for gunicorn (and other WSGI servers).

  gunicorn wsgi:app
  gunicorn --bind 0.0.0.0:$PORT wsgi:app

The Flask dev server (python app.py) initialises in its own __main__ block;
gunicorn skips that block, so we do the same initialisation here.
"""

from app import app, init_client, strategies

init_client()
strategies.start()
