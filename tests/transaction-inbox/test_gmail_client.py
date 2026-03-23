"""
test_gmail_client.py — Tests for the Gmail IMAP client with mocked connections.

All IMAP interactions are mocked — no real Gmail connection needed.
"""

import email.mime.text
import email.mime.multipart
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

from lib.gmail_client import GmailClient, html_to_text


# ---------------------------------------------------------------------------
# HTML to text
# ---------------------------------------------------------------------------

class TestHtmlToText:
    def test_simple_html(self):
        html = "<p>Hello <b>World</b></p>"
        text = html_to_text(html)
        assert "Hello" in text
        assert "World" in text
        assert "<p>" not in text

    def test_strips_script_tags(self):
        html = "<script>alert('x')</script><p>Content</p>"
        text = html_to_text(html)
        assert "alert" not in text
        assert "Content" in text

    def test_newlines_on_block_elements(self):
        html = "<div>Line 1</div><div>Line 2</div>"
        text = html_to_text(html)
        assert "Line 1" in text
        assert "Line 2" in text

    def test_empty_html(self):
        assert html_to_text("") == ""

    def test_plain_text_passthrough(self):
        text = html_to_text("No HTML here")
        assert text == "No HTML here"


# ---------------------------------------------------------------------------
# Email address extraction
# ---------------------------------------------------------------------------

class TestEmailAddressExtraction:
    def test_angle_bracket_format(self):
        # noinspection PyProtectedMember
        result = GmailClient._extract_email_address("HDFC Bank <alerts@hdfcbank.net>")
        assert result == "alerts@hdfcbank.net"

    def test_bare_email(self):
        result = GmailClient._extract_email_address("alerts@hdfcbank.net")
        assert result == "alerts@hdfcbank.net"

    def test_complex_name(self):
        result = GmailClient._extract_email_address(
            '"HDFC Bank Alerts" <alerts@hdfcbank.net>'
        )
        assert result == "alerts@hdfcbank.net"


# ---------------------------------------------------------------------------
# Email date parsing
# ---------------------------------------------------------------------------

class TestEmailDateParsing:
    def test_standard_rfc_date(self):
        result = GmailClient._parse_email_date(
            "Mon, 23 Mar 2026 22:00:00 +0530"
        )
        assert "2026-03-23" in result

    def test_empty_date(self):
        result = GmailClient._parse_email_date("")
        # Should return current time as ISO string
        assert datetime.now().strftime("%Y-%m-%d") in result

    def test_malformed_date(self):
        result = GmailClient._parse_email_date("not a date")
        # Should return current time as fallback
        assert result is not None


# ---------------------------------------------------------------------------
# Mocked IMAP tests
# ---------------------------------------------------------------------------

def _build_raw_email(from_addr, subject, body_text, date=None):
    """Build a raw email bytes object for mock IMAP."""
    msg = email.mime.text.MIMEText(body_text)
    msg["From"] = from_addr
    msg["Subject"] = subject
    msg["Date"] = date or "Mon, 23 Mar 2026 22:00:00 +0530"
    msg["Message-ID"] = f"<test-{hash(subject)}@gmail.com>"
    return msg.as_bytes()


def _build_multipart_email(from_addr, subject, plain_body, html_body):
    """Build a multipart email with both plain and HTML parts."""
    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg["From"] = from_addr
    msg["Subject"] = subject
    msg["Date"] = "Mon, 23 Mar 2026 22:00:00 +0530"
    msg["Message-ID"] = f"<multi-{hash(subject)}@gmail.com>"

    plain_part = email.mime.text.MIMEText(plain_body, "plain")
    html_part = email.mime.text.MIMEText(html_body, "html")
    msg.attach(plain_part)
    msg.attach(html_part)
    return msg.as_bytes()


class TestGmailClientMocked:
    """Test IMAP operations with a mocked connection."""

    def _make_client(self):
        return GmailClient("imap.gmail.com", "test@gmail.com", "fakepassword")

    @patch("lib.gmail_client.imaplib.IMAP4_SSL")
    def test_connect(self, mock_imap_class):
        mock_conn = MagicMock()
        mock_imap_class.return_value = mock_conn

        client = self._make_client()
        client.connect()

        mock_imap_class.assert_called_once_with("imap.gmail.com")
        mock_conn.login.assert_called_once_with("test@gmail.com", "fakepassword")
        mock_conn.select.assert_called_once_with("INBOX")

    @patch("lib.gmail_client.imaplib.IMAP4_SSL")
    def test_fetch_unseen(self, mock_imap_class):
        mock_conn = MagicMock()
        mock_imap_class.return_value = mock_conn

        raw = _build_raw_email(
            "alerts@hdfcbank.net",
            "Debit Alert",
            "Rs.500 has been debited from your account to Swiggy.",
        )
        mock_conn.uid.side_effect = [
            ("OK", [b"1 2"]),              # SEARCH
            ("OK", [(b"1", raw)]),          # FETCH uid 1
            ("OK", [(b"2", raw)]),          # FETCH uid 2
        ]

        client = self._make_client()
        client._conn = mock_conn

        emails = client.fetch_unseen()
        assert len(emails) == 2
        assert emails[0]["sender_email"] == "alerts@hdfcbank.net"
        assert "Rs.500" in emails[0]["body"]

    @patch("lib.gmail_client.imaplib.IMAP4_SSL")
    def test_fetch_empty(self, mock_imap_class):
        mock_conn = MagicMock()
        mock_imap_class.return_value = mock_conn
        mock_conn.uid.return_value = ("OK", [b""])

        client = self._make_client()
        client._conn = mock_conn

        emails = client.fetch_unseen()
        assert len(emails) == 0

    @patch("lib.gmail_client.imaplib.IMAP4_SSL")
    def test_mark_seen(self, mock_imap_class):
        mock_conn = MagicMock()
        mock_imap_class.return_value = mock_conn

        client = self._make_client()
        client._conn = mock_conn

        client.mark_seen(["1", "2"])
        assert mock_conn.uid.call_count == 2

    @patch("lib.gmail_client.imaplib.IMAP4_SSL")
    def test_multipart_email_plain_preferred(self, mock_imap_class):
        mock_conn = MagicMock()
        mock_imap_class.return_value = mock_conn

        raw = _build_multipart_email(
            "alerts@hdfcbank.net",
            "Alert",
            "Plain text body with Rs.1000 debited",
            "<html><body>HTML body with Rs.1000 debited</body></html>",
        )
        mock_conn.uid.side_effect = [
            ("OK", [b"1"]),
            ("OK", [(b"1", raw)]),
        ]

        client = self._make_client()
        client._conn = mock_conn

        emails = client.fetch_unseen()
        assert len(emails) == 1
        assert "Plain text body" in emails[0]["body"]

    @patch("lib.gmail_client.imaplib.IMAP4_SSL")
    def test_disconnect(self, mock_imap_class):
        mock_conn = MagicMock()
        mock_imap_class.return_value = mock_conn

        client = self._make_client()
        client._conn = mock_conn
        client.disconnect()

        mock_conn.close.assert_called_once()
        mock_conn.logout.assert_called_once()
        assert client._conn is None

    def test_ensure_connected_raises(self):
        client = self._make_client()
        with pytest.raises(ConnectionError, match="Not connected"):
            client._ensure_connected()
