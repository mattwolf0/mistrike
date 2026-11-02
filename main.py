import json
import os
import sys
import time
import webbrowser

from datetime import timedelta

from colorama import Fore, Style, just_fix_windows_console

import timing
import xiaomi


RefreshBefore = 60
WarmUpBefore = 3
CheckBefore = 300

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
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temp.open("w", encoding="utf-8") as file:
            json.dump(data, file)
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        temp.replace(path)
        return True
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


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
    good(f"Xiaomi account confirmed for {account['userId']}")
    if ask_yes("Remember this login? [Y/n]: ", default=True):
        if write_json(xiaomi.SessionFile, account):
            info(f"Session saved to {xiaomi.SessionFile}")
        else:
            warn("Could not save session, keeping it in memory only")
    else:
        info("Session is kept in memory only")
    return account


def get_login(web):
    saved = read_json(xiaomi.SessionFile, None)
    if xiaomi.valid_session(saved):
        print(f"\nSaved account: {paint(saved['userId'], Orange)}")
        choice = input("Enter to continue, or L to log out: ").strip().lower()
        if choice not in ("l", "logout"):
            return saved

        if remove_file(xiaomi.SessionFile):
            good("Saved login removed")

    return new_login(web)


def clear_wait():
    print("\r" + " " * 46 + "\r", end="", flush=True)


def wait_until(target, start, mono, stop=0):
    while True:
        now = timing.clock_now(start, mono)
        left = (target - now).total_seconds()
        if left <= stop:
            clear_wait()
            return now

        if left - stop > 20:
            print(f"\r{paint('[>]', Orange)} Waiting: {timing.wait_text(left)}", end="", flush=True)
            time.sleep(min(left - stop - 20, 30))
        elif left - stop > 1:
            time.sleep(0.05)
        else:
            time.sleep(0.001)


def next_midnight(start, last=None):
    midnight = (start + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if last is not None and midnight <= last:
        midnight += timedelta(days=1)
    return midnight


def next_check(start, last=None):
    check = next_midnight(start, last) - timedelta(seconds=CheckBefore)
    if check <= start:
        check += timedelta(days=1)
    return check


def fresh_ping(api, headers, timeout=5):
    try:
        status, ping = xiaomi.state_call(api, headers, timeout=timeout, tries=1)
        if status.get("code") == -1 or not timing.valid_ping(ping):
            return None
        return ping
    except ConnectionError:
        return None


def warm_up(api, headers, target, start, mono):
    ping = None
    for _ in range(2):
        if timing.seconds_left(target, start, mono) < 1:
            break
        measured = fresh_ping(api, headers, timeout=2)
        if measured is not None:
            ping = measured
    return ping


def new_headers(web, account, version, headers):
    try:
        token = xiaomi.get_service_token(web, account)
    except (ConnectionError, RuntimeError, ValueError) as exc:
        warn(f"Could not refresh token, keeping current token: {exc}")
        return headers
    info("Service token refreshed")
    return xiaomi.api_headers(account, token, version)


def sync_again(start, mono):
    try:
        new_start, new_mono = timing.synced_clock()
    except ConnectionError:
        warn("Time sync unavailable, keeping current time")
        return start, mono
    diff = timing.clock_now(new_start, new_mono) - timing.clock_now(start, mono)
    info(f"Time correction: {diff.total_seconds() * 1000:+.0f} ms")
    return new_start, new_mono


def wait_for_send(api, headers, midnight, target, auto, history, start, mono, ping):
    if timing.seconds_left(target, start, mono) > WarmUpBefore:
        wait_until(target, start, mono, WarmUpBefore)
    measured = warm_up(api, headers, target, start, mono)
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
        ping = measured
        info(f"Connection ready, ping: {measured:.0f} ms")

    sent = wait_until(target, start, mono)
    return target, sent, ping


def wait_for_check(start, mono, last):
    target = next_check(start, last)
    info(f"Next account check: {target.strftime('%H:%M:%S')} (GMT+8)")
    wait_until(target, start, mono)


def server_text(ts):
    server = timing.server_time(ts)
    if server is None:
        return None
    if ts > 1e11:
        return server.strftime("%H:%M:%S.%f")[:-3]
    return server.strftime("%H:%M:%S")


def save_result(results, midnight, target, sent, ping, answer=None, response=None, error=None):
    response = response or {}
    data = response.get("data") or {}
    results = timing.add_result(results, {
        "opening": midnight.strftime("%Y-%m-%d"),
        "delay": round((midnight - target).total_seconds() * 1000),
        "sent": round((sent - midnight).total_seconds() * 1000, 1),
        "ping": round(ping) if timing.valid_ping(ping) else None,
        "response": round(answer) if answer is not None else None,
        "code": response.get("code"),
        "result": data.get("apply_result"),
        "server_ts": response.get("ts"),
        "message": error or xiaomi.parse_apply(response)
    })
    write_json(timing.ResultsFile, results)
    return results


def show_results(results):
    if not results:
        return
    print()
    info("Last tries:")
    for item in results[-5:]:
        line = "    " + item["opening"]
        if isinstance(item.get("sent"), (int, float)):
            line += f"  sent {item['sent']:+.0f} ms"
        if isinstance(item.get("response"), (int, float)):
            line += f"  response {item['response']:.0f} ms"
        server = server_text(item.get("server_ts"))
        if server:
            line += f"  server {server}"
        line += f"  {item.get('message', '')}"
        print(line)


def run():
    banner()
    results = timing.clean_results(read_json(timing.ResultsFile, []))
    show_results(results)
    auto = ask_yes("\nUse delay calculator? [y/N]: ")
    use_history = auto and ask_yes("Use saved timing history? [Y/n]: ", default=True)
    history = timing.clean_history(read_json(timing.HistoryFile, [])) if use_history else []

    web = xiaomi.new_web()
    try:
        account = None
        manual_delay = None
        last_midnight = None
        with xiaomi.new_api() as api:
            while True:
                try:
                    if account is None:
                        account = get_login(web)
                    token = xiaomi.get_service_token(web, account)
                except xiaomi.LoginExpired:
                    warn("Login expired, opening a new login in one minute")
                    remove_file(xiaomi.SessionFile)
                    web.cookies.clear()
                    account = None
                    time.sleep(60)
                    continue
                except (ConnectionError, RuntimeError, ValueError) as exc:
                    fail(str(exc))
                    info("Trying login again in one minute")
                    time.sleep(60)
                    continue

                version = xiaomi.app_version()
                info(f"Mi Community version: {version}")
                headers = xiaomi.api_headers(account, token, version)
                try:
                    status, ping = xiaomi.state_call(api, headers)
                except ConnectionError as exc:
                    fail(str(exc))
                    info("Trying account check again in one minute")
                    time.sleep(60)
                    continue

                print()
                if status["code"] == -1:
                    fail(status["message"])
                    info("Trying account check again in one minute")
                    time.sleep(60)
                    continue

                info(status["message"])
                if status["code"] == 1:
                    return 0

                try:
                    start, mono = timing.synced_clock()
                except ConnectionError as exc:
                    fail(str(exc))
                    info("Trying time sync again in one minute")
                    time.sleep(60)
                    continue

                if status["code"] != 2:
                    warn("Account is not ready. Checking again before the next opening")
                    wait_for_check(start, mono, last_midnight)
                    continue

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
                    if manual_delay is None:
                        manual_delay = ask_delay()
                    delay = manual_delay
                    info(f"Delay: {delay * 1000:.0f} ms")

                midnight = next_midnight(start, last_midnight)
                target = midnight - timedelta(seconds=delay)
                info(f"Target: {target.strftime('%H:%M:%S.%f')} (GMT+8)")

                if timing.seconds_left(target, start, mono) > RefreshBefore:
                    wait_until(target, start, mono, RefreshBefore)
                    headers = new_headers(web, account, version, headers)
                    start, mono = sync_again(start, mono)

                target, sent, ping = wait_for_send(
                    api, headers, midnight, target, auto, history, start, mono, ping
                )
                last_midnight = midnight

                posted = time.perf_counter()
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
                    warn("It may have reached the server. Waiting for the next opening")
                    results = save_result(
                        results, midnight, target, sent, ping, error=f"Request failed: {exc}"
                    )
                    continue
                answer = (time.perf_counter() - posted) * 1000

                good(f"Sent at: {sent.strftime('%H:%M:%S.%f')} (GMT+8)")
                info(f"Response time: {answer:.0f} ms")
                server = server_text(response.get("ts"))
                if server:
                    info(f"Server response: {server} (GMT+8)")
                print()
                results = save_result(results, midnight, target, sent, ping, answer, response)

                if auto and use_history:
                    wake = max(0, (sent - target).total_seconds() * 1000)
                    history = timing.add_history(history, ping, wake)
                    write_json(timing.HistoryFile, history)

                message = xiaomi.parse_apply(response)
                if xiaomi.apply_done(response):
                    good(message)
                    return 0

                warn(message)
                warn("Application was not accepted. Waiting for the next opening")
    except (ConnectionError, RuntimeError) as exc:
        fail(str(exc))
        return 1
    finally:
        web.close()


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
