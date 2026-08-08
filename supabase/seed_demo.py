"""
seed_demo.py
------------
Create a small set of demo accounts with realistic finances and relationships,
so the app can be exercised without touching anyone's real data.

    python supabase/seed_demo.py

Re-running is safe: existing demo accounts are reused and their bills, income
and connections are rebuilt from scratch.

Every account uses @example.com, which is reserved by the IANA and cannot
reach a real inbox, and they all share one obvious password. These exist to be
poked at — never put anything real in them.

Requires "Confirm email" to be OFF in the Supabase dashboard, since these
accounts cannot receive mail.
"""

import json
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

URL = "https://lbrudglqapguvzihtncf.supabase.co"
KEY = "sb_publishable_7oC4oxifui5dhvT3qfN4fg_vmmx5CU9"
PASSWORD = "demo-password-123"

TODAY = date.today()


def last_weekday(weekday: int) -> str:
    """The most recent given weekday (0 = Monday), as YYYY-MM-DD."""
    back = (TODAY.weekday() - weekday) % 7 or 7
    return (TODAY - timedelta(days=back)).isoformat()


def in_days(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


# Two households. Ana and Ben plan together and share both ways; Ana's mother
# Carla keeps her own budget and shares one way, which is the asymmetric case
# worth being able to look at.
PEOPLE = {
    "ana": {
        "email": "demo.ana@example.com",
        "name": "Ana Demo",
        "bills": [
            ("Rent", 1450, "Housing", "monthly", 1),
            ("Council tax", 168, "Housing", "monthly", 5),
            ("Electric & gas", 96.40, "Utilities", "monthly", 12),
            ("Broadband", 39.99, "Utilities", "monthly", 18),
            ("Phone", 22, "Utilities", "monthly", 20),
            ("Car insurance", 780, "Insurance", "annual", in_days(96)),
            ("Water", 240, "Utilities", "quarterly", in_days(24)),
            ("Groceries", 420, "Groceries", "monthly", 28),
            ("Gym", 34, "Other", "monthly", 25),
            ("Streaming", 17.98, "Subscriptions", "monthly", 22),
        ],
        "income": [
            ("Salary", 2150, "biweekly", last_weekday(3)),   # last Thursday
            ("Tutoring", 180, "monthly", 26),
        ],
    },
    "ben": {
        "email": "demo.ben@example.com",
        "name": "Ben Demo",
        "bills": [
            ("Student loan", 295, "Debt", "monthly", 15),
            ("Car payment", 268.50, "Auto", "monthly", 8),
            ("Phone", 28, "Utilities", "monthly", 20),
            ("Bike insurance", 190, "Insurance", "annual", in_days(210)),
            ("Dentist plan", 21, "Healthcare", "monthly", 3),
            ("Cleaner", 60, "Other", "biweekly", in_days(4)),
        ],
        "income": [
            ("Salary", 3300, "semimonthly", (15, 30)),
            ("Freelance", 640, "monthly", 28),
        ],
    },
    "carla": {
        "email": "demo.carla@example.com",
        "name": "Carla Demo",
        "bills": [
            ("Mortgage", 910, "Housing", "monthly", 1),
            ("Home insurance", 420, "Insurance", "annual", in_days(140)),
            ("Energy", 128, "Utilities", "monthly", 14),
            ("Groceries", 310, "Groceries", "monthly", 6),
            ("Car service plan", 300, "Auto", "semiannual", in_days(58)),
        ],
        "income": [
            ("Pension", 1780, "monthly", 25),
        ],
    },
}

# (from, to, from_label, to_label, from_shares, to_shares)
#
# Each side labels the other independently, so the pair need not agree and
# usually should not. Ana calls Carla her mother; Carla calls Ana her daughter.
LINKS = [
    ("ana", "ben", "Partner", "Partner", True, True),
    ("ana", "carla", "Mother", "Daughter", False, True),
]


def call(method, path, token=None, body=None, prefer=None):
    request = urllib.request.Request(f"{URL}{path}", method=method)
    request.add_header("apikey", KEY)
    request.add_header("Authorization", f"Bearer {token or KEY}")
    request.add_header("Content-Type", "application/json")
    if prefer:
        request.add_header("Prefer", prefer)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(request, data, timeout=30) as response:
            text = response.read().decode()
            return response.status, (json.loads(text) if text.strip() else None)
    except urllib.error.HTTPError as error:
        text = error.read().decode()
        try:
            return error.code, json.loads(text)
        except json.JSONDecodeError:
            return error.code, text


def account(email, name):
    """Sign up, or sign in if the account is already there."""
    status, payload = call("POST", "/auth/v1/signup", body={
        "email": email, "password": PASSWORD,
        "data": {"display_name": name},
    })
    if not (payload or {}).get("access_token"):
        status, payload = call("POST", "/auth/v1/token?grant_type=password",
                               body={"email": email, "password": PASSWORD})
    if not (payload or {}).get("access_token"):
        raise SystemExit(
            f"could not sign in as {email}: {status} {payload}\n"
            "If this mentions confirmation, switch 'Confirm email' off in\n"
            "Authentication -> Sign In / Providers -> Email."
        )
    token = payload["access_token"]
    user_id = payload["user"]["id"]
    call("PATCH", f"/rest/v1/profiles?id=eq.{user_id}", token,
         {"display_name": name})
    return token, user_id


def wipe(token):
    for table in ("bills", "income", "connections"):
        call("DELETE", f"/rest/v1/{table}?id=not.is.null", token)


def add_bill(token, user_id, row):
    name, amount, category, frequency, when = row
    payload = {"user_id": user_id, "name": name, "amount": amount,
               "category": category, "frequency": frequency}
    if frequency == "monthly":
        payload["due_day"] = when
    else:
        payload["anchor_date"] = when
    status, body = call("POST", "/rest/v1/bills", token, payload)
    if status >= 300:
        print(f"    ! {name}: {body}")


def add_income(token, user_id, row):
    name, amount, frequency, when = row
    payload = {"user_id": user_id, "name": name, "amount": amount,
               "frequency": frequency}
    if frequency == "monthly":
        payload["pay_day"] = when
    elif frequency == "semimonthly":
        payload["pay_day"], payload["pay_day_2"] = when
    else:
        payload["anchor_date"] = when
    status, body = call("POST", "/rest/v1/income", token, payload)
    if status >= 300:
        print(f"    ! {name}: {body}")


def main():
    print("Creating demo accounts…\n")
    people = {}
    for key, spec in PEOPLE.items():
        token, user_id = account(spec["email"], spec["name"])
        people[key] = {"token": token, "id": user_id, **spec}
        print(f"  {spec['name']:<12} {spec['email']}")

    print("\nClearing previous demo data…")
    for person in people.values():
        wipe(person["token"])

    print("\nAdding bills and income…")
    for key, person in people.items():
        for row in person["bills"]:
            add_bill(person["token"], person["id"], row)
        for row in person["income"]:
            add_income(person["token"], person["id"], row)
        print(f"  {person['name']:<12} {len(person['bills'])} bills, "
              f"{len(person['income'])} income")

    print("\nLinking them up…")
    for source, target, from_label, to_label, from_shares, to_shares in LINKS:
        a, b = people[source], people[target]
        status, created = call("POST", "/rest/v1/connections", a["token"], {
            "requester_id": a["id"], "addressee_id": b["id"],
            "requester_relationship": from_label,
        }, prefer="return=representation")
        if status >= 300:
            print(f"  ! {source} -> {target}: {created}")
            continue
        link_id = created[0]["id"]
        # The invitee accepts, then each side sets only its own flag and its own
        # label — which is the only way the database will allow it.
        call("PATCH", f"/rest/v1/connections?id=eq.{link_id}", b["token"],
             {"status": "accepted", "addressee_relationship": to_label})
        call("PATCH", f"/rest/v1/connections?id=eq.{link_id}", a["token"],
             {"requester_shares": from_shares})
        call("PATCH", f"/rest/v1/connections?id=eq.{link_id}", b["token"],
             {"addressee_shares": to_shares})
        arrow = "<->" if from_shares and to_shares else "->" if to_shares else "<-"
        print(f"  {a['name']} {arrow} {b['name']}  "
              f"({a['name']}: {from_label} / {b['name']}: {to_label})")

    print("\nDone. Sign in with any of these:\n")
    for person in people.values():
        print(f"  {person['email']:<26} {PASSWORD}")
    print("\n  Ana and Ben share both ways.")
    print("  Carla shares with Ana, but Ana does not share back —")
    print("  so Ana sees a 'Viewing' picker and Carla does not.")


if __name__ == "__main__":
    sys.exit(main())
