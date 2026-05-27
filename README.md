# SeatRadar

SeatRadar is a BookMyShow monitoring system with:

- continuous background checks
- phone-number login with OTP via Twilio Verify
- booking-open detection
- section alerts like `Gold`, `Platinum`, `Director Choice`
- exact-seat alerts like `A2`, `A3`
- contiguous-seat detection for a requested ticket count
- Twilio SMS, WhatsApp, and voice-call notifications
- a local web dashboard for authenticated users

## What You Can Monitor

- `booking open`: notify as soon as the seat-selection flow goes live
- `preferred sections`: notify when a section like `Gold` is available
- `exact seats`: notify when seats like `A2`, `A3` are available
- `seat count`: notify when enough contiguous seats are open together

## Install

```bash
pip install -r requirements.txt
playwright install chromium
```

## Configure Twilio

Fill [config.json.example](/c:/Abhishek/SeatRadar/config.json.example) into `config.json`.

Important fields:

- `notify_methods`: any mix of `["sms"]`, `["whatsapp"]`, `["call"]`
- `notify_on_initial_status`
- `notifiers.twilio.account_sid`
- `notifiers.twilio.auth_token`
- `notifiers.twilio.from_number`
- `notifiers.twilio.whatsapp_from_number`
- `notifiers.twilio.to_number`
- `notifiers.twilio.verify_service_sid`

You can still keep one default watch in `config.json` for the CLI commands, and the web UI stores additional watches in `data/watchlists.json`.

## Run The Web App

```bash
uvicorn app:app --reload
```

Then open:

```text
http://127.0.0.1:8000
```

From the dashboard you can:

- log in with phone OTP
- create a watch
- set movie, cinema hall, show time, and seat-layout URL
- choose sections, exact seats, and ticket count
- choose SMS, WhatsApp, and/or call per watch
- run a watch immediately
- pause/resume watches
- delete watches

Leave the web app running if you want continuous monitoring.

## CLI Commands

Test notifications only:

```bash
python test_notifications.py
```

Run one live check with the watch in `config.json`:

```bash
python run_once.py
```

Run continuous monitoring for the watch in `config.json`:

```bash
python watcher.py
```

Run a quick sanity suite:

```bash
python quick_test.py
```

## Recommended Flow

1. Configure Twilio in `config.json`
2. Run `python test_notifications.py`
3. Start the dashboard with `uvicorn app:app --reload`
4. Sign in with phone OTP
5. Create one or more watches in the UI
6. Keep the app running while SeatRadar monitors in the background

## Notes About Accuracy

BookMyShow layouts vary, so SeatRadar uses a mix of:

- visible text extraction
- layout geometry
- row-label detection
- seat color and style heuristics

That makes it much more useful than the original text-only matcher, but some layouts may still need tuning in [scraper/bookmyshow.py](/c:/Abhishek/SeatRadar/scraper/bookmyshow.py).

## Files

- [app.py](/c:/Abhishek/SeatRadar/app.py): FastAPI dashboard
- [monitor.py](/c:/Abhishek/SeatRadar/monitor.py): background monitoring and watch storage
- [models.py](/c:/Abhishek/SeatRadar/models.py): watch and snapshot models
- [scraper/bookmyshow.py](/c:/Abhishek/SeatRadar/scraper/bookmyshow.py): BookMyShow scraper
- [notifiers/dispatcher.py](/c:/Abhishek/SeatRadar/notifiers/dispatcher.py): notification routing
- [notifiers/twilio_sms.py](/c:/Abhishek/SeatRadar/notifiers/twilio_sms.py): Twilio SMS
- [notifiers/twilio_whatsapp.py](/c:/Abhishek/SeatRadar/notifiers/twilio_whatsapp.py): Twilio WhatsApp
- [notifiers/twilio_call.py](/c:/Abhishek/SeatRadar/notifiers/twilio_call.py): Twilio voice call
- [auth.py](/c:/Abhishek/SeatRadar/auth.py): Twilio Verify OTP helpers

## Troubleshooting

If the browser opens a fresh seat-count prompt:

- that is normal for a new session on some BookMyShow flows
- keep `headless` false while debugging
- use the same seat-layout URL from BookMyShow

If you see Cloudflare or captcha-like pages:

- try visible mode first
- complete the challenge manually
- rerun the check

If no seats are detected:

- verify the show URL manually in your browser
- try broad sections first like `Gold`
- then add exact seats like `A2`, `A3`

If SMS/WhatsApp/call fails:

- verify the Twilio number format uses country code
- verify your trial account destination number is approved in Twilio
