"""
email_monitor.py
Connects to IMAP, fetches unread emails from support@grow.security,
and checks for the Hebrew purchase phrase.
"""

import imaplib
import email
import email.header
import email.utils
import json
import os
import re
import logging
from datetime import datetime, timezone, timedelta
from email.policy import default as default_policy

logger = logging.getLogger(__name__)

PROCESSED_UIDS_FILE = os.path.join(os.path.dirname(__file__), "processed_uids.json")


def _load_processed_uids() -> set:
    if os.path.exists(PROCESSED_UIDS_FILE):
        try:
            with open(PROCESSED_UIDS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def _save_processed_uid(uid: str) -> None:
    uids = _load_processed_uids()
    uids.add(uid)
    with open(PROCESSED_UIDS_FILE, "w") as f:
        json.dump(list(uids), f)


SENDER_FILTER = "support@grow.security"
REQUIRED_PHRASE = "רכישת כניסה לקורס הגינון האקולוגי מורחב"


def decode_part(part) -> str:
    """Decode a raw email bytes payload to a unicode string."""
    charset = part.get_content_charset() or "utf-8"
    try:
        return part.get_payload(decode=True).decode(charset, errors="replace")
    except Exception:
        return part.get_payload(decode=True).decode("utf-8", errors="replace")


def get_email_body(msg) -> str:
    """Extract plain-text body from an email.Message object."""
    body_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and part.get("Content-Disposition") is None:
                body_parts.append(decode_part(part))
    else:
        body_parts.append(decode_part(msg))
    return "\n".join(body_parts)


def extract_purchaser_email(body: str) -> str | None:
    """
    Extract the customer's email from the grow.security email format.
    The body contains a line like:  מייל: customer@example.com <other@example.com>
    We specifically extract the email right after the 'מייל:' label.
    Falls back to the first non-sender email if the label is not found.
    """
    email_pattern = r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"

    # Primary: find email after the Hebrew "מייל:" label
    label_match = re.search(r"מייל:\s*(" + email_pattern + r")", body)
    if label_match:
        return label_match.group(1).lower()

    # Fallback: first email that is not the sender
    candidates = re.findall(email_pattern, body)
    for addr in candidates:
        if addr.lower() != SENDER_FILTER:
            return addr.lower()
    return None


def fetch_new_purchase_emails(
    imap_host: str,
    imap_port: int,
    email_address: str,
    email_password: str,
) -> list[dict]:
    """
    Connect to IMAP, search for unread emails from SENDER_FILTER that
    contain REQUIRED_PHRASE. Returns a list of dicts:
        {
            "uid": bytes,
            "from": str,
            "subject": str,
            "body": str,
            "purchaser_email": str,
            "username": str,
        }
    Marks matched emails as seen.
    """
    results = []

    try:
        logger.info("Connecting to IMAP %s:%s …", imap_host, imap_port)
        if imap_port == 993:
            mail = imaplib.IMAP4_SSL(imap_host, imap_port)
        else:
            mail = imaplib.IMAP4(imap_host, imap_port)
        mail.login(email_address, email_password)
        mail.select("INBOX")
    except imaplib.IMAP4.error as exc:
        logger.error("IMAP connection / login failed: %s", exc)
        return results

    try:
        # Search for emails from the known sender (seen or unseen)
        status, data = mail.uid("search", None, f'(UNSEEN FROM "{SENDER_FILTER}")')
        if status != "OK" or not data[0]:
            logger.info("No emails from %s.", SENDER_FILTER)
            return results

        uids = data[0].split()
        logger.info("Found %d email(s) from sender.", len(uids))

        processed_uids = _load_processed_uids()

        for uid in uids:
            uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
            if uid_str in processed_uids:
                logger.info("UID %s: already processed, skipping.", uid_str)
                continue

            status, msg_data = mail.uid("fetch", uid, "(RFC822)")
            if status != "OK":
                continue

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw, policy=default_policy)

            # Skip emails older than 15 minutes
            date_str = msg.get("Date", "")
            try:
                msg_time = email.utils.parsedate_to_datetime(date_str)
                if msg_time.tzinfo is None:
                    msg_time = msg_time.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - msg_time
                if age > timedelta(minutes=15):
                    logger.info("UID %s: email is %d min old, skipping.", uid_str, int(age.total_seconds() / 60))
                    continue
            except Exception:
                logger.warning("UID %s: could not parse date '%s', processing anyway.", uid_str, date_str)

            body = get_email_body(msg)

            if REQUIRED_PHRASE not in body:
                logger.info("UID %s: required phrase not found — marking as unread.", uid_str)
                mail.uid("store", uid, "-FLAGS", "(\\Seen)")
                continue

            purchaser_email = extract_purchaser_email(body)
            if not purchaser_email:
                logger.warning("UID %s: phrase found but no purchaser email extracted.", uid)
                continue

            username = purchaser_email.split("@")[0]
            logger.info("UID %s: purchase detected for %s (username: %s)", uid, purchaser_email, username)

            # Mark as seen and save UID locally so we never process it again
            store_res = mail.uid("store", uid, "+FLAGS", "(\\Seen)")
            logger.info("UID %s: marked as seen — %s", uid_str, store_res[0])
            _save_processed_uid(uid_str)

            results.append(
                {
                    "uid": uid,
                    "from": msg.get("From", ""),
                    "subject": msg.get("Subject", ""),
                    "body": body,
                    "purchaser_email": purchaser_email,
                    "username": username,
                }
            )
    except Exception as exc:
        logger.exception("Error while processing emails: %s", exc)
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return results


def create_draft(
    imap_host: str,
    imap_port: int,
    email_address: str,
    email_password: str,
    to_address: str,
    username: str,
    password: str,
) -> bool:
    """
    Append a draft message to the IMAP Drafts folder.
    Returns True on success.
    """
    import email.mime.text
    import email.utils
    import time

    subject = "פרטי הכניסה שלך לקורס גינון אקולוגי מורחב"
    course1_url = "https://meshek.chitim.co.il/courses/%D7%A7%D7%95%D7%A8%D7%A1-%D7%92%D7%99%D7%A0%D7%95%D7%9F-%D7%90%D7%A7%D7%95%D7%9C%D7%95%D7%92%D7%99/"
    course2_url = "https://meshek.chitim.co.il/courses/%D7%A7%D7%95%D7%A8%D7%A1-%D7%90%D7%93%D7%A0%D7%99%D7%95%D7%AA-%D7%95%D7%9E%D7%A8%D7%A4%D7%A1%D7%95%D7%AA/"
    body_text = (
        f"היי,<br><br>"
        f"תודה לך על רכישת קורס דיגיטלי של משק חיטים.<br><br><br>"
        f"לכניסה לקורס הגינון האקולוגי המורחב:<br>"
        f'<a href="{course1_url}">{course1_url}</a><br><br>'
        f"לכניסה לקורס הגינון ירקות באדניות ומרפסות:<br>"
        f'<a href="{course2_url}">{course2_url}</a><br><br>'
        f"*במקרה והקישורים אינם לחיצים ניתן להעתיק ולהדביק לדפדפן.<br><br><br>"
        f"לאחר הכניסה לקישור להתחבר בעזרת פרטי ההתחברות,<br>"
        f"במקרה ויצרת משתמש בעבר יש להשתמש במייל והסיסמא שיצרת.<br>"
        f"במקרה ולא אלו הם פרטי ההתחברות שלך:<br><br>"
        f"שם משתמש: {username}<br>"
        f"סיסמה: {password}<br><br><br>"
        f"משק חיטים"
    )

    msg = email.mime.text.MIMEText(body_text, "html", "utf-8")
    msg["From"] = email_address
    msg["To"] = to_address
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)

    raw_message = msg.as_bytes()

    try:
        if imap_port == 993:
            mail = imaplib.IMAP4_SSL(imap_host, imap_port)
        else:
            mail = imaplib.IMAP4(imap_host, imap_port)
        mail.login(email_address, email_password)

        # Find the real Drafts folder by its \Drafts flag
        drafts_folder = None
        _, folder_list = mail.list()
        for item in folder_list or []:
            decoded = item.decode() if isinstance(item, bytes) else item
            if "\\Drafts" in decoded or "\\drafts" in decoded.lower():
                # Extract folder name: last quoted or unquoted token after " / "
                parts = decoded.split('"/"')
                folder_name = parts[-1].strip().strip('"')
                if folder_name:
                    drafts_folder = folder_name
                    break

        # Fallback if flag not found
        if not drafts_folder:
            drafts_folder = "INBOX.Drafts"

        # Check if a draft for this recipient already exists — skip if so
        sel_status, _ = mail.select(drafts_folder)
        if sel_status == "OK":
            _, search_data = mail.uid("search", None, f'(TO "{to_address}")')
            if search_data and search_data[0]:
                existing = search_data[0].split()
                if existing:
                    logger.info("Draft for %s already exists — skipping duplicate.", to_address)
                    mail.logout()
                    return True
            # Close selected mailbox before appending
            try:
                mail.close()
            except Exception:
                pass

        for folder in [drafts_folder, "Drafts", "INBOX.Drafts"]:
            result = mail.append(
                folder,
                "\\Draft",
                imaplib.Time2Internaldate(time.time()),
                raw_message,
            )
            logger.info("APPEND to '%s': %s", folder, result)
            if result[0] == "OK":
                logger.info("Draft saved to folder '%s' for %s", folder, to_address)
                mail.logout()
                return True

        logger.warning("Could not save draft to any folder.")
        mail.logout()
        return False
    except Exception as exc:
        logger.exception("Failed to create draft: %s", exc)
        return False
