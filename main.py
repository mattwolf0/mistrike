import json
import os
import sys
import time
import webbrowser

from datetime import datetime, timedelta, timezone

from colorama import Fore, Style, just_fix_windows_console

import timing
import xiaomi


UseColor = sys.stdout.isatty() and "NO_COLOR" not in os.environ
Orange = Fore.LIGHTYELLOW_EX
Green = Fore.LIGHTGREEN_EX
Red = Fore.LIGHTRED_EX
Yellow = Fore.YELLOW
Gray = Fore.LIGHTBLACK_EX
Bold = Style.BRIGHT


def paint(text, color):
    return f"{color}{text}{Style.RESET_ALL}" if UseColor else text


def banner():
    print()
    print(paint("  MiStrike", Bold + Orange))
    print(paint("  Xiaomi application timing", Gray))
    print(paint("  --------------------------------", Gray))


def info(text):
    print(f"{paint('[i]', Orange)} {text}")


def good(text):
    print(f"{paint('[+]', Green)} {text}")


def warn(text):
    print(f"{paint('[!]', Yellow)} {text}")


def fail(text):
    print(f"{paint('[x]', Red)} {text}")


def ask_yes(text, default=False):
    while True:
        value = input(text).strip().lower()
        if not value:
            return default
        if value in ("y", "yes"):
            return True
        if value in ("n", "no"):
            return False


def ask_delay():
    while True:
        try:
            value = int(input(
                "\nDelay before 00:00 (GMT+8) in ms\n"
                "Example: 200, 500 or 1000: "
            ))
            if value >= 0:
                return value / 1000
        except ValueError:
            pass


def read_json(path, fallback):
    try:
        with path.open(encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return fallback


def write_json(path, data):
    temp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with temp.open("w", encoding="utf-8") as file:
            json.dump(data, file)
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        temp.replace(path)
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def remove_file(path):
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def open_login(url):
    info("Opening Xiaomi login in your browser...")
    if not webbrowser.open(url):
        print(f"\n{url}\n")
    info("Finish the login in the browser. This window will continue automatically.")


def new_login(web):
    account = xiaomi.browser_login(web, open_login)
    good(f"Login successful for account {account['userId']}")
    if ask_yes("Remember this login? [Y/n]: ", default=True):
        write_json(xiaomi.SessionFile, account)
        info(f"Session saved to {xiaomi.SessionFile}")
    else:
        info("Session is kept in memory only")
    return account


def get_login(web):
    saved = read_json(xiaomi.SessionFile, None)
    if xiaomi.valid_session(saved):
        print(f"\nSaved account: {paint(saved['userId'], Orange)}")
        choice = input("Enter to continue, or L to log out: ").strip().lower()
        if choice not in ("l", "logout"):
            xiaomi.put_session(web, saved)
            return saved, True

        if remove_file(xiaomi.SessionFile):
            good("Saved login removed")

    return new_login(web), False


def clear_wait():
    print("\r" + " " * 46 + "\r", end="", flush=True)


def fresh_ping(api, headers):
    try:
        status, ping = xiaomi.state_call(api, headers, timeout=5, tries=1)
        if status.get("code") == -1 or not timing.valid_ping(ping):
            return None
        return ping
    except ConnectionError:
        return None


def wait_for_send(api, headers, midnight, delay, auto, history, start, mono, ping):
    target = midnight - timedelta(seconds=delay)
    info(f"Target: {target.strftime('%H:%M:%S.%f')} (GMT+8)")

    checked = False
    while True:
        now = timing.clock_now(start, mono)
        left = (target - now).total_seconds()
        if left <= 0:
            clear_wait()
            return target, now, ping

        if not checked and left <= 15:
            clear_wait()
            checked = True
            measured = fresh_ping(api, headers)
            if measured is None:
                warn("Fresh ping unavailable, keeping current delay")
            elif auto:
                ping = measured
                delay = timing.pick_delay(ping, history)
                target = midnight - timedelta(seconds=delay)
                info(f"Fresh ping: {ping:.0f} ms")
                info(f"Updated delay: {delay * 1000:.0f} ms")
                info(f"Updated target: {target.strftime('%H:%M:%S.%f')} (GMT+8)")
            else:
                info(f"Connection ready, ping: {measured:.0f} ms")
            continue

        if left > 20:
            print(f"\r{paint('[>]', Orange)} Waiting: {timing.wait_text(left)}", end="", flush=True)
            time.sleep(min(left - 20, 30))
        elif left > 1:
            time.sleep(0.05)
        else:
            time.sleep(0.001)


def run():
    banner()
    auto = ask_yes("\nUse delay calculator? [y/N]: ")
    use_history = auto and ask_yes("Use saved timing history? [Y/n]: ", default=True)
    history = timing.clean_history(read_json(timing.HistoryFile, [])) if use_history else []

    web = xiaomi.new_web()
    try:
        account, was_saved = get_login(web)
        try:
            token = xiaomi.get_service_token(web, account)
        except RuntimeError:
            if not was_saved:
                raise
            warn("Saved login expired, opening a new login")
            remove_file(xiaomi.SessionFile)
            web.cookies.clear()
            account = new_login(web)
            token = xiaomi.get_service_token(web, account)
    except (ConnectionError, RuntimeError) as exc:
        fail(str(exc))
        return 1
    finally:
        web.close()

    version = xiaomi.app_version()
    info(f"Mi Community version: {version}")
    headers = xiaomi.api_headers(account, token, version)
    with xiaomi.new_api() as api:
        try:
            status, ping = xiaomi.state_call(api, headers)
        except ConnectionError as exc:
            fail(str(exc))
            return 1

        print()
        info(status["message"])
        if status["code"] == 1:
            return 0
        if status["code"] != 2:
            return 1

        if auto:
            delay = timing.pick_delay(ping, history)
            if timing.valid_ping(ping):
                info(f"Ping: {ping:.0f} ms")
            else:
                warn(f"Ping ignored: {ping:.0f} ms")
            if use_history:
                info(f"Saved tries: {len(history)}")
            info(f"Calculated delay: {delay * 1000:.0f} ms")
        else:
            delay = ask_delay()
            info(f"Delay: {delay * 1000:.0f} ms")

        try:
            start, mono = timing.synced_clock()
        except ConnectionError as exc:
            fail(str(exc))
            return 1

        midnight = (start + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        target, sent, ping = wait_for_send(
            api, headers, midnight, delay, auto, history, start, mono, ping
        )

        try:
            response = xiaomi.api_call(
                api,
                "POST",
                xiaomi.ApplyPath,
                headers,
                body={"is_retry": True},
                timeout=15
            )
        except (ValueError, ConnectionError) as exc:
            fail(f"Request failed: {exc}")
            warn("It was not sent again because the server may have received it")
            return 1

        good(f"Sent at: {sent.strftime('%H:%M:%S.%f')} (GMT+8)")
        server_ts = response.get("ts", 0)
        if isinstance(server_ts, (int, float)) and server_ts > 0:
            server_time = datetime.fromtimestamp(server_ts, timezone.utc).astimezone(timing.ChinaTime)
            info(f"Server response: {server_time.strftime('%H:%M:%S')} (GMT+8)")
        print()
        info(xiaomi.parse_apply(response))

        if auto and use_history:
            wake = max(0, (sent - target).total_seconds() * 1000)
            history = timing.add_history(history, ping, wake)
            write_json(timing.HistoryFile, history)

        return 0


def main():
    just_fix_windows_console()
    try:
        return run()
    except KeyboardInterrupt:
        print()
        warn("Stopped by user")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
