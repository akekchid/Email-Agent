"""Offline parser tests. Run: python -m unittest discover -s tests/unit -v."""

import base64
import binascii
import unittest
from datetime import datetime, timezone

from gmail.models import GmailMessage
from gmail.parser import clean_email_body, extract_body, parse_message, parse_timestamp


def text_part(text, mime_type="text/plain", charset="utf-8", **fields):
    """Build a Gmail MIME part with real base64url-encoded content."""
    return {
        "mimeType": mime_type,
        "headers": [
            {"name": "Content-Type", "value": f"{mime_type}; charset={charset}"}
        ],
        "body": {"data": base64.urlsafe_b64encode(text.encode(charset)).decode("ascii")},
        **fields,
    }


def raw_message():
    payload = text_part("Hello from Gmail.")
    payload["headers"].extend([
        {"name": "From", "value": '"Ada Lovelace" <ada@example.com>'},
        {"name": "To", "value": "Grace <grace@example.com>"},
        {"name": "Subject", "value": "Test message"},
    ])
    return {
        "id": "message-123",
        "threadId": "thread-456",
        "internalDate": "1704067200123",
        "payload": payload,
    }


class HeaderExtractionTests(unittest.TestCase):
    def test_parses_headers_and_message_identifiers(self):
        self.assertEqual(parse_message(raw_message()), GmailMessage(
            id="message-123",
            thread_id="thread-456",
            sender_name="Ada Lovelace",
            sender_email="ada@example.com",
            recipient="Grace <grace@example.com>",
            subject="Test message",
            body="Hello from Gmail.",
            timestamp=datetime(2024, 1, 1, 0, 0, 0, 123000, tzinfo=timezone.utc),
        ))

    def test_header_names_are_case_insensitive(self):
        raw = raw_message()
        raw["payload"]["headers"] = [
            {"name": "fRoM", "value": "Ada <ada@example.com>"},
            {"name": "TO", "value": "grace@example.com"},
            {"name": "sUbJeCt", "value": "Mixed case"},
        ]
        parsed = parse_message(raw)
        self.assertEqual(parsed.sender_name, "Ada")
        self.assertEqual(parsed.sender_email, "ada@example.com")
        self.assertEqual(parsed.recipient, "grace@example.com")
        self.assertEqual(parsed.subject, "Mixed case")

    def test_sender_without_display_name(self):
        raw = raw_message()
        raw["payload"]["headers"] = [{"name": "From", "value": "ada@example.com"}]
        parsed = parse_message(raw)
        self.assertEqual(parsed.sender_name, "")
        self.assertEqual(parsed.sender_email, "ada@example.com")


class BodyExtractionTests(unittest.TestCase):
    def test_decodes_unicode_and_urlsafe_base64(self):
        self.assertEqual(extract_body(text_part("Hello 🌍 — café")), "Hello 🌍 — café")

    def test_accepts_missing_base64_padding(self):
        part = text_part("Hello")
        part["body"]["data"] = part["body"]["data"].rstrip("=")
        self.assertEqual(extract_body(part), "Hello")

    def test_honors_declared_charset(self):
        self.assertEqual(extract_body(text_part("café", charset="iso-8859-1")), "café")

    def test_uses_content_type_header_when_mime_type_is_missing(self):
        part = text_part("<p>Hello</p>", "text/html")
        del part["mimeType"]
        self.assertEqual(extract_body(part), "<p>Hello</p>")

    def test_ignores_non_text_content(self):
        self.assertEqual(extract_body(text_part("binary", "image/png")), "")

    def test_skips_named_attachments(self):
        self.assertEqual(extract_body(text_part("private notes", filename="notes.txt")), "")

    def test_skips_attachment_disposition_without_filename(self):
        part = text_part("private notes")
        part["headers"].append({"name": "Content-Disposition", "value": "attachment"})
        self.assertEqual(extract_body(part), "")


class TimestampTests(unittest.TestCase):
    def test_converts_milliseconds_to_aware_utc_datetime(self):
        self.assertEqual(
            parse_timestamp("1704067200123"),
            datetime(2024, 1, 1, 0, 0, 0, 123000, tzinfo=timezone.utc),
        )

    def test_unix_epoch(self):
        self.assertEqual(parse_timestamp("0"), datetime(1970, 1, 1, tzinfo=timezone.utc))

    def test_timestamp_before_epoch(self):
        self.assertEqual(
            parse_timestamp("-1000"),
            datetime(1969, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
        )

    def test_rejects_non_integer_timestamps(self):
        for value in ("", "not-a-date", "1704067200.123"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_timestamp(value)


class BodyCleanupTests(unittest.TestCase):
    def test_decodes_named_and_numeric_html_entities(self):
        self.assertEqual(clean_email_body("Tom &amp; Jerry &#60;3 &#x1F600;"), "Tom & Jerry <3 😀")

    def test_normalizes_nonbreaking_spaces_tabs_and_line_endings(self):
        self.assertEqual(clean_email_body("  Hello&nbsp;world\t\t!\r\nNext\r\n  "), "Hello world !\nNext")

    def test_limits_blank_lines_without_removing_paragraph_breaks(self):
        self.assertEqual(clean_email_body("one\n\n\n\ntwo\n\nthree"), "one\n\ntwo\n\nthree")

    def test_empty_or_whitespace_only_body(self):
        for body in ("", " \t\r\n ", "&nbsp;"):
            with self.subTest(body=body):
                self.assertEqual(clean_email_body(body), "")

    def test_html_tags_are_currently_preserved(self):
        # Cleanup decodes entities; HTML-to-text conversion is not implemented.
        self.assertEqual(clean_email_body("<p>Tom &amp; Jerry</p>"), "<p>Tom & Jerry</p>")

    def test_parse_message_applies_cleanup_to_extracted_body(self):
        raw = raw_message()
        raw["payload"] = text_part("  Tom &amp; Jerry&nbsp;\r\n\r\n\r\nHello\t\tworld  ")
        self.assertEqual(parse_message(raw).body, "Tom & Jerry \n\nHello world")


class MultipartTests(unittest.TestCase):
    def test_plain_text_wins_regardless_of_alternative_order(self):
        plain = text_part("Hello")
        html = text_part("<p>Hello</p>", "text/html")
        for parts in ([plain, html], [html, plain]):
            with self.subTest(parts=parts):
                self.assertEqual(extract_body({"mimeType": "multipart/alternative", "parts": parts}), "Hello")

    def test_nested_multipart_with_attachments(self):
        raw = raw_message()
        raw["payload"] = {
            "mimeType": "multipart/mixed",
            "parts": [
                text_part("attachment", filename="notes.txt"),
                {"mimeType": "multipart/related", "parts": [
                    text_part("image", "image/png"),
                    {"mimeType": "multipart/alternative", "parts": [
                        text_part("<p>Hello</p>", "text/html"),
                        text_part("Hello"),
                    ]},
                ]},
            ],
        }
        self.assertEqual(parse_message(raw).body, "Hello")

    def test_html_fallback_when_plain_part_is_empty(self):
        payload = {"parts": [text_part(""), text_part("<p>Hello</p>", "text/html")]}
        self.assertEqual(extract_body(payload), "<p>Hello</p>")

    def test_joins_inline_text_parts_in_document_order(self):
        payload = {"parts": [text_part("First"), {"parts": [text_part("Second")]}, text_part("Third")]}
        self.assertEqual(extract_body(payload), "First\nSecond\nThird")

    def test_skips_entire_attachment_subtree(self):
        payload = {"parts": [{"filename": "forwarded.eml", "parts": [text_part("Attached message")]}, text_part("Main body")]}
        self.assertEqual(extract_body(payload), "Main body")


class MissingFieldsTests(unittest.TestCase):
    def test_missing_optional_headers_and_body_default_to_empty_strings(self):
        raw = raw_message()
        raw["payload"] = {}
        parsed = parse_message(raw)
        for field in ("sender_name", "sender_email", "recipient", "subject", "body"):
            with self.subTest(field=field):
                self.assertEqual(getattr(parsed, field), "")

    def test_missing_required_message_fields_raise_key_error(self):
        for field in ("id", "threadId", "payload", "internalDate"):
            raw = raw_message()
            del raw[field]
            with self.subTest(field=field), self.assertRaises(KeyError) as error:
                parse_message(raw)
            self.assertEqual(error.exception.args, (field,))

    def test_empty_and_unavailable_body_data(self):
        for payload in ({}, {"body": {}}, {"body": {"data": ""}}, {"body": {"attachmentId": "external"}}, {"parts": []}):
            with self.subTest(payload=payload):
                self.assertEqual(extract_body(payload), "")


class MalformedPayloadTests(unittest.TestCase):
    def test_invalid_base64_length_raises_decode_error(self):
        with self.assertRaises(binascii.Error):
            extract_body({"mimeType": "text/plain", "body": {"data": "a"}})

    def test_invalid_utf8_bytes_are_replaced(self):
        data = base64.urlsafe_b64encode(b"Hello \xff").decode("ascii")
        self.assertEqual(extract_body({"body": {"data": data}}), "Hello \ufffd")

    def test_unknown_charset_falls_back_to_utf8(self):
        part = text_part("café")
        part["headers"] = [{"name": "Content-Type", "value": "text/plain; charset=unknown-charset"}]
        self.assertEqual(extract_body(part), "café")

    def test_incomplete_header_records_raise_key_error(self):
        for header in ({"name": "From"}, {"value": "ada@example.com"}):
            raw = raw_message()
            raw["payload"]["headers"] = [header]
            with self.subTest(header=header), self.assertRaises(KeyError):
                parse_message(raw)

    def test_null_timestamp_is_rejected(self):
        raw = raw_message()
        raw["internalDate"] = None
        with self.assertRaises(TypeError):
            parse_message(raw)


if __name__ == "__main__":
    unittest.main()
