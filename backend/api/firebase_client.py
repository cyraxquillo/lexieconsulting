import json
import os

import firebase_admin
from firebase_admin import credentials, firestore


def get_firebase_app():
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    credentials_json = os.getenv("FIREBASE_CREDENTIALS", "").strip()
    if not credentials_json:
        raise RuntimeError("FIREBASE_CREDENTIALS must contain service-account JSON")
    try:
        credentials_info = json.loads(credentials_json)
    except json.JSONDecodeError as error:
        raise RuntimeError("FIREBASE_CREDENTIALS must contain valid JSON") from error
    if not isinstance(credentials_info, dict):
        raise RuntimeError("FIREBASE_CREDENTIALS must contain a JSON object")

    certificate = credentials.Certificate(credentials_info)
    project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()
    database_url = os.getenv("FIREBASE_DATABASE_URL", "").strip()
    options = {}
    if project_id:
        options["projectId"] = project_id
    if database_url:
        options["databaseURL"] = database_url
    try:
        return firebase_admin.initialize_app(certificate, options or None)
    except ValueError:
        return firebase_admin.get_app()


def get_firestore_client():
    return firestore.client(get_firebase_app())