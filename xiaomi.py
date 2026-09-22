import base64
import hashlib
import json
import re
import time
import urllib.parse

from pathlib import Path

import requests


AccountUrl = "https://account.xiaomi.com"
ServiceLoginUrl = f"{AccountUrl}/pass/serviceLogin"
BrowserLoginUrl = f"{AccountUrl}/longPolling/loginUrl"
StoreUrl = "https://play.google.com/store/apps/details?id=com.mi.global.bbs&hl=en&gl=us"

ApiUrl = "https://sgp-api.buy.mi.com"
StatePath = "/bbs/api/global/user/bl-switch/state"
ApplyPath = "/bbs/api/global/apply/bl-auth"
SessionFile = Path(__file__).resolve().parent / "data" / "session.json"
DefaultAppVersion = "5.4.39"


def new_web():
    web = requests.Session()
    web.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json;charset=UTF-8",
        "Accept-Language": "en-US,en;q=0.9"
    })
    return web


def new_api():
    return requests.Session()


def xiaomi_json(response):
    text = response.text
    if text.startswith("&&&START&&&"):
        text = text[11:]
    return json.loads(text)


def web_json(web, url, params=None, timeout=30):
    try:
        response = web.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return xiaomi_json(response)
    except requests.Timeout as exc:
        raise ConnectionError("Connection timed out") from exc
    except requests.RequestException as exc:
        raise ConnectionError("Connection failed") from exc


def put_session(web, account):
    for key, value in account.items():
        web.cookies.set(key, value, domain=".xiaomi.com", path="/")


def valid_session(data):
    if not isinstance(data, dict):
        return False
    return all(isinstance(data.get(key), str) and data[key] for key in (
        "deviceId", "passToken", "userId"
    ))


def browser_login(web, open_login):
    auth = {"sid": "18n_bbs_global", "_json": "true"}
    first = web_json(web, ServiceLoginUrl, auth)

    for key in ("serviceParam", "qs", "callback", "_sign"):
        if key not in first:
            raise RuntimeError("Xiaomi did not return the login data")
        auth[key] = first[key]

    auth["_json"] = "false"
    login = web_json(web, BrowserLoginUrl, auth)
    url = login.get("loginUrl")
    poll = login.get("lp")
    timeout = int(login.get("timeout", 120))
    if not url or not poll:
        raise RuntimeError("Xiaomi did not return the browser login link")

    open_login(url)
    web_json(web, poll, timeout=max(10, min(timeout, 300)))

    cookies = web.cookies.get_dict()
    account = {key: cookies.get(key) for key in ("deviceId", "passToken", "userId")}
    if not valid_session(account):
        raise RuntimeError("Login finished without the required session cookies")
    return account


def get_service_token(web, account):
    put_session(web, account)
    data = web_json(web, ServiceLoginUrl, {"sid": "18n_bbs_global", "_json": "true"})
    nonce = data.get("nonce")
    security = data.get("ssecurity")
    location = data.get("location")
    if not nonce or not security or not location:
        raise RuntimeError("Saved login expired or service data is missing")

    raw = f"nonce={nonce}&{security}".encode()
    sign = urllib.parse.quote(base64.b64encode(hashlib.sha1(raw).digest()))
    joiner = "&" if "?" in location else "?"
    try:
        response = web.get(f"{location}{joiner}clientSign={sign}", timeout=20)
        response.raise_for_status()
    except requests.Timeout as exc:
        raise ConnectionError("Mi Community session timed out") from exc
    except requests.RequestException as exc:
        raise ConnectionError("Could not open the Mi Community session") from exc

    token = web.cookies.get_dict().get("new_bbs_serviceToken")
    if not token:
        raise RuntimeError("Mi Community service token is missing")
    return token


def app_version():
    try:
        response = requests.get(StoreUrl, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        response.raise_for_status()
        for match in re.finditer(r"AF_initDataCallback[\s\S]*?</script", response.text):
            block = match.group()
            if "ds:5" not in block:
                continue
            value = re.search(r"data:([\s\S]*?), sideChannel: {}", block)
            if not value:
                continue
            version = json.loads(value.group(1))[1][2][140][0][0][0]
            if re.fullmatch(r"\d+\.\d+\.\d+", version):
                return version
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        pass
    return DefaultAppVersion


def api_headers(account, token, version):
    major, minor, patch = (int(part) for part in version.split("."))
    version_code = major * 100000 + minor * 100 + patch
    cookie = (
        f"new_bbs_serviceToken={token};"
        f"versionCode={version_code};"
        f"versionName={version};"
        f"deviceId={account['deviceId']};"
    )
    return {
        "User-Agent": "okhttp/4.12.0",
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "Cookie": cookie
    }


def api_call(api, method, path, headers, body=None, timeout=10):
    try:
        response = api.request(
            method,
            f"{ApiUrl}{path}",
            headers=headers,
            json=body,
            timeout=timeout
        )
        response.raise_for_status()
        return response.json()
    except requests.Timeout as exc:
        raise ConnectionError("API request timed out") from exc
    except requests.RequestException as exc:
        raise ConnectionError("API request failed") from exc
    except requests.JSONDecodeError as exc:
        raise ValueError("API returned invalid JSON") from exc


def parse_state(response):
    if response.get("code") != 0:
        return {"code": -1, "message": response.get("msg", "Account check failed")}

    data = response.get("data") or {}
    deadline = data.get("deadline_format", "")
    if data.get("is_pass") == 1:
        return {
            "code": 1,
            "message": f"Unlock access is active until {deadline} Beijing time"
        }
    if data.get("button_state") == 1:
        return {"code": 2, "message": "Account is ready to apply"}
    if data.get("button_state") == 2:
        return {"code": 3, "message": f"Account error, try again after {deadline}"}
    return {"code": 4, "message": "Account must be registered for at least 30 days"}


def parse_apply(response):
    if response.get("code") != 0:
        return response.get("msg", "Application failed")

    data = response.get("data") or {}
    result = data.get("apply_result")
    deadline = data.get("deadline_format", "")
    if result == 1:
        return "Application successful"
    if result in (2, 4):
        return f"Account error, try again after {deadline}"
    if result == 3:
        return f"Application quota reached, try again after {deadline} (GMT+8)"
    if result == 5:
        return "Application failed, try again later"
    if result == 6:
        return "Please try again in a minute"
    if result == 7:
        return "Please try again later"
    return "Unknown server response"


def state_call(api, headers, timeout=10, tries=2):
    error = None
    for _ in range(tries):
        started = time.monotonic()
        try:
            response = api_call(api, "GET", StatePath, headers, timeout=timeout)
            return parse_state(response), (time.monotonic() - started) * 1000
        except (ValueError, ConnectionError) as exc:
            error = exc
    raise ConnectionError("Could not check account state") from error
