"""Příjem příloh faktur z IMAP schránky – frameworkově neutrální (jen stdlib).

Vrací dataclassy :class:`FetchedAttachment`; založení záznamu/uložení souboru je
věc Django slupky (management command). Spojení jde vstříknout (``open_connection``)
kvůli testům bez sítě.

Bezpečnost: obsah e-mailu je nedůvěryhodný. Bereme jen přílohy s povolenou
příponou (PDF/ISDOC/XML) do velikostního limitu a volitelně jen od povolených
odesílatelů. Parsování ISDOC (XML) dělá jádro – pro ostrý provoz zvaž defusedxml.
"""

from __future__ import annotations

import email
import imaplib
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.utils import parseaddr

DEFAULT_EXTENSIONS = (".pdf", ".isdoc", ".xml")
DEFAULT_MAX_BYTES = 15 * 1024 * 1024  # 15 MB / příloha


@dataclass(frozen=True)
class MailboxConfig:
    host: str
    username: str
    password: str
    port: int = 993
    use_ssl: bool = True
    folder: str = "INBOX"
    mark_seen: bool = True
    allowed_senders: tuple[str, ...] = ()
    allowed_extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
    max_bytes: int = DEFAULT_MAX_BYTES


@dataclass(frozen=True)
class FetchedAttachment:
    filename: str
    content: bytes
    sender: str = ""
    subject: str = ""
    message_uid: str = ""


def _decode(value: str) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _raw_message(msgdata) -> bytes | None:
    """Vytáhne RFC822 bajty z odpovědi IMAP fetch (různě zanořené tuply)."""
    if not msgdata:
        return None
    for item in msgdata:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def _open_imap(config: MailboxConfig):
    cls = imaplib.IMAP4_SSL if config.use_ssl else imaplib.IMAP4
    conn = cls(config.host, config.port)
    conn.login(config.username, config.password)
    conn.select(config.folder)
    return conn


def fetch_invoice_attachments(config: MailboxConfig, *, open_connection=None) -> list[FetchedAttachment]:
    """Stáhne přílohy faktur z nepřečtených e-mailů. Vrací seznam příloh.

    ``open_connection`` (kvůli testům) je callable ``(config) -> conn`` vracející
    již přihlášené a vybrané spojení; jinak se sestaví reálné IMAP spojení.
    """
    conn = (open_connection or _open_imap)(config)
    allowed_ext = tuple(e.lower() for e in config.allowed_extensions)
    allowed_senders = tuple(s.lower() for s in config.allowed_senders)
    attachments: list[FetchedAttachment] = []
    try:
        typ, data = conn.search(None, "UNSEEN")
        uids = data[0].split() if data and data[0] else []
        for uid in uids:
            typ, msgdata = conn.fetch(uid, "(RFC822)")
            raw = _raw_message(msgdata)
            if not raw:
                continue
            message = email.message_from_bytes(raw)
            sender = parseaddr(message.get("From", ""))[1].lower()
            subject = _decode(message.get("Subject", ""))
            uid_str = uid.decode() if isinstance(uid, (bytes, bytearray)) else str(uid)

            if allowed_senders and sender not in allowed_senders:
                _mark_seen(conn, uid, config)
                continue

            for part in message.walk():
                filename = part.get_filename()
                if not filename or not filename.lower().endswith(allowed_ext):
                    continue
                payload = part.get_payload(decode=True) or b""
                if not payload or len(payload) > config.max_bytes:
                    continue
                attachments.append(FetchedAttachment(
                    filename=_decode(filename),
                    content=payload,
                    sender=sender,
                    subject=subject,
                    message_uid=uid_str,
                ))
            _mark_seen(conn, uid, config)
    finally:
        for close in (getattr(conn, "close", None), getattr(conn, "logout", None)):
            if close is not None:
                try:
                    close()
                except Exception:
                    pass
    return attachments


def _mark_seen(conn, uid, config: MailboxConfig) -> None:
    if config.mark_seen:
        try:
            conn.store(uid, "+FLAGS", "\\Seen")
        except Exception:
            pass
