"""Testy IMAP fetcheru – přes fake spojení, žádná síť."""

import unittest
from email.message import EmailMessage

from uctokit.invoices.mailbox import MailboxConfig, fetch_invoice_attachments


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

    def test_extension_filtered(self):
        raw = _make_email("d@x.cz", "Foto", "obrazek.png", b"PNGDATA", subtype="png")
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
