# Chitim Course Enrollment Automation

Automation script that monitors an email inbox, creates a WordPress user, enrolls them in a course, and saves a draft reply — all triggered by a purchase-confirmation email.

---

## What it does

1. **Checks IMAP** (`chitim@zahav.net.il`) for unread emails from `support@grow.security` received in the last 15 minutes.
2. **Filters** emails that contain the phrase:
   > רכישת כניסה לקורס הגינון האקולוגי מורחב
3. **Extracts** the purchaser's email address from the line starting with `מייל:` in the email body, and derives a username (everything before `@`).
4. **Creates a WordPress user** via the REST API (`meshek.chitim.co.il`). If the user already exists, fetches their existing ID.
5. **Enrolls the user** in *קורס גינון אקולוגי מורחב* (course ID 1827) via the WP admin enrollment page.
6. **Saves a draft email** to the IMAP Drafts folder addressed to the purchaser with their login credentials and course links.
7. **Marks the email as read** Emails that don't match the phrase are marked back as unread.

---

## Requirements

- Python 3.11+
- Playwright with Chromium (installed automatically)
- AWS account with Secrets Manager (for production) or a `.env` file (for local development)

---

## Installation

```bash
# 1. Clone / navigate to the project folder
cd chitim-course-automation

# 2. Create a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install Playwright browsers (only needed once)
playwright install chromium
```

---

## Configuration

### Local development
Copy `.env.example` to `.env` and fill in all values:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `IMAP_HOST` | IMAP server hostname |
| `IMAP_PORT` | IMAP port (143 for plain, 993 for SSL) |
| `EMAIL_ADDRESS` | Full email address (`chitim@zahav.net.il`) |
| `EMAIL_PASSWORD` | Email account password |
| `WP_ADMIN_URL` | WordPress admin base URL |
| `WP_ADMIN_USER` | WordPress admin username |
| `WP_ADMIN_PASSWORD` | WordPress admin password |
| `NEW_USER_PASSWORD` | Default password for new users (default: `1234`) |
| `CHECK_INTERVAL` | Seconds between inbox checks (default: `300`) |

### AWS (production)
Store all variables above as a single JSON secret in **AWS Secrets Manager** named `chitim-course`.
The function auto-detects Lambda via the `AWS_LAMBDA_FUNCTION_NAME` environment variable and loads config from Secrets Manager instead of `.env`.

Grant the Lambda execution role `secretsmanager:GetSecretValue` on the secret ARN.

---

## Running the automation

### Locally
```bash
python main.py
```
The script runs once and exits. Use a scheduler (cron, Task Scheduler) to run it repeatedly.

### AWS Lambda
Deploy as a Lambda function and trigger it with **EventBridge** on a schedule (e.g. `rate(15 minutes)`).

> **Note:** Playwright requires a Lambda container image or a Lambda layer with Chromium — standard Lambda runtimes do not include a browser.

---

## Project structure

```
chitim-course-automation/
├── main.py              # Entry point — runs once per invocation
├── email_monitor.py     # IMAP polling, phrase detection, draft creation
├── wordpress_automation.py  # Playwright automation (user creation + enrollment)
├── config.py            # Loads config from Secrets Manager or .env
├── requirements.txt
├── .env.example
└── README.md
```

---

## Logs

All activity is printed to stdout with timestamps:

```bash
python main.py >> raw.log 2>&1
```
