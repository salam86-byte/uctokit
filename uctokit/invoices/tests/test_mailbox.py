"""Testy IMAP fetcheru – přes fake spojení, žádná síť."""

import unittest
from email.message import EmailMessage

from uctokit.invoices.mailbox import (
    MailboxConfig, fetch_invoice_attachments, fetch_invoice_messages,
)


def _make_email(sender, subject, filename, content, subtype="pdf"):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "faktury@klub.cz"
    msg["Subject"] = subject
    msg.set_content("Faktura v priloze")
    maintype = "application" if subtype != "png" else "image"
    msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes()


class FakeIMAP:
    def __init__(self, messages):
        self.messages = messages  # [(uid_bytes, raw_bytes)]
        self.seen = []

    def search(self, charset, criterion):
        return "OK", [b" ".join(uid for uid, _ in self.messages)]

    def fetch(self, uid, spec):
        for u, raw in self.messages:
            if u == uid:
                return "OK", [(b"1 (RFC822)", raw)]
        return "NO", [None]

    def store(self, uid, flags, value):
        self.seen.append(uid)
        return "OK", [b""]

    def close(self):
        pass

    def logout(self):
        pass


def _config(**kw):
    base = dict(host="mail.example.cz", username="faktury@klub.cz", password="x")
    base.update(kw)
    return MailboxConfig(**base)


def _run(messages, config):
    fake = FakeIMAP(messages)
    result = fetch_invoice_attachments(config, open_connection=lambda c: fake)
    return result, fake


class MailboxTests(unittest.TestCase):
    def test_fetches_pdf_attachment(self):
        raw = _make_email("Dodavatel <d@x.cz>", "Faktura 1", "faktura.pdf", b"%PDF-data")
        result, _ = _run([(b"1", raw)], _config())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].filename, "faktura.pdf")
        self.assertEqual(result[0].content, b"%PDF-data")
        self.assertEqual(result[0].sender, "d@x.cz")

    def test_allowlist_skips_other_senders(self):
        raw = _make_email("Cizi <spam@evil.cz>", "Faktura", "faktura.pdf", b"%PDF")
        result, fake = _run([(b"1", raw)], _config(allowed_senders=("ok@x.cz",)))
        self.assertEqual(result, [])
        self.assertIn(b"1", fake.seen)  # označeno přečtené, ať se nezpracovává znovu

    def test_allowlist_accepts_listed_sender(self):
        raw = _make_email("OK <ok@x.cz>", "Faktura", "faktura.isdoc", b"<Invoice/>")
        result, _ = _run([(b"1", raw)], _config(allowed_senders=("ok@x.cz",)))
        self.assertEqual(len(result), 1)

    def test_image_attachment_is_accepted(self):
        raw = _make_email("d@x.cz", "Foto", "faktura.png", b"PNGDATA", subtype="png")
        result, _ = _run([(b"1", raw)], _config())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].filename, "faktura.png")

    def test_unsupported_extension_filtered(self):
        raw = _make_email("d@x.cz", "Text", "poznamka.txt", b"TEXT", subtype="plain")
        result, _ = _run([(b"1", raw)], _config())
        self.assertEqual(result, [])

    def test_size_cap(self):
        raw = _make_email("d@x.cz", "Velka", "faktura.pdf", b"x" * 5000)
        result, _ = _run([(b"1", raw)], _config(max_bytes=1000))
        self.assertEqual(result, [])

    def test_marks_seen(self):
        raw = _make_email("d@x.cz", "Faktura", "faktura.pdf", b"%PDF")
        _, fake = _run([(b"1", raw)], _config(mark_seen=True))
        self.assertIn(b"1", fake.seen)


if __name__ == "__main__":
    unittest.main()


def _make_raw_email_rfc2047(filename_encoded, subtype="pdf", body=b"%PDF-data"):
    """Zpráva s jménem přílohy kódovaným dle RFC 2047 (=?UTF-8?Q?...?=).

    Skládá se ručně: `EmailMessage.add_attachment` kóduje jména dle RFC 2231
    (filename*=utf-8\'\'...), které `get_filename()` dekóduje samo — a reálný
    problém by tak nešlo reprodukovat. Poštovní klienti běžně posílají obojí.
    """
    import base64
    payload = base64.b64encode(body).decode()
    return (
        "From: Dodavatel <d@x.cz>\r\n"
        "To: faktury@klub.cz\r\n"
        "Subject: Faktura\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/mixed; boundary="BOUND"\r\n'
        "\r\n"
        "--BOUND\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "Faktura v priloze\r\n"
        "--BOUND\r\n"
        f"Content-Type: application/{subtype}\r\n"
        f'Content-Disposition: attachment; filename="{filename_encoded}"\r\n'
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n"
        f"{payload}\r\n"
        "--BOUND--\r\n"
    ).encode()


class EncodedFilenameTests(unittest.TestCase):
    """Jméno přílohy s diakritikou chodí MIME-kódované (RFC 2047).

    Regrese z ostrého provozu: kontrola přípony běžela nad ZAKÓDOVANÝM jménem
    („=?UTF-8?Q?Faktura_vydan=C3=A1…=2Eisdoc?=“), které na „.isdoc“ nekončí —
    česká faktura se tiše zahodila a nikde se to neprojevilo.
    """

    def test_diacritics_pdf_is_accepted(self):
        raw = _make_raw_email_rfc2047(
            "=?UTF-8?Q?Faktura_vydan=C3=A1_Dodavatel=2D20260133=2Epdf?=")
        result, _ = _run([(b"1", raw)], _config())
        self.assertEqual([a.filename for a in result],
                         ["Faktura vydaná Dodavatel-20260133.pdf"])

    def test_diacritics_isdoc_is_accepted(self):
        raw = _make_raw_email_rfc2047(
            "=?UTF-8?Q?Faktura_vydan=C3=A1_Dodavatel=2Eisdoc?=", subtype="xml",
            body=b"<Invoice/>")
        result, _ = _run([(b"1", raw)], _config())
        self.assertEqual([a.filename for a in result], ["Faktura vydaná Dodavatel.isdoc"])

    def test_base64_encoded_filename(self):
        # „DŮLEŽITÉ UPOZORNĚNÍ.pdf“ rozdělené do dvou B-slov, jak to poslal klient
        raw = _make_raw_email_rfc2047(
            "=?UTF-8?B?RMWuTEXFvUlUw4kgVVBPWk9STsSaTsONLnA=?= =?UTF-8?B?ZGY=?=")
        result, _ = _run([(b"1", raw)], _config())
        self.assertEqual([a.filename for a in result], ["DŮLEŽITÉ UPOZORNĚNÍ.pdf"])

    def test_encoded_name_with_wrong_extension_still_filtered(self):
        raw = _make_raw_email_rfc2047("=?UTF-8?Q?P=C5=99=C3=ADloh=C3=A1=2Eexe?=",
                                      subtype="octet-stream")
        result, _ = _run([(b"1", raw)], _config())
        self.assertEqual(result, [])


class SearchCriteriaTests(unittest.TestCase):
    """Kritérium výběru zpráv jde přenastavit (kdo má vlastní deduplikaci)."""

    def test_default_is_unseen(self):
        raw = _make_email("d@x.cz", "F", "f.pdf", b"%PDF")
        fake = FakeIMAP([(b"1", raw)])
        seen = {}
        fake.search = lambda charset, crit: (seen.setdefault("crit", crit), ("OK", [b"1"]))[1]
        fetch_invoice_attachments(_config(), open_connection=lambda c: fake)
        self.assertEqual(seen["crit"], "UNSEEN")

    def test_all_criteria_is_passed_through(self):
        raw = _make_email("d@x.cz", "F", "f.pdf", b"%PDF")
        fake = FakeIMAP([(b"1", raw)])
        seen = {}
        fake.search = lambda charset, crit: (seen.setdefault("crit", crit), ("OK", [b"1"]))[1]
        fetch_invoice_attachments(_config(search_criteria="ALL"), open_connection=lambda c: fake)
        self.assertEqual(seen["crit"], "ALL")


class MessageReportTests(unittest.TestCase):
    """Každá zpráva musí nechat stopu — i ta, ze které nic nevzešlo."""

    def test_message_without_attachment_is_reported(self):
        msg = EmailMessage()
        msg["From"] = "Dodavatel <d@x.cz>"
        msg["Subject"] = "Faktura je v textu"
        msg["Message-ID"] = "<abc@x.cz>"
        msg.set_content("Fakturujeme vam 1000 Kc")
        fake = FakeIMAP([(b"1", msg.as_bytes())])
        result = fetch_invoice_messages(_config(), open_connection=lambda c: fake)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].attachments, ())
        self.assertEqual(result[0].skipped, ())
        self.assertEqual(result[0].subject, "Faktura je v textu")
        self.assertEqual(result[0].message_id, "<abc@x.cz>")

    def test_unsupported_attachment_is_reported_with_reason(self):
        raw = _make_email("d@x.cz", "Faktura", "faktura.zip", b"PK\x03\x04",
                          subtype="zip")
        result = fetch_invoice_messages(_config(), open_connection=lambda c: FakeIMAP([(b"1", raw)]))
        self.assertEqual(result[0].attachments, ())
        self.assertEqual([(s.filename, s.reason) for s in result[0].skipped],
                         [("faktura.zip", "extension")])

    def test_too_big_attachment_is_reported(self):
        raw = _make_email("d@x.cz", "Faktura", "velka.pdf", b"x" * 500)
        result = fetch_invoice_messages(_config(max_bytes=100),
                                        open_connection=lambda c: FakeIMAP([(b"1", raw)]))
        self.assertEqual([(s.filename, s.reason) for s in result[0].skipped],
                         [("velka.pdf", "too_big")])
        self.assertEqual(result[0].skipped[0].size, 500)

    def test_blocked_sender_is_reported_not_silently_dropped(self):
        raw = _make_email("cizi@jinde.cz", "Reklama", "letak.pdf", b"%PDF")
        result = fetch_invoice_messages(_config(allowed_senders=("d@x.cz",)),
                                        open_connection=lambda c: FakeIMAP([(b"1", raw)]))
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].sender_blocked)
        self.assertEqual(result[0].attachments, ())

    def test_mixed_message_reports_both(self):
        msg = EmailMessage()
        msg["From"] = "Dodavatel <d@x.cz>"
        msg["Subject"] = "Faktura + priloha navic"
        msg.set_content("text")
        msg.add_attachment(b"%PDF", maintype="application", subtype="pdf",
                           filename="faktura.pdf")
        msg.add_attachment(b"PK", maintype="application", subtype="zip",
                           filename="ostatni.zip")
        result = fetch_invoice_messages(_config(), open_connection=lambda c: FakeIMAP([(b"1", msg.as_bytes())]))
        self.assertEqual([a.filename for a in result[0].attachments], ["faktura.pdf"])
        self.assertEqual([s.filename for s in result[0].skipped], ["ostatni.zip"])

    def test_attachments_wrapper_still_works(self):
        raw = _make_email("d@x.cz", "Faktura", "faktura.pdf", b"%PDF")
        result = fetch_invoice_attachments(_config(), open_connection=lambda c: FakeIMAP([(b"1", raw)]))
        self.assertEqual([a.filename for a in result], ["faktura.pdf"])
