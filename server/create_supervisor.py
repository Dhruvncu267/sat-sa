"""
Create a supervisor account for SAT-SA (Firebase version).
================================================================
This is the ONLY way a login account gets created -- there is still no
"Sign up" button anywhere in the app, on purpose. This script needs your
Firebase credentials set up exactly like the running app does (see
firebase_setup.py / README.md): either a server/serviceAccountKey.json
file, or the FIREBASE_SERVICE_ACCOUNT_JSON environment variable.

Usage:
    python create_supervisor.py

Then follow the prompts: email, display name, password (hidden as you
type, twice to confirm). This creates the account in Firebase
Authentication AND a matching profile document in Firestore (which is
what lets you deactivate someone later without deleting their login).

    python create_supervisor.py --deactivate a@b.com
    python create_supervisor.py --reactivate a@b.com
"""
import argparse
import getpass
import sys

import firebase_setup
import firestore_store


def prompt_create():
    print("Create a new SAT-SA supervisor account.\n")
    email = input("Supervisor email: ").strip()
    name = input("Display name (e.g. 'A. Sharma'): ").strip()
    while True:
        password = getpass.getpass("Password (min 8 characters, hidden as you type): ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords did not match -- try again.\n")
            continue
        if len(password) < 8:
            print("Firebase requires at least 6 characters; we ask for 8 -- try again.\n")
            continue
        break
    try:
        user = firebase_setup.create_auth_user(email, password, name)
        firestore_store.create_supervisor_profile(user.uid, email, name)
    except Exception as e:
        print(f"\nCould not create account: {e}")
        sys.exit(1)
    print(f"\nDone. '{email}' can now log in to SAT-SA with the password you just set.")


def main():
    parser = argparse.ArgumentParser(description="Manage SAT-SA supervisor accounts.")
    parser.add_argument("--deactivate", metavar="EMAIL", help="Block an account from using the dashboard (Firebase Auth login still works, but the backend will refuse the account).")
    parser.add_argument("--reactivate", metavar="EMAIL", help="Re-enable a previously deactivated account.")
    args = parser.parse_args()

    if args.deactivate or args.reactivate:
        email = args.deactivate or args.reactivate
        active = bool(args.reactivate)
        from firebase_admin import auth as fb_auth
        user = fb_auth.get_user_by_email(email)
        firebase_setup.db().collection(firestore_store.SUPERVISORS).document(user.uid).set(
            {"active": active}, merge=True
        )
        print(f"{'Reactivated' if active else 'Deactivated'}: {email}")
        return

    prompt_create()


if __name__ == "__main__":
    main()
