"""
gmail_client.py — IMAP client wrapper for fetching emails from Gmail.

Uses Python's built-in imaplib + email modules. Connects via IMAP4_SSL
with App Password authentication. Read-only: fetches and parses emails,
marks only matching ones as Seen after processing.

Non-matching emails are left UNSEEN so other skills can ingest them.
"""

import email
import email.header
import email.utils
import imaplib
import logging
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser
from io import StringIO

logger = logging.getLogger(__name__)


class _HTMLTextExtractor(HTMLParser):
    """Minimal HTML-to-text converter for email bodies."""

    def __init__(self):
        super().__init__()
        self._output = StringIO()
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
        if tag in ("p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4"):
            self._output.write("\n")

    def handle_data(self, data):
        if not self._skip:
            self._output.write(data)

    def get_text(self):
        return self._output.getvalue()


def html_to_text(html_content: str) -> str:
    """Strip HTML tags and return plain text."""
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(html_content)
        text = extractor.get_text()
    except Exception:
        # Fallback: brute-force tag removal
        text = re.sub(r"<[^>]+>", " ", html_content)
    return re.sub(r"\s+", " ", text).strip()


class GmailClient:
    """
    IMAP client for reading emails from a Gmail inbox.

    Usage:
        client = GmailClient(host, email, app_password)
        client.connect()
        emails = client.fetch_unseen()
        client.mark_seen([e["uid"] for e in emails])
        client.disconnect()
    """

    def __init__(self, host: str, email_address: str, app_password: str):
        self.host = host
        self.email_address = email_address
        self.app_password = app_password
        self._conn = None

    def connect(self):
        """Establish IMAP4_SSL connection and authenticate."""
        logger.info("Connecting to %s as %s", self.host, self.email_address)
        try:
            self._conn = imaplib.IMAP4_SSL(self.host)
            self._conn.login(self.email_address, self.app_password)
            self._conn.select("INBOX")
            logger.info("Connected and INBOX selected")
        except imaplib.IMAP4.error as e:
            logger.error("IMAP connection/auth failed: %s", e)
            raise ConnectionError(f"Failed to connect to Gmail: {e}") from e

    def disconnect(self):
        """Close connection cleanly."""
        if self._conn:
            try:
                self._conn.close()
                self._conn.logout()
                logger.info("IMAP connection closed")
            except Exception as e:
                logger.warning("Error during disconnect: %s", e)
            finally:
                self._conn = None

    def fetch_unseen(self) -> list:
        """
        Fetch all UNSEEN emails from INBOX.

        Returns a list of parsed email dicts.
        Does NOT mark emails as seen — caller decides which to mark.
        """
        logger.info("Fetching UNSEEN emails")
        return self._search_and_fetch("UNSEEN")

    def fetch_by_date_range(self, from_date: str, to_date: str) -> list:
        """
        Fetch emails within a date range (for reprocessing).

        Args:
            from_date: YYYY-MM-DD
            to_date: YYYY-MM-DD

        Returns a list of parsed email dicts regardless of Seen status.
        """
        # IMAP date format: DD-Mon-YYYY
        from_dt = datetime.strptime(from_date, "%Y-%m-%d")
        to_dt = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)

        imap_from = from_dt.strftime("%d-%b-%Y")
        imap_to = to_dt.strftime("%d-%b-%Y")

        criteria = f'(SINCE "{imap_from}" BEFORE "{imap_to}")'
        logger.info("Fetching emails in range %s to %s", from_date, to_date)
        return self._search_and_fetch(criteria)

    def mark_seen(self, uid_list: list):
        """
        Mark specific emails as \\Seen by UID.

        Only call this for emails that matched the allow-list and were processed.
        Non-matching emails are left UNSEEN for other skills.
        """
        if not uid_list:
            return
        logger.info("Marking %d emails as Seen", len(uid_list))
        for uid in uid_list:
            try:
                self._conn.uid("STORE", str(uid), "+FLAGS", "(\\Seen)")
            except imaplib.IMAP4.error as e:
                logger.error("Failed to mark UID %s as seen: %s", uid, e)

    def _search_and_fetch(self, criteria: str) -> list:
        """Search INBOX and fetch+parse matching emails."""
        self._ensure_connected()
        try:
            status, data = self._conn.uid("SEARCH", None, criteria)
            if status != "OK":
                logger.error("IMAP SEARCH failed: %s", status)
                return []

            uid_list = data[0].split()
            logger.info("Found %d emails matching criteria: %s", len(uid_list), criteria)

            results = []
            for uid in uid_list:
                parsed = self._fetch_one(uid)
                if parsed:
                    results.append(parsed)
            return results

        except imaplib.IMAP4.error as e:
            logger.error("IMAP search/fetch error: %s", e)
            return []

    def _fetch_one(self, uid: bytes) -> dict:
        """Fetch and parse a single email by UID."""
        uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
        try:
            status, data = self._conn.uid("FETCH", uid_str, "(RFC822)")
            if status != "OK" or not data or not data[0]:
                logger.warning("Failed to fetch UID %s", uid_str)
                return None

            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)

            # Decode headers
            from_addr = self._decode_header(msg.get("From", ""))
            subject = self._decode_header(msg.get("Subject", ""))
            date_str = msg.get("Date", "")
            message_id = msg.get("Message-ID", "")

            # Parse email date
            received_at = self._parse_email_date(date_str)

            # Extract sender email address
            sender_email = self._extract_email_address(from_addr)

            # Extract body
            body_plain, body_html = self._extract_body(msg)
            body = body_plain if body_plain else html_to_text(body_html) if body_html else ""

            parsed = {
                "email_uid": uid_str,
                "message_id": message_id.strip(),
                "from": from_addr,
                "sender_email": sender_email.lower(),
                "subject": subject,
                "date": date_str,
                "received_at": received_at,
                "body": body,
                "body_html": body_html or "",
            }

            logger.debug(
                "Fetched email UID=%s from=%s subject=%.60s",
                uid_str, sender_email, subject,
            )
            return parsed

        except Exception as e:
            logger.error("Error parsing email UID %s: %s", uid_str, e, exc_info=True)
            return None

    def _extract_body(self, msg) -> tuple:
        """
        Extract plain text and HTML body from an email message.

        Returns (plain_text, html_text) — either or both may be empty string.
        """
        plain = ""
        html = ""

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))
                if "attachment" in content_disposition:
                    continue

                try:
                    payload = part.get_payload(decode=True)
                    if payload is None:
                        continue
                    charset = part.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace")

                    if content_type == "text/plain" and not plain:
                        plain = text
                    elif content_type == "text/html" and not html:
                        html = text
                except Exception as e:
                    logger.debug("Error decoding part: %s", e)
                    continue
        else:
            content_type = msg.get_content_type()
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace")
                    if content_type == "text/plain":
                        plain = text
                    elif content_type == "text/html":
                        html = text
            except Exception as e:
                logger.debug("Error decoding body: %s", e)

        return plain, html

    @staticmethod
    def _decode_header(header_value: str) -> str:
        """Decode RFC 2047 encoded email header."""
        if not header_value:
            return ""
        decoded_parts = email.header.decode_header(header_value)
        result = []
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                result.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                result.append(part)
        return " ".join(result)

    @staticmethod
    def _extract_email_address(from_header: str) -> str:
        """Extract bare email address from a From header like 'Name <email>'."""
        match = re.search(r"<([^>]+)>", from_header)
        if match:
            return match.group(1).strip()
        # Might be bare email
        match = re.search(r"[\w.+-]+@[\w.-]+", from_header)
        return match.group(0) if match else from_header.strip()

    @staticmethod
    def _parse_email_date(date_str: str) -> str:
        """Parse email Date header to ISO format."""
        if not date_str:
            return datetime.now().isoformat()
        try:
            parsed = email.utils.parsedate_to_datetime(date_str)
            return parsed.isoformat()
        except Exception:
            return datetime.now().isoformat()

    def _ensure_connected(self):
        """Raise if not connected."""
        if not self._conn:
            raise ConnectionError("Not connected to Gmail. Call connect() first.")
