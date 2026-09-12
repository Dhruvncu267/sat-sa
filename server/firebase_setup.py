"""
SOCAssure Firebase wiring
=========================
One place that initializes the Firebase Admin SDK and hands out a Firestore
client + an ID-token verifier to the rest of the app.

How it finds your credentials (checked in this order):
  1. Env var FIREBASE_SERVICE_ACCOUNT_JSON -- the *entire contents* of your
     service-account JSON file, pasted as one environment variable. This is
     the one to use when deploying (Render/Railway/etc. -- most hosts let
     you paste a multi-line env var in their dashboard, and this avoids
     needing to upload a secret file at all).
  2. Env var GOOGLE_APPLICATION_CREDENTIALS -- a path to the JSON file on
     disk. Google's own libraries respect this automatically.
  3. A local file at server/serviceAccountKey.json (for running on your own
     machine during development). This file is in .gitignore -- it must
     NEVER be committed or pushed anywhere public.

If none of these are present, the app fails fast with a clear error instead
of silently running with no database.
"""
import json
import os

import firebase_admin
from firebase_admin import auth as fb_auth
from firebase_admin import credentials, firestore

HERE = os.path.dirname(os.path.abspath(__file__))
_LOCAL_KEY_PATH = os.path.join(HERE, "serviceAccountKey.json")

_app = None
_db = None


def _load_credentials():
    inline = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    if inline:
        return credentials.Certificate(json.loads(inline))

    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if path and os.path.exists(path):
        return credentials.Certificate(path)

    if os.path.exists(_LOCAL_KEY_PATH):
        return credentials.Certificate(_LOCAL_KEY_PATH)

    raise RuntimeError(
        "No Firebase service account credentials found. Set the "
        "FIREBASE_SERVICE_ACCOUNT_JSON environment variable (paste the whole "
        "JSON key file as its value), or place serviceAccountKey.json in the "
        "server/ folder for local development. See README.md."
    )


def init():
    """Idempotent -- safe to call from multiple modules at import time."""
    global _app, _db
    if _app is not None:
        return
    cred = _load_credentials()
    _app = firebase_admin.initialize_app(cred)
    _db = firestore.client()


def db():
    init()
    return _db


def verify_id_token(id_token):
    """Returns the decoded token dict (has 'uid', 'email', ...) on success,
    or None if the token is missing, malformed, expired, or was revoked.
    Never raises -- callers just treat None as 'not authenticated'."""
    init()
    if not id_token:
        return None
    try:
        return fb_auth.verify_id_token(id_token)
    except Exception:
        return None


def create_auth_user(email, password, display_name=""):
    """Creates a brand-new Firebase Auth user. Called from three places:
    the open public /api/signup route in app.py (anyone can trigger this),
    the older token-gated /setup route in app.py, and create_supervisor.py
    for command-line account creation. Access control for who gets to SEE
    data lives entirely elsewhere (require_supervisor + _owns_entity in
    app.py, and firestore_store.scope_analysis_to_owner) -- this function
    itself does not gate who can create an account."""
    init()
    return fb_auth.create_user(email=email, password=password, display_name=display_name)
