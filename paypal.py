"""PayPal Checkout (Orders v2) over HTTPS with the standard library.

Proof of payment is our own authenticated capture call: after the buyer approves on PayPal,
the server asks PayPal to capture the order and reads PayPal's answer. Nothing is marked paid
from a redirect, an email or any other message a third party could forge.

Two kinds of PayPal account:
- TAPNET's own (.env on the server, never in GitHub): used when a practitioner hasn't connected
  PayPal yet, and later to send the monthly invoices.
    NUMER_PAYPAL_ENV        sandbox | live (default sandbox)
    NUMER_PAYPAL_CLIENT_ID  REST app client ID
    NUMER_PAYPAL_SECRET     REST app secret
- A practitioner's own PayPal Business account (keys saved encrypted in the database, see
  practitioner_paypal.py): their clients pay them directly.
Every call takes the Account to use; without one it uses TAPNET's.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

API = {"sandbox": "https://api-m.sandbox.paypal.com", "live": "https://api-m.paypal.com"}
TIMEOUT = 20

_token_lock = threading.Lock()
_tokens: dict = {}  # (env, client_id) -> {"value", "expires"}


@dataclass(frozen=True)
class Account:
    key: str          # "tapnet" or "user:<id>": stored on each order, to capture and check it later
    env: str          # sandbox | live
    client_id: str
    secret: str

    def __repr__(self) -> str:  # never print the secret
        return f"Account({self.key}, {self.env})"


class PayPalError(Exception):
    """A PayPal call failed. `issue` is PayPal's error code when it gave one (e.g. INSTRUMENT_DECLINED)."""

    def __init__(self, message: str, status: int | None = None, issue: str = ""):
        super().__init__(message)
        self.status = status
        self.issue = issue


def environment() -> str:
    return "live" if os.environ.get("NUMER_PAYPAL_ENV", "sandbox").strip().lower() == "live" else "sandbox"


def configured() -> bool:
    return bool(os.environ.get("NUMER_PAYPAL_CLIENT_ID") and os.environ.get("NUMER_PAYPAL_SECRET"))


def tapnet_account() -> Account | None:
    client_id = os.environ.get("NUMER_PAYPAL_CLIENT_ID", "")
    secret = os.environ.get("NUMER_PAYPAL_SECRET", "")
    return Account("tapnet", environment(), client_id, secret) if client_id and secret else None


def _account(account: Account | None) -> Account:
    account = account or tapnet_account()
    if account is None:
        raise PayPalError("PayPal is not configured")
    return account


def _request(method: str, path: str, body: dict | None = None, headers: dict | None = None,
             form: str | None = None, account: Account | None = None) -> dict:
    data = None
    all_headers = {"Accept": "application/json", **(headers or {})}
    if form is not None:
        data = form.encode()
        all_headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        all_headers["Content-Type"] = "application/json"
    env = account.env if account is not None else environment()
    request = urllib.request.Request(API.get(env, API["sandbox"]) + path, data=data, method=method, headers=all_headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        issue = ""
        try:
            details = json.loads(exc.read() or b"{}")
            issue = ((details.get("details") or [{}])[0].get("issue") or details.get("name") or "")
        except (ValueError, AttributeError):
            pass
        raise PayPalError(f"PayPal {method} {path.split('?')[0]} failed ({exc.code})", exc.code, issue) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PayPalError(f"PayPal unreachable: {type(exc).__name__}") from None
    return json.loads(raw or b"{}")


def _access_token(account: Account) -> str:
    key = (account.env, account.client_id, account.secret)
    with _token_lock:
        cached = _tokens.get(key)
        if cached and cached["expires"] > time.time() + 60:
            return cached["value"]
        basic = base64.b64encode(f"{account.client_id}:{account.secret}".encode()).decode()
        result = _request("POST", "/v1/oauth2/token", headers={"Authorization": f"Basic {basic}"},
                          form="grant_type=client_credentials", account=account)
        _tokens[key] = {"value": result["access_token"],
                        "expires": time.time() + int(result.get("expires_in", 300))}
        return result["access_token"]


def verify(account: Account) -> None:
    """Raise PayPalError unless PayPal accepts these keys (used when a practitioner connects)."""
    _access_token(account)


def _auth(account: Account, extra: dict | None = None) -> dict:
    return {"Authorization": f"Bearer {_access_token(account)}", **(extra or {})}


def money_value(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def create_order(*, reference: str, cents: int, currency: str, description: str,
                 return_url: str, cancel_url: str, brand: str, account: Account | None = None) -> tuple[str, str]:
    """Create a checkout order. Returns (PayPal order id, URL to send the buyer to)."""
    account = _account(account)
    body = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "reference_id": reference, "custom_id": reference, "description": description[:127],
            "amount": {"currency_code": currency, "value": money_value(cents)},
        }],
        "payment_source": {"paypal": {"experience_context": {
            "brand_name": brand[:127], "shipping_preference": "NO_SHIPPING", "user_action": "PAY_NOW",
            "return_url": return_url, "cancel_url": cancel_url,
        }}},
    }
    result = _request("POST", "/v2/checkout/orders", body, account=account,
                      headers=_auth(account, {"PayPal-Request-Id": f"create-{reference}", "Prefer": "return=minimal"}))
    links = {link.get("rel"): link.get("href") for link in result.get("links", [])}
    approve = links.get("payer-action") or links.get("approve")
    if not result.get("id") or not approve:
        raise PayPalError("PayPal did not return an approval link")
    return result["id"], approve


def get_order(order_id: str, account: Account | None = None) -> dict:
    account = _account(account)
    return _request("GET", f"/v2/checkout/orders/{_safe(order_id)}", headers=_auth(account), account=account)


def capture_order(order_id: str, account: Account | None = None) -> dict:
    """Capture an approved order. Safe to repeat: PayPal-Request-Id makes the retry idempotent, and an
    order captured earlier (ORDER_ALREADY_CAPTURED) is read back instead."""
    account = _account(account)
    try:
        return _request("POST", f"/v2/checkout/orders/{_safe(order_id)}/capture", {}, account=account,
                        headers=_auth(account, {"PayPal-Request-Id": f"capture-{order_id}",
                                                "Prefer": "return=representation"}))
    except PayPalError as exc:
        if exc.issue == "ORDER_ALREADY_CAPTURED":
            return get_order(order_id, account)
        raise


def _safe(order_id: str) -> str:
    if not order_id or not order_id.isalnum() or len(order_id) > 64:
        raise PayPalError("Invalid PayPal order id")
    return order_id


def completed_capture(result: dict, *, reference: str, cents: int, currency: str) -> dict | None:
    """The completed capture in a PayPal order response, if it pays exactly what we asked for.

    Returns {"capture_id", "payer_email"} or None (not completed, or a mismatch in reference,
    amount or currency, which is never treated as paid).
    """
    if result.get("status") != "COMPLETED":
        return None
    for unit in result.get("purchase_units") or []:
        if unit.get("custom_id") != reference and unit.get("reference_id") != reference:
            continue
        for capture in ((unit.get("payments") or {}).get("captures") or []):
            amount = capture.get("amount") or {}
            if (capture.get("status") == "COMPLETED" and amount.get("currency_code") == currency
                    and amount.get("value") == money_value(cents)):
                payer = (result.get("payer") or {}).get("email_address") \
                    or (((result.get("payment_source") or {}).get("paypal") or {}).get("email_address")) or ""
                return {"capture_id": capture.get("id") or "", "payer_email": payer}
    return None
