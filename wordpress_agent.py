"""
wordpress_agent.py
Uses Playwright to:
  1. Log in to WordPress admin.
  2. Create a new user (username derived from purchaser email, password = 1234).
  3. Enroll that user in "קורס גינון אקולוגי מורחב".
"""

import logging
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

logger = logging.getLogger(__name__)

COURSE_NAME = "קורס גינון אקולוגי מורחב"
NEW_USER_ROLE = "subscriber"


class WordPressAgent:
    def __init__(self, admin_url: str, admin_user: str, admin_password: str, headless: bool = True):
        self.admin_url = admin_url.rstrip("/")
        self.admin_user = admin_user
        self.admin_password = admin_password
        self.headless = headless

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _login(self, page) -> bool:
        """Navigate to wp-login.php and authenticate."""
        login_url = f"{self.admin_url}/wp-login.php"
        logger.info("Navigating to login page: %s", login_url)
        page.goto(login_url, wait_until="domcontentloaded")

        # Fill fields via JS (bypasses visibility checks)
        page.evaluate(
            """([u, p]) => {
                document.getElementById('user_login').value = u;
                document.getElementById('user_pass').value = p;
            }""",
            [self.admin_user, self.admin_password],
        )
        # Dispatch click event directly — bypasses all visibility checks
        page.dispatch_event("#wp-submit", "click")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)

        if "wp-admin" in page.url or "dashboard" in page.url:
            logger.info("WP login successful.")
            return True

        logger.error("WP login failed. Current URL: %s", page.url)
        return False

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def create_user(self, username: str, email: str, password: str) -> str | None:
        """
        Create a new WordPress subscriber via the REST API using an authenticated
        browser session (X-WP-Nonce) since Basic Auth is not available.
        Returns the user ID (str) on success, None on failure.
        """
        site_root = self.admin_url.replace("/wp-admin", "")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context()
            page = context.new_page()
            try:
                if not self._login(page):
                    return None

                # Get nonce from wpApiSettings injected on any admin page
                nonce = page.evaluate("() => (window.wpApiSettings && window.wpApiSettings.nonce) || null")
                if not nonce:
                    # Navigate to user-new.php to ensure nonce is available
                    page.goto(f"{self.admin_url}/user-new.php", wait_until="domcontentloaded")
                    page.wait_for_timeout(1000)
                    nonce = page.evaluate("() => (window.wpApiSettings && window.wpApiSettings.nonce) || null")

                if not nonce:
                    logger.error("Could not obtain WP REST API nonce.")
                    return None

                logger.info("Obtained WP nonce. Creating user '%s' via REST API.", username)

                result = page.evaluate(
                    """async ([siteRoot, nonce, username, email, password, role]) => {
                        const resp = await fetch(siteRoot + '/wp-json/wp/v2/users', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'X-WP-Nonce': nonce,
                            },
                            body: JSON.stringify({username, email, password, roles: [role]}),
                        });
                        const body = await resp.json().catch(() => ({}));
                        return {status: resp.status, body};
                    }""",
                    [site_root, nonce, username, email, password, NEW_USER_ROLE],
                )

                status = result.get("status")
                body = result.get("body", {})

                if status == 201:
                    uid = str(body.get("id", ""))
                    logger.info("User '%s' created via REST API with ID %s.", username, uid)
                    return uid

                code = body.get("code", "")
                logger.info("Create user response: status=%s code=%s", status, code)

                if code in ("existing_user_login", "existing_user_email") or status == 400:
                    logger.info("User '%s' already exists — fetching existing ID.", username)
                    search_result = page.evaluate(
                        """async ([siteRoot, nonce, username]) => {
                            const resp = await fetch(
                                siteRoot + '/wp-json/wp/v2/users?search=' + encodeURIComponent(username) + '&context=edit',
                                {headers: {'X-WP-Nonce': nonce}}
                            );
                            const body = await resp.json().catch(() => []);
                            return {status: resp.status, body};
                        }""",
                        [site_root, nonce, username],
                    )
                    users = search_result.get("body", [])
                    if isinstance(users, list):
                        for u in users:
                            if u.get("slug") == username or u.get("username") == username:
                                logger.info("Found existing user '%s' with ID %s.", username, u["id"])
                                return str(u["id"])
                        if users:
                            logger.info("Returning first search result ID %s for '%s'.", users[0]["id"], username)
                            return str(users[0]["id"])

                logger.error("User creation failed for '%s': status=%s body=%s", username, status, body)
                return None

            except Exception as exc:
                logger.exception("Unexpected error during create_user: %s", exc)
                return None
            finally:
                browser.close()

    def enroll_student(self, username: str, user_id: str = None) -> bool:
        """
        Enroll the given username in COURSE_NAME via the WP admin enrollment page.
        Returns True on success.
        """
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context()
            page = context.new_page()

            try:
                if not self._login(page):
                    return False

                # --- Resolve user ID if not already known ---
                if not user_id or user_id == "unknown":
                    page.goto(f"{self.admin_url}/users.php?s={username}", wait_until="domcontentloaded")
                    page.wait_for_timeout(1000)
                    user_id = page.evaluate(
                        """() => {
                            const row = document.querySelector('tr[id^="user-"]');
                            return row ? row.id.replace('user-', '') : null;
                        }"""
                    )
                    if not user_id or not str(user_id).isdigit():
                        raise Exception("Could not find numeric user ID for '%s' in users list" % username)
                    logger.info("Resolved user ID %s for '%s' from users list.", user_id, username)

                # --- Navigate to enrollment page ---
                enroll_url = f"{self.admin_url}/admin.php?page=enrollments&sub_page=enroll_student"
                logger.info("Navigating to enrollment page: %s", enroll_url)
                page.goto(enroll_url, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)

                # Inject the user as a Select2 option and select it directly
                page.evaluate(
                    """([uid, uname]) => {
                        var select = document.querySelector("select[name='student_id']");
                        var option = new Option(uname, uid, true, true);
                        select.appendChild(option);
                        if (typeof jQuery !== 'undefined') {
                            jQuery(select).trigger('change');
                        }
                    }""",
                    [str(user_id), username],
                )
                actual_val = page.evaluate("document.querySelector(\"select[name='student_id']\").value")
                logger.info("student_id set to: %s", actual_val)

                # --- Select the course (value 1827 = קורס גינון אקולוגי מורחב) ---
                page.select_option("select[name='course_id']", value="1827")
                course_val = page.evaluate("document.querySelector(\"select[name='course_id']\").value")
                logger.info("course_id set to: %s (course: %s)", course_val, COURSE_NAME)

                # --- Click Enroll via jQuery to trigger TutorLMS AJAX handler ---
                page.evaluate("jQuery(\"button[name='tutor_enroll_student_btn']\").trigger('click')")
                logger.info("Enroll button clicked via jQuery trigger.")

                # Wait for AJAX response (success notice or page update)
                try:
                    page.wait_for_selector(
                        ".notice-success, .updated, .alert-success, .tutor-alert-success",
                        timeout=10000,
                    )
                    logger.info("Enrollment successful for '%s'.", username)
                    return True
                except PWTimeoutError:
                    # Log page title and visible alerts for diagnosis
                    alerts = page.locator(".notice, .alert, .tutor-alert").all_inner_texts()
                    logger.error(
                        "Enrollment failed for '%s'. Page: %s | Alerts: %s",
                        username,
                        page.title(),
                        alerts,
                    )
                    return False

            except Exception as exc:
                logger.exception("Unexpected error during enrollment: %s", exc)
                return False
            finally:
                browser.close()
