# SeatRadar Testing - Quick Checklist

Complete testing in 7 steps. Estimated time: **30-45 minutes**.

## Step 1: Setup & Installation (5 min)

```bash
# Navigate to project
cd c:\Abhishek\SeatRadar

# Create virtual environment (optional)
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright
playwright install chromium

# Verify everything works
python quick_test.py
```

**✓ Expected Output:**
```
============================================================
TEST SUMMARY
============================================================
Total: 8
Passed: 8
Failed: 0

============================================================
✓ ALL TESTS PASSED!
============================================================
```

If `quick_test.py` passes → **Move to Step 2**

## Step 2: Configure Your Event (5 min)

```bash
# Copy example config
cp config.json.example config.json

# Edit config.json with your details:
# - Copy a BookMyShow event URL
# - Set reasonable preferred_seats (e.g., "Gold", "Platinum")
# - For now, set "notify_methods": []
```

**Edit config.json:**
```json
{
  "show_name": "Your Movie - Your City",
  "show_url": "https://in.bookmyshow.com/events/your-movie-name/ET00123456",
  "preferred_seats": ["Gold", "Platinum", "Row A"],
  "check_interval_sec": 60,
  "stop_after_first_alert": false,
  "headless": true,
  "notify_methods": [],
  "notifiers": { ... }
}
```

**✓ Expected:** File edits without errors

## Step 3: Test Notifications (10 min)

**Only if you want to test SMS/WhatsApp/Calls. Otherwise skip to Step 4.**

### Option A: Use CallMeBot SMS (Recommended - Free)

```bash
# 1. Visit: https://www.callmebot.com/sms.php
# 2. Enter your phone number (+1234567890)
# 3. Get API key (it's "0" for free)

# 2. Edit config.json:
# "notify_methods": ["sms"],
# "notifiers": {
#   "callmebot": {
#     "phone": "+919876543210"
#   }
# }

# 3. Test
python test_notifications.py
```

**✓ Expected:** You receive SMS within 30 seconds

### Option B: Use Twilio Voice Calls (Free $15 Trial)

```bash
# 1. Create account at https://www.twilio.com/console
# 2. Get: Account SID, Auth Token, Phone Number
# 3. Edit config.json with credentials
# 4. Test
python test_notifications.py
```

**✓ Expected:** You receive voice call within 1 minute

**⚠ Skip this step if:**
- You don't want notifications yet
- You want to test scraper first
- Just set `"notify_methods": []` in config.json

## Step 4: Test Scraper with Real Page (10 min)

This is the core test - verify seats are correctly scraped.

```bash
# 1. Make sure config.json has a valid event URL

# 2. Run single check
python run_once.py
```

**✓ Expected Output (Option 1 - Seats Found):**
```
============================================================
SeatRadar Single Check
============================================================
Checking Your Movie - Your City
Navigating to https://in.bookmyshow.com/...
Page loaded successfully
Extracted seat labels: Gold, Platinum, Silver...
✓ Found seats: Gold, Platinum
Alert sent successfully
```
Exit code: `0` ✓

**✓ Expected Output (Option 2 - No Seats):**
```
Checking Your Movie - Your City
Navigating to https://...
❌ No preferred seats available
```
Exit code: `1` ✓

**✗ Troubleshooting if it fails:**

| Problem | Solution |
|---------|----------|
| "Invalid URL" or timeout | Verify URL is correct, try in browser first |
| "No seat labels found" | Set `"headless": false` to see browser, check manually if seats exist |
| "Event is sold out" | Try a different event |
| Playwright error | Run: `playwright install chromium` |

## Step 5: Test Continuous Monitoring (10 min)

```bash
# 1. Edit config.json:
# - Set "check_interval_sec": 10  (for quick testing)
# - Keep "preferred_seats" same as Step 4

# 2. Start watcher
python watcher.py

# 3. Let it run for 3-4 checks (30-40 seconds)

# 4. Press Ctrl+C to stop
```

**✓ Expected Logs:**
```
--- Check #1 ---
Checking Your Movie - Your City at 2024-01-15 14:30:55
Navigating to...
Found seat labels: Gold, Platinum
Matched preferred: Gold
🎉 Found new seats: Gold
Alert sent (or "No notifiers configured")
Waiting 10s until next check...

--- Check #2 ---
Checking Your Movie - Your City
All matched seats already alerted: Gold
Waiting 10s until next check...
```

**✓ Expected:** Duplicate alert NOT sent in Check #2

## Step 6: Verify Logs (5 min)

```bash
# Check logs were created
ls -la logs\

# View today's log
type logs\seatradar_20240115.log

# Should contain:
# - Timestamps
# - Navigation logs
# - Seat extraction logs  
# - Alert logs
```

**✓ Expected Log Entry:**
```
2024-01-15 14:30:55 [INFO] watcher: Checking Your Movie
2024-01-15 14:30:56 [INFO] scraper.bookmyshow: Navigating to https://...
2024-01-15 14:30:58 [DEBUG] scraper.bookmyshow: Extracted seats: Gold, Platinum...
2024-01-15 14:30:59 [INFO] notifiers.dispatcher: Alert sent
```

## Step 7: Final Integration Test (5 min)

```bash
# Reset to production-like config
# Edit config.json:
{
  "show_name": "Your Event",
  "show_url": "https://in.bookmyshow.com/...",
  "preferred_seats": ["Gold"],
  "check_interval_sec": 60,
  "notify_methods": ["sms"]  # or [] if skipped Step 3
}

# Run once more
python run_once.py

# Check exit code
echo %ERRORLEVEL%  # Windows
# or
echo $?  # Mac/Linux
```

**✓ Expected:**
- Exit code `0` if found seats
- Exit code `1` if no seats or error
- Notification received (if enabled)
- Log entry created

---

## Complete Test Passed! ✓

Ready to deployment:

### Local Monitoring
```bash
python watcher.py
```

### GitHub Actions Setup
1. Push code to GitHub
2. Add secret: `CONFIG_JSON` = your config.json content
3. Workflow runs every 2 minutes automatically

### Next Steps
- [ ] Set `check_interval_sec` to 60 (normal monitoring)
- [ ] Enable notifications in config
- [ ] Deploy to GitHub Actions
- [ ] Monitor logs daily

---

## Troubleshooting Quick Ref

| Issue | Command | Fix |
|-------|---------|-----|
| Imports fail | `python quick_test.py` | Check Step 1 |
| Playwright error | `playwright install chromium` | Reinstall browser |
| No seats found | `python run_once.py` with `headless: false` | Check URL/selectors |
| SMS not received | `python test_notifications.py` | Verify phone number |
| Exit code 1 | Check logs in `logs/` | Read detailed error |
| GitHub Actions fails | Check `CONFIG_JSON` secret | Ensure valid JSON |

---

## Sample Test Session Output

```
C:\Abhishek\SeatRadar> python quick_test.py
============================================================
SeatRadar Quick Test Suite
Started: 2024-01-15 14:30:45
============================================================

============================================================
Step 1: Testing Imports
============================================================
✓ config.settings imported
✓ scraper.bookmyshow imported
✓ notifiers.dispatcher imported
✓ notifiers.callmebot_sms imported
✓ notifiers.callmebot_whatsapp imported
✓ notifiers.twilio_call imported

============================================================
Step 2: Testing Configuration
============================================================
✓ config.json.example is valid JSON
✓ All required config fields present in example
ℹ config.json not yet created (using example)

============================================================
Step 3: Testing Scraper Initialization
============================================================
ℹ Creating scraper instance...
✓ Playwright browser initialized
✓ Playwright browser closed cleanly

============================================================
Step 4: Testing Notifiers
============================================================
✓ NotificationDispatcher initialized with empty config

============================================================
Step 5: Testing State Files
============================================================
✓ State file path generated: logs\TestShow_alerted_seats.json
✓ Logs directory exists: logs

============================================================
Step 6: Testing Logging
============================================================
ℹ Logs directory will be created on first run
✓ Logging system ready

============================================================
Step 7: Verifying File Structure
============================================================
✓ watcher.py
✓ run_once.py
✓ test_notifications.py
✓ requirements.txt
✓ config.json.example
✓ .gitignore
✓ README.md
✓ config/settings.py
✓ scraper/bookmyshow.py
✓ notifiers/dispatcher.py
✓ notifiers/callmebot_sms.py
✓ notifiers/callmebot_whatsapp.py
✓ notifiers/twilio_call.py
✓ .github/workflows/watch.yml

============================================================
Step 8: Checking Dependencies
============================================================
✓ playwright installed
✓ aiohttp installed
✓ twilio installed
✓ Playwright Chromium installed and working

============================================================
TEST SUMMARY
============================================================
Total: 8
Passed: 8
Failed: 0

============================================================
✓ ALL TESTS PASSED!
============================================================

Next steps:
1. Edit config.json with your BookMyShow event details
2. Run: python test_notifications.py
3. Run: python run_once.py
4. Run: python watcher.py

C:\Abhishek\SeatRadar>
```

Once this shows ✓ ALL TESTS PASSED, follow the 7 steps above to fully test the application!
