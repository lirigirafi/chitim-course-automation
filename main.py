"""
main.py
Entry point for the Chitim course-enrollment automation agent.

Flow:
  1. Poll IMAP inbox for purchase-confirmation emails from support@grow.security.
  2. For each matching email:
       a. Create a WordPress user (username = part before @ in purchaser's email).
       b. Enroll the user in the course.
       c. Save a draft reply with the credentials.
  3. Sleep and repeat.
"""

import logging

import config
from email_monitor import fetch_new_purchase_emails, create_draft
from wordpress_agent import WordPressAgent

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load configuration (Secrets Manager on Lambda, .env locally)
# ---------------------------------------------------------------------------
cfg = config.load()

IMAP_HOST = cfg["IMAP_HOST"]
IMAP_PORT = int(cfg["IMAP_PORT"])
EMAIL_ADDRESS = cfg["EMAIL_ADDRESS"]
EMAIL_PASSWORD = cfg["EMAIL_PASSWORD"]

WP_ADMIN_URL = cfg["WP_ADMIN_URL"]
WP_ADMIN_USER = cfg["WP_ADMIN_USER"]
WP_ADMIN_PASSWORD = cfg["WP_ADMIN_PASSWORD"]

NEW_USER_PASSWORD = cfg["NEW_USER_PASSWORD"]
CHECK_INTERVAL = int(cfg["CHECK_INTERVAL"])


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _check_config() -> bool:
    required = {
        "EMAIL_ADDRESS": EMAIL_ADDRESS,
        "EMAIL_PASSWORD": EMAIL_PASSWORD,
        "WP_ADMIN_USER": WP_ADMIN_USER,
        "WP_ADMIN_PASSWORD": WP_ADMIN_PASSWORD,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        return False
    return True


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------
def process_email(record: dict, agent: WordPressAgent) -> None:
    username = record["username"]
    purchaser_email = record["purchaser_email"]

    logger.info("=== Processing purchase for %s ===", purchaser_email)

    # Step 1: Create WordPress user
    logger.info("Step 1/3 – Creating WP user '%s' …", username)
    user_id = agent.create_user(
        username=username,
        email=purchaser_email,
        password=NEW_USER_PASSWORD,
    )
    if not user_id:
        logger.error("User creation failed for '%s'. Skipping enrollment.", username)
        return

    # Step 2: Enroll in course
    logger.info("Step 2/3 – Enrolling '%s' (ID: %s) in course …", username, user_id)
    enrolled = agent.enroll_student(username=username, user_id=user_id)
    if not enrolled:
        logger.error("Enrollment failed for '%s'.", username)
        # Still attempt to send credentials even if enrollment failed
    else:
        logger.info("Enrollment successful for '%s'.", username)

    # Step 3: Create draft email with credentials
    logger.info("Step 3/3 – Creating draft email for %s …", purchaser_email)
    draft_ok = create_draft(
        imap_host=IMAP_HOST,
        imap_port=IMAP_PORT,
        email_address=EMAIL_ADDRESS,
        email_password=EMAIL_PASSWORD,
        to_address=purchaser_email,
        username=username,
        password=NEW_USER_PASSWORD,
    )
    if draft_ok:
        logger.info("Draft email saved for %s.", purchaser_email)
    else:
        logger.warning("Draft email could not be saved for %s.", purchaser_email)

    logger.info("=== Done with %s ===", purchaser_email)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run() -> None:
    if not _check_config():
        raise SystemExit(1)

    agent = WordPressAgent(
        admin_url=WP_ADMIN_URL,
        admin_user=WP_ADMIN_USER,
        admin_password=WP_ADMIN_PASSWORD,
        headless=True,
    )

    logger.info("Agent started.")

    try:
        logger.info("Checking inbox …")
        records = fetch_new_purchase_emails(
            imap_host=IMAP_HOST,
            imap_port=IMAP_PORT,
            email_address=EMAIL_ADDRESS,
            email_password=EMAIL_PASSWORD,
        )

        if not records:
            logger.info("No new purchase emails found.")
        else:
            for record in records:
                try:
                    process_email(record, agent)
                except Exception as exc:
                    logger.exception(
                        "Unhandled error processing email UID %s: %s",
                        record.get("uid"),
                        exc,
                    )
    except Exception as exc:
        logger.exception("Unexpected error: %s", exc)

    logger.info("Done.")


if __name__ == "__main__":
    run()
