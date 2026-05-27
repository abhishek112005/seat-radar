# SeatRadar Testing Guide

Complete testing procedures for all components and the full application.

## Testing Pyramid

```
┌─────────────────────────┐
│   Full Integration      │  (Real BookMyShow page)
├─────────────────────────┤
│   Component Tests       │  (Scraper, Notifiers)
├─────────────────────────┤
│   Unit Tests / Manual   │  (Config, Utilities)
└─────────────────────────┘
```

## Phase 1: Setup Validation (Pre-Testing)

### 1.1 Verify Installation

```bash
# Navigate to project
cd c:\Abhishek\SeatRadar

# Check Python version (3.8+)
python --version

# Create virtual environment (optional but recommended)
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser
playwright install chromium

# Verify imports work
python -c "from config.settings import load_config; print('✓ Config imports OK')"
python -c "from scraper.bookmyshow import BookMyShowScraper; print('✓ Scraper imports OK')"
python -c "from notifiers.dispatcher import NotificationDispatcher; print('✓ Notifiers imports OK')"
```

Expected output:
```
✓ Config imports OK
✓ Scraper imports OK
✓ Notifiers imports OK
```

### 1.2 Verify File Structure

```bash
# Check all required files exist
ls -R

# Should see:
# - config/settings.py
# - scraper/bookmyshow.py
# - notifiers/*.py
# - watcher.py, run_once.py, test_notifications.py
# - requirements.txt, config.json.example
# - .github/workflows/watch.yml
# - README.md, TESTING.md
```

## Phase 2: Configuration Testing

### 2.1 Config Load Test

```python
# test_config_load.py
import asyncio
from config.settings import load_config, validate_config

async def test_config():
    try:
        # Copy example first
        import shutil
        shutil.copy("config.json.example", "config.json")
        
        config = await load_config("config.json")
        print("✓ Config loaded successfully")
        print(f"  Show: {config['show_name']}")
        print(f"  URL: {config['show_url'][:50]}...")
        print(f"  Preferred seats: {config['preferred_seats']}")
        print(f"  Check interval: {config['check_interval_sec']}s")
        
        # Config should have defaults merged
        assert config.get("headless") == True, "Missing default: headless"
        assert config.get("notifiers"), "Missing notifiers section"
        
        print("✓ All default values present")
        
        return True
    except FileNotFoundError as e:
        print(f"✗ {e}")
        return False

asyncio.run(test_config())
```

Run:
```bash
python test_config_load.py
```

### 2.2 Config Validation Test

```python
# test_config_validation.py
import asyncio
from config.settings import load_config, validate_config
import json

async def test_validation():
    # Test 1: Missing URL should fail
    with open("config.json", "r") as f:
        config = json.load(f)
    
    config["show_url"] = ""
    with open("config_test.json", "w") as f:
        json.dump(config, f)
    
    try:
        test_cfg = await load_config("config_test.json")
        await validate_config(test_cfg)
        print("✗ Should have rejected empty URL")
    except ValueError as e:
        print(f"✓ Correctly rejected: {e}")
    
    # Test 2: SMS without phone should fail
    config["notify_methods"] = ["sms"]
    config["notifiers"]["callmebot"]["phone"] = ""
    with open("config_test.json", "w") as f:
        json.dump(config, f)
    
    try:
        test_cfg = await load_config("config_test.json")
        await validate_config(test_cfg)
        print("✗ Should have rejected SMS without phone")
    except ValueError as e:
        print(f"✓ Correctly rejected: {e}")
    
    print("✓ All validation tests passed")

asyncio.run(test_validation())
```

## Phase 3: Notification Testing (Recommended First)

**Test notifications BEFORE testing the scraper.**

### 3.1 Test Notification Setup

```bash
# 1. Get a real phone number (for CallMeBot) or Twilio account

# 2. Edit config.json with credentials:
# - For SMS: Add your phone number (+1234567890)
# - For WhatsApp: Add WhatsApp number after setup
# - For Twilio: Add account SID, token, etc.

# 3. Update notify_methods in config.json:
# "notify_methods": ["sms"]

# 4. Run test
python test_notifications.py
```

Expected:
- ✅ You receive SMS/WhatsApp/call within 30 seconds
- ✅ Log shows "sent successfully"

### 3.2 Test Each Notifier Individually

**Test SMS Notifier:**

```python
# test_sms.py
import asyncio
from notifiers.callmebot_sms import CallMeBotSMSNotifier

async def test_sms():
    notifier = CallMeBotSMSNotifier("+919876543210")  # Your number
    success = await notifier.send("🎬 SeatRadar Test SMS - If you see this, SMS works!")
    print(f"SMS Test: {'✓ Success' if success else '✗ Failed'}")

asyncio.run(test_sms())
```

**Test WhatsApp Notifier:**

```python
# test_whatsapp.py
import asyncio
from notifiers.callmebot_whatsapp import CallMeBotWhatsAppNotifier

async def test_whatsapp():
    notifier = CallMeBotWhatsAppNotifier("+919876543210")  # Your WhatsApp number
    success = await notifier.send("🎬 SeatRadar Test WhatsApp - If you see this, WhatsApp works!")
    print(f"WhatsApp Test: {'✓ Success' if success else '✗ Failed'}")

asyncio.run(test_whatsapp())
```

**Test Twilio Voice Call:**

```python
# test_twilio.py
import asyncio
from notifiers.twilio_call import TwilioCallNotifier

async def test_twilio():
    notifier = TwilioCallNotifier(
        account_sid="ACxxxxxxxxxxxxxxxx",
        auth_token="your_auth_token",
        from_number="+1234567890",
        to_number="+919876543210"
    )
    success = await notifier.send("SeatRadar test. This is a test voice call.")
    print(f"Twilio Test: {'✓ Success' if success else '✗ Failed'}")

asyncio.run(test_twilio())
```

**Test Dispatcher (All at once):**

```bash
# This is what the main app uses
python test_notifications.py
```

## Phase 4: Scraper Testing

### 4.1 Test Scraper Initialization

```python
# test_scraper_init.py
import asyncio
from scraper.bookmyshow import BookMyShowScraper

async def test_init():
    scraper = BookMyShowScraper(headless=True)
    try:
        await scraper.init()
        print("✓ Playwright browser initialized")
        await scraper.close()
        print("✓ Playwright browser closed cleanly")
    except Exception as e:
        print(f"✗ Scraper init failed: {e}")

asyncio.run(test_init())
```

### 4.2 Test with a Real Event Page

```python
# test_scraper_real.py
import asyncio
from scraper.bookmyshow import BookMyShowScraper

async def test_scraper():
    # Use a REAL BookMyShow event URL
    # Find one at https://in.bookmyshow.com/events/
    test_url = "https://in.bookmyshow.com/events/movie-title-where-you-live/ET00123456"
    
    scraper = BookMyShowScraper(headless=False)  # Show browser for debugging
    await scraper.init()
    
    try:
        print("Navigating to event page...")
        page = await scraper.navigate_to_booking(test_url)
        
        if not page:
            print("✗ Failed to navigate")
            return
        
        print("✓ Page loaded successfully")
        
        # Extract seats
        seats = await scraper.extract_seat_labels(page)
        print(f"✓ Found {len(seats)} seat categories:")
        for seat in sorted(seats)[:10]:  # Show first 10
            print(f"  - {seat}")
        
        # Check if sold out
        is_sold = await scraper.check_sold_out(page)
        print(f"Sold out: {is_sold}")
        
        await page.close()
        
    except Exception as e:
        print(f"✗ Scraper error: {e}")
    finally:
        await scraper.close()

asyncio.run(test_scraper())
```

Run with headless=False to see the browser:
```bash
python test_scraper_real.py
# Browser will open, show page being scraped
# Check if seats are correctly extracted
```

### 4.3 Test Seat Filtering

```python
# test_seat_filtering.py
import asyncio
from scraper.bookmyshow import BookMyShowScraper

async def test_filtering():
    scraper = BookMyShowScraper(headless=True)
    
    # Test case 1: Simple matching
    seats = {"Gold", "Silver", "Bronze", "Platinum"}
    preferred = ["Gold"]
    filtered = await scraper.filter_available_seats(seats, preferred)
    assert "Gold" in filtered, "Failed to match Gold"
    print("✓ Test 1: Simple matching - PASSED")
    
    # Test case 2: Case-insensitive
    seats = {"GOLD PREMIUM", "Silver"}
    preferred = ["gold"]
    filtered = await scraper.filter_available_seats(seats, preferred)
    assert len(filtered) == 1, "Case-insensitive matching failed"
    print("✓ Test 2: Case-insensitive - PASSED")
    
    # Test case 3: Sold-out filtering
    seats = {"Gold Available", "Silver Sold Out", "Platinum Disabled"}
    preferred = ["gold", "silver", "platinum"]
    filtered = await scraper.filter_available_seats(seats, preferred)
    assert "Gold Available" in filtered, "Should include Gold Available"
    assert len(filtered) == 1, "Should filter out sold/disabled"
    print("✓ Test 3: Sold-out filtering - PASSED")
    
    # Test case 4: Substring matching
    seats = {"Row A Gold", "Row B Silver", "Row C Gold"}
    preferred = ["Row A"]
    filtered = await scraper.filter_available_seats(seats, preferred)
    assert len(filtered) == 1, "Should match substring"
    print("✓ Test 4: Substring matching - PASSED")

asyncio.run(test_filtering())
```

## Phase 5: Full Integration Testing

### 5.1 Single Run Test (`run_once.py`)

**Before running:** Make sure you have a real BookMyShow event URL in config.json

```bash
# Update config.json with:
# 1. Real event URL
# 2. Realistic preferred_seats that might exist
# 3. At least one notification method enabled (or comment out notify_methods)

python run_once.py
```

Expected outcomes:

| Exit Code | Meaning | What to Check |
|-----------|---------|---------------|
| 0 | ✓ Seats found, alert sent | Check notification received |
| 1 | ✗ No seats found or error | Check scraper logs, verify URL |
| 2 | ✗ Seats found but alert failed | Check notification credentials |

### 5.2 Continuous Monitoring Test (`watcher.py`)

```bash
# Start watcher with 10-second intervals for testing
# Edit config.json: "check_interval_sec": 10

python watcher.py

# Let it run for 3-4 cycles (30-40 seconds)
# Observe logs:
# - Each check iteration
# - Seat matching
# - Alert sending

# Press Ctrl+C to stop
```

Expected log output:
```
==============================================================
SeatRadar Watcher Started
==============================================================
Loaded 0 previously alerted seats
Check interval: 10 seconds

--- Check #1 ---
Checking My Event at 2024-01-15 14:30:55
Navigating to https://in.bookmyshow.com/...
✓ Page loaded
Extracted seat labels: {'Gold', 'Silver', 'Platinum'}
Matched preferred seats: {'Gold'}
🎉 Found new seats: {'Gold'}
Alert sent. Total alerted: {'Gold'}
Waiting 10s until next check...

--- Check #2 ---
Checking My Event at 2024-01-15 14:31:05
All matched seats already alerted: {'Gold'}
Waiting 10s until next check...

--- Check #3 ---
No preferred seats available
```

### 5.3 State File Test

```python
# test_state_file.py
import json
from pathlib import Path
from config.settings import get_state_file_path

def test_state():
    # Get state file path
    state_file = get_state_file_path("My Event")
    print(f"State file: {state_file}")
    
    # Check it exists and is valid JSON
    if state_file.exists():
        with open(state_file, "r") as f:
            data = json.load(f)
        
        print(f"✓ State file is valid JSON")
        print(f"  Alerted seats: {data['alerted_seats']}")
        print(f"  Last updated: {data['last_updated']}")
    else:
        print("ℹ State file doesn't exist yet (will be created on first alert)")

test_state()
```

## Phase 6: Logging Verification

### 6.1 Check Log Files

```bash
# List log files
ls -la logs/

# View today's log
type logs\seatradar_20240115.log

# Check log levels
# Should see: DEBUG, INFO, WARNING, ERROR entries
```

**Sample log entry:**
```
2024-01-15 14:30:55,123 [INFO] watcher: Checking My Event at 2024-01-15 14:30:55
2024-01-15 14:30:56,456 [INFO] scraper.bookmyshow: Navigating to https://...
2024-01-15 14:30:58,789 [DEBUG] scraper.bookmyshow: Extracted seat labels: {'Gold', 'Silver'}
2024-01-15 14:30:59,012 [INFO] scraper.bookmyshow: Matched preferred seat: 'Gold'
2024-01-15 14:30:59,345 [INFO] watcher: 🎉 Found new seats: {'Gold'}
2024-01-15 14:30:59,678 [INFO] notifiers.dispatcher: SMS notifier: sent
```

### 6.2 Log Rotation

```python
# test_logging.py
from pathlib import Path
import logging
from datetime import datetime

def test_logs():
    logs_dir = Path("logs")
    log_files = list(logs_dir.glob("seatradar_*.log"))
    
    if log_files:
        print(f"✓ Found {len(log_files)} log files")
        for log_file in sorted(log_files)[-3:]:  # Show last 3
            size = log_file.stat().st_size
            print(f"  - {log_file.name} ({size} bytes)")
    else:
        print("ℹ No log files yet (created after first run)")

test_logs()
```

## Phase 7: Edge Cases & Error Handling

### 7.1 Sold Out Event

```python
# test_sold_out_handling.py
import asyncio
from scraper.bookmyshow import BookMyShowScraper

async def test():
    scraper = BookMyShowScraper(headless=True)
    await scraper.init()
    
    # Find a SOLD OUT BookMyShow event
    sold_out_url = "https://in.bookmyshow.com/events/sold-out-movie/ET00000000"
    
    try:
        available, matched = await scraper.scrape_and_check(
            sold_out_url,
            ["Gold", "Silver"]
        )
    except ValueError as e:
        print(f"✓ Correctly caught sold out: {e}")
    
    await scraper.close()

asyncio.run(test())
```

### 7.2 Invalid/Broken URL

```bash
# config.json: "show_url": "https://invalid.example.com"

python run_once.py

# Should handle gracefully with error log
# Exit code: 1
```

### 7.3 No Matching Seats

```bash
# config.json: "preferred_seats": ["VeryUniqueSeatName"]

python run_once.py

# Should find page but no matches
# Exit code: 1
# Log: "No preferred seats available"
```

### 7.4 Network Timeout

Simulate by:
1. Running without internet
2. Or adding delay in browser

Expected: Timeout error in logs, graceful exit

## Phase 8: GitHub Actions Testing

### 8.1 Local Workflow Simulation

```bash
# Install act (GitHub Actions runner locally)
# https://github.com/nektos/act

act -j watch

# Or manually run the commands:
python -m pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
echo '{"show_name": "test", ...}' > config.json
python run_once.py
```

### 8.2 Manual GitHub Actions Test

1. Push to GitHub with config secret set
2. Go to **Actions** tab
3. Select **SeatRadar Watch**
4. Click **Run workflow** → **Run workflow**
5. Watch the run complete
6. Check logs in artifact

## Phase 9: Performance Testing

### 9.1 Page Load Time

```python
# test_performance.py
import asyncio
import time
from scraper.bookmyshow import BookMyShowScraper

async def test_perf():
    scraper = BookMyShowScraper(headless=True)
    await scraper.init()
    
    start = time.time()
    page = await scraper.navigate_to_booking(
        "https://in.bookmyshow.com/events/..."
    )
    elapsed = time.time() - start
    
    print(f"Page load time: {elapsed:.2f}s")
    print(f"✓ Acceptable" if elapsed < 10 else f"✗ Slow (> 10s)")
    
    await page.close()
    await scraper.close()

asyncio.run(test_perf())
```

**Target:** < 10 seconds per page load

### 9.2 Full Check Duration

```python
# test_full_check_time.py
import asyncio
import time
from config.settings import load_config
from scraper.bookmyshow import BookMyShowScraper

async def test():
    config = await load_config("config.json")
    scraper = BookMyShowScraper(headless=True)
    await scraper.init()
    
    start = time.time()
    available, matched = await scraper.scrape_and_check(
        config["show_url"],
        config["preferred_seats"]
    )
    elapsed = time.time() - start
    
    print(f"Total check time: {elapsed:.2f}s")
    print(f"✓ Acceptable" if elapsed < 15 else f"⚠ Slow")
    
    await scraper.close()

asyncio.run(test())
```

## Phase 10: End-to-End Checklist

```
PRE-DEPLOYMENT CHECKLIST
========================

[ ] Setup & Installation
  [ ] Python 3.8+ installed
  [ ] All dependencies installed (pip install -r requirements.txt)
  [ ] Playwright Chromium installed
  [ ] All files present (ls -R shows complete structure)

[ ] Configuration
  [ ] config.json created from example
  [ ] show_url is valid BookMyShow event
  [ ] preferred_seats is meaningful list
  [ ] notify_methods has at least one channel
  [ ] Credentials filled for enabled methods

[ ] Notifications (TEST BEFORE SCRAPER)
  [ ] SMS: test_notifications.py receives SMS
  [ ] WhatsApp: test_notifications.py receives message
  [ ] Call: test_notifications.py receives call
  [ ] OR at least one method tested successfully

[ ] Scraper
  [ ] test_scraper_init.py completes without error
  [ ] test_scraper_real.py shows page in browser
  [ ] Seat labels are correctly extracted
  [ ] Sold-out detection works

[ ] Single Run
  [ ] python run_once.py completes
  [ ] Exit code matches expected (0, 1, or 2)
  [ ] Logs appear in logs/ directory
  [ ] Notification sent (if seats found)

[ ] Watcher
  [ ] python watcher.py runs without crashing
  [ ] Multiple checks complete
  [ ] State file created (logs/)
  [ ] Duplicate alerts prevented

[ ] Logging
  [ ] logs/ directory has daily files
  [ ] Log contains all check attempts
  [ ] Log level filtering works

[ ] Error Handling
  [ ] Invalid URL handled gracefully
  [ ] Missing config rejected
  [ ] Notification failures logged
  [ ] No unhandled exceptions

[ ] GitHub Actions (if deploying)
  [ ] CONFIG_JSON secret created
  [ ] Workflow file exists (.github/workflows/watch.yml)
  [ ] Manual trigger test succeeds
  [ ] Logs artifact downloads

DEPLOYMENT READY
================
All boxes checked? Deploy with confidence!
```

## Quick Test Commands Reference

```bash
# 1. Setup
pip install -r requirements.txt
playwright install chromium

# 2. Config
cp config.json.example config.json
# Edit config.json

# 3. Notifications (FIRST!)
python test_notifications.py

# 4. Scraper
python -c "from test_scraper_real import *; import asyncio; asyncio.run(test_scraper())"

# 5. Single run
python run_once.py

# 6. Continuous
python watcher.py

# 7. Check logs
type logs\seatradar_*.log

# All in one (if everything in config.json is pre-filled):
python test_notifications.py && python run_once.py && python -c "print('✓ All tests passed!')"
```

## Debugging Tips

**Browser won't load page:**
- Set `"headless": false` to see what's happening
- Check network connectivity
- Verify URL is correct

**Seats not extracted:**
- Run with `headless: false` to manually inspect selectors
- Open DevTools (F12) on real BookMyShow to find correct CSS selectors
- Edit `scraper/bookmyshow.py` selectors list

**Notifications not working:**
- Run `test_notifications.py` individually first
- Check phone/WhatsApp setup (CallMeBot)
- Verify Twilio trial credit exists
- Check logs for API errors

**Duplicate alerts:**
- Verify state file is being saved (`logs/*_alerted_seats.json`)
- Run `python -c "import json; print(json.load(open('logs/..._alerted_seats.json')))"`

**GitHub Actions issues:**
- Verify CONFIG_JSON secret exists
- Check workflow file syntax
- Manually run workflow to see detailed error

## Continuous Integration Strategy

**Local Development:**
```
write code → test_notifications.py → test_scraper.py → run_once.py → watcher.py
```

**Before Commit:**
```
All tests pass → check logs look good → commit
```

**Deployment:**
```
Push → GitHub Actions runs automatically → Get notification on phone
```

