# MiStrike

Small Python tool for sending a Xiaomi bootloader unlock application at the target time.

## Setup

Install Python 3.11 or newer, then open the project folder and run:

```powershell
python -m pip install -r requirements.txt
```

## Features

- Browser login with optional saved session
- Delay calculator from current ping
- Optional saved timing history
- NTP time check before sending
- Fresh token and time check one minute before sending
- Fresh ping a few seconds before sending
- Simple colored terminal output
- Keeps waiting for the next opening after a failed try
- Checks a not ready account again before the next opening
- Saved results of every try

## Usage

Run:

```powershell
python main.py
```

Log in in the browser when it opens. You can save the login for next time. Do not share the saved `data/session.json` file, because it gives full access to your Xiaomi account.

Turn on the delay calculator if you want MiStrike to estimate the send delay. The program checks your account, waits for the Xiaomi target time, then sends one application request.

Every try is saved to `data/results.json`, and the last five are shown at start. `sent` is the send time from 00:00 (GMT+8) and `server` is the Xiaomi server time, so you can see if the try was early or late.

## Disclaimer

This is an unofficial tool and it is not connected to Xiaomi.

Use it at your own risk. You are responsible for your Xiaomi account, your device, and following Xiaomi rules. I am not responsible for account limits, failed applications, lost data, or any other problem from using this tool.
