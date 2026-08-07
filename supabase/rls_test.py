"""
Prove the row-level security policies actually refuse things.

Creates two throwaway accounts, then checks that each one can only reach what
it should — before sharing, while sharing, and after sharing is switched off.
"""
import json
import urllib.error
import urllib.request

URL = "https://lbrudglqapguvzihtncf.supabase.co"
KEY = "sb_publishable_7oC4oxifui5dhvT3qfN4fg_vmmx5CU9"

A_EMAIL = "rls-test-alpha@example.com"
B_EMAIL = "rls-test-beta@example.com"
PASSWORD = "Test-Password-9f3a!"

failures = []


def call(method, path, token=None, body=None, prefer=None):
    request = urllib.request.Request(f"{URL}{path}", method=method)
    request.add_header("apikey", KEY)
    request.add_header("Authorization", f"Bearer {token or KEY}")
    request.add_header("Content-Type", "application/json")
    if prefer:
        request.add_header("Prefer", prefer)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(request, data, timeout=25) as response:
            text = response.read().decode()
            return response.status, (json.loads(text) if text.strip() else None)
    except urllib.error.HTTPError as error:
        text = error.read().decode()
        try:
            return error.code, json.loads(text)
        except json.JSONDecodeError:
            return error.code, text


def check(label, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def account(email):
    status, payload = call("POST", "/auth/v1/signup",
                           body={"email": email, "password": PASSWORD})
    if status < 300 and payload and payload.get("access_token"):
        return payload["access_token"], payload["user"]["id"]
    status, payload = call("POST", "/auth/v1/token?grant_type=password",
                           body={"email": email, "password": PASSWORD})
    if status < 300 and payload and payload.get("access_token"):
        return payload["access_token"], payload["user"]["id"]
    raise SystemExit(f"could not get a session for {email}: {status} {payload}")


def reset(token):
    """Clear anything a previous run left behind, so this one starts clean."""
    for table in ("bills", "income"):
        call("DELETE", f"/rest/v1/{table}?id=not.is.null", token)
    call("DELETE", "/rest/v1/connections?id=not.is.null", token)


print("== accounts ==")
a_token, a_id = account(A_EMAIL)
b_token, b_id = account(B_EMAIL)
print(f"  A {a_id}\n  B {b_id}")

reset(a_token)
reset(b_token)
print("  reset previous state")

print("\n== profile row created by the signup trigger ==")
status, rows = call("GET", f"/rest/v1/profiles?id=eq.{a_id}&select=id,display_name", a_token)
check("A has a profile",
      status == 200 and isinstance(rows, list) and rows and rows[0]["id"] == a_id,
      f"{status} {rows}")

print("\n== A writes its own data ==")
status, created = call("POST", "/rest/v1/bills", a_token,
                       {"user_id": a_id, "name": "A secret rent", "amount": 1234.56,
                        "category": "Housing", "due_day": 3},
                       prefer="return=representation")
check("A can insert a bill", status in (200, 201), f"{status} {created}")
bill_id = created[0]["id"] if isinstance(created, list) and created else None

status, rows = call("GET", "/rest/v1/bills?select=*", a_token)
check("A reads its own bill", status == 200 and len(rows) == 1, f"{status} {rows}")

print("\n== A cannot forge a row owned by B ==")
status, payload = call("POST", "/rest/v1/bills", a_token,
                       {"user_id": b_id, "name": "planted", "amount": 5, "due_day": 1})
check("insert as another user is refused", status >= 400, f"HTTP {status}")

print("\n== B is blind to A, with no connection ==")
status, rows = call("GET", "/rest/v1/bills?select=*", b_token)
check("B sees nothing of A's", status == 200 and rows == [], f"{status} {rows}")
status, rows = call("GET", f"/rest/v1/profiles?id=eq.{a_id}&select=id", b_token)
check("B cannot read A's profile", status == 200 and rows == [], f"{status} {rows}")

print("\n== connect them ==")
status, conn = call("POST", "/rest/v1/connections", a_token,
                    {"requester_id": a_id, "addressee_id": b_id,
                     "relationship": "Partner"},
                    prefer="return=representation")
check("A can invite B", status in (200, 201), f"{status} {conn}")
conn_id = conn[0]["id"] if isinstance(conn, list) and conn else None

status, rows = call("GET", "/rest/v1/bills?select=*", b_token)
check("pending connection shares nothing", status == 200 and rows == [], f"{status} {rows}")

status, _ = call("PATCH", f"/rest/v1/connections?id=eq.{conn_id}", b_token,
                 {"status": "accepted"})
check("B can accept", status in (200, 204), f"HTTP {status}")

status, rows = call("GET", "/rest/v1/bills?select=*", b_token)
check("accepted but not shared still shows nothing", status == 200 and rows == [],
      f"{status} {rows}")

print("\n== A switches sharing on ==")
status, _ = call("PATCH", f"/rest/v1/connections?id=eq.{conn_id}", a_token,
                 {"requester_shares": True})
check("A can set its own share flag", status in (200, 204), f"HTTP {status}")

status, rows = call("GET", "/rest/v1/bills?select=name,amount", b_token)
check("B can now read A's bill", status == 200 and len(rows) == 1, f"{status} {rows}")

print("\n== sharing is read-only ==")
status, _ = call("PATCH", f"/rest/v1/bills?id=eq.{bill_id}", b_token, {"amount": 1})
_, after = call("GET", f"/rest/v1/bills?id=eq.{bill_id}&select=amount", a_token)
unchanged = after and float(after[0]["amount"]) == 1234.56
check("B cannot edit A's bill", unchanged, f"amount now {after}")

status, _ = call("DELETE", f"/rest/v1/bills?id=eq.{bill_id}", b_token)
_, after = call("GET", f"/rest/v1/bills?id=eq.{bill_id}&select=id", a_token)
check("B cannot delete A's bill", bool(after), f"rows left {after}")

print("\n== B cannot flip A's switch on its own behalf ==")
call("PATCH", f"/rest/v1/connections?id=eq.{conn_id}", b_token, {"requester_shares": False})
_, rows = call("GET", "/rest/v1/bills?select=name", b_token)
check("A's sharing survives B turning it off", len(rows) == 1, f"{rows}")

# The dangerous direction: forcing someone to share without their consent.
call("PATCH", f"/rest/v1/connections?id=eq.{conn_id}", a_token, {"requester_shares": False})
call("PATCH", f"/rest/v1/connections?id=eq.{conn_id}", b_token, {"requester_shares": True})
_, flags = call("GET", f"/rest/v1/connections?id=eq.{conn_id}&select=requester_shares",
                a_token)
check("B cannot force A to share",
      isinstance(flags, list) and flags and flags[0]["requester_shares"] is False,
      f"requester_shares={flags}")
_, rows = call("GET", "/rest/v1/bills?select=name", b_token)
check("forced sharing leaks nothing", rows == [], f"{rows}")

print("\n== B cannot answer an invitation on A's behalf, or re-answer ==")
status, _ = call("PATCH", f"/rest/v1/connections?id=eq.{conn_id}", a_token,
                 {"status": "declined"})
_, flags = call("GET", f"/rest/v1/connections?id=eq.{conn_id}&select=status", a_token)
check("requester cannot change status",
      isinstance(flags, list) and flags and flags[0]["status"] == "accepted",
      f"status={flags}")

print("\n== nobody can promote themselves to admin ==")
call("PATCH", f"/rest/v1/profiles?id=eq.{b_id}", b_token, {"is_admin": True})
_, me = call("GET", f"/rest/v1/profiles?id=eq.{b_id}&select=is_admin", b_token)
check("is_admin is not self-writable",
      isinstance(me, list) and me and me[0]["is_admin"] is False, f"{me}")
status, payload = call("POST", "/rest/v1/rpc/export_bills", b_token, {})
check("export still refused after the attempt", status >= 400,
      f"HTTP {status} {str(payload)[:70]}")

print("\n== display name is still editable ==")
status, _ = call("PATCH", f"/rest/v1/profiles?id=eq.{b_id}", b_token,
                 {"display_name": "Beta"})
_, me = call("GET", f"/rest/v1/profiles?id=eq.{b_id}&select=display_name", b_token)
check("own display_name can be set",
      isinstance(me, list) and me and me[0]["display_name"] == "Beta", f"{me}")

# Restore sharing so the revocation check below still means something.
call("PATCH", f"/rest/v1/connections?id=eq.{conn_id}", a_token, {"requester_shares": True})

print("\n== A switches sharing off ==")
call("PATCH", f"/rest/v1/connections?id=eq.{conn_id}", a_token, {"requester_shares": False})
status, rows = call("GET", "/rest/v1/bills?select=*", b_token)
check("access is revoked immediately", status == 200 and rows == [], f"{status} {rows}")

print("\n== admin export is closed to non-admins ==")
status, payload = call("POST", "/rest/v1/rpc/export_bills", b_token, {})
check("non-admin export refused", status >= 400, f"HTTP {status} {str(payload)[:80]}")

print("\n== email lookup needs a session ==")
status, _ = call("POST", "/rest/v1/rpc/find_user_by_email", None,
                 {"lookup_email": A_EMAIL})
check("signed-out lookup refused", status >= 400, f"HTTP {status}")
status, found = call("POST", "/rest/v1/rpc/find_user_by_email", b_token,
                     {"lookup_email": A_EMAIL})
check("signed-in lookup resolves an id", status == 200 and found == a_id, f"{status} {found}")

print("\n" + ("ALL PASS" if not failures else f"FAILURES ({len(failures)}): {failures}"))
print(f"\ncleanup: delete users {A_EMAIL} and {B_EMAIL} in Authentication -> Users")
