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
import re
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.utils import parseaddr

DEFAULT_EXTENSIONS = (
    ".pdf", ".isdoc", ".xml", ".xlsx",
    ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff",
)
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
    # IMAP kritérium výběru zpráv. Výchozí "UNSEEN" zpracuje jen nepřečtené,
    # což je levné, ale křehké: kdokoli si mail otevře v poštovním klientovi
    # (nebo ho do schránky sám přepošle), faktura se už nikdy nenačte. Kdo si
    # nese vlastní deduplikaci, může dát "ALL" a na příznak přečtení nespoléhat.
    search_criteria: str = "UNSEEN"
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
    body_text: str = ""


@dataclass(frozen=True)
class SkippedAttachment:
    """Příloha, kterou jsme NEVZALI — i s důvodem.

    Tiché zahození je nejhorší možné chování: faktura zmizí a nikdo se to
    nedozví. Konzument si důvod uloží a ukáže člověku.
    """
    filename: str
    reason: str          # "extension" | "too_big" | "empty"
    size: int = 0


@dataclass(frozen=True)
class FetchedMessage:
    """Jedna zpráva ze schránky se VŠÍM, co se z ní dalo zjistit.

    Vrací se i pro zprávu, ze které nic nevzešlo (bez příloh, samé nepovolené
    typy, zablokovaný odesílatel) — právě aby bylo vidět, že přišla.
    """
    uid: str
    sender: str = ""
    subject: str = ""
    date: str = ""
    message_id: str = ""
    body_text: str = ""
    attachments: tuple[FetchedAttachment, ...] = ()
    skipped: tuple[SkippedAttachment, ...] = ()
    sender_blocked: bool = False


def _decode(value: str) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_body_text(message, *, max_chars: int = 20000) -> str:
    """Vytáhne čitelný text těla e-mailu. Preferuje text/plain; když není,
    z text/html hrubě odstraní značky. Přílohy (mají filename) přeskočí.

    Bezpečnost: vrací jen prostý text (žádné HTML) — konzument ho má stejně
    zobrazit escapovaně; obsah je nedůvěryhodný."""
    plain: list[str] = []
    html: list[str] = []
    for part in message.walk():
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except (LookupError, ValueError):
            text = payload.decode("utf-8", errors="replace")
        (plain if ctype == "text/plain" else html).append(text)

    out = "\n".join(t.strip() for t in plain if t.strip())
    if not out and html:
        stripped = re.sub(r"(?is)<(script|style).*?</\1>", " ", "\n".join(html))
        stripped = re.sub(r"(?i)<br\s*/?>", "\n", stripped)
        stripped = re.sub(r"<[^>]+>", " ", stripped)
        import html as _html
        out = re.sub(r"[ \t]+", " ", _html.unescape(stripped)).strip()
    return out[:max_chars]


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


def fetch_invoice_messages(config: MailboxConfig, *, open_connection=None) -> list[FetchedMessage]:
    """Projde schránku a vrátí ZÁZNAM O KAŽDÉ zprávě, ne jen přijaté přílohy.

    Zpráva se vrací i tehdy, když z ní nic nevzešlo — bez příloh, samé
    nepovolené typy, zablokovaný odesílatel. Konzument tak může ukázat,
    co přišlo a proč se z toho nestala faktura; tiché zahození je přesně
    ten způsob, jak se ztrácejí doklady.

    Které zprávy se berou, řídí ``config.search_criteria`` (výchozí ``UNSEEN``).
    ``open_connection`` (kvůli testům) je callable ``(config) -> conn``.
    """
    conn = (open_connection or _open_imap)(config)
    allowed_ext = tuple(e.lower() for e in config.allowed_extensions)
    allowed_senders = tuple(s.lower() for s in config.allowed_senders)
    messages: list[FetchedMessage] = []
    try:
        typ, data = conn.search(None, config.search_criteria or "UNSEEN")
        uids = data[0].split() if data and data[0] else []
        for uid in uids:
            uid_str = uid.decode() if isinstance(uid, (bytes, bytearray)) else str(uid)
            typ, msgdata = conn.fetch(uid, "(RFC822)")
            raw = _raw_message(msgdata)
            if not raw:
                # I nečitelná zpráva musí být vidět, ať se neztratí beze stopy.
                messages.append(FetchedMessage(uid=uid_str, subject="(zprávu se nepodařilo načíst)"))
                continue
            message = email.message_from_bytes(raw)
            sender = parseaddr(message.get("From", ""))[1].lower()
            subject = _decode(message.get("Subject", ""))
            date = _decode(message.get("Date", ""))
            message_id = (message.get("Message-ID", "") or "").strip()

            if allowed_senders and sender not in allowed_senders:
                messages.append(FetchedMessage(
                    uid=uid_str, sender=sender, subject=subject, date=date,
                    message_id=message_id, sender_blocked=True))
                _mark_seen(conn, uid, config)
                continue

            body_text = _extract_body_text(message)
            taken: list[FetchedAttachment] = []
            skipped: list[SkippedAttachment] = []

            for part in message.walk():
                # Dekódovat MIME hlavičku MUSÍME PŘED kontrolou přípony. Jméno
                # s diakritikou přijde jako "=?UTF-8?Q?Faktura_vydan=C3=A1…=2Epdf?="
                # a to na ".pdf" nekončí — česká faktura by se tiše zahodila.
                filename = _decode(part.get_filename() or "")
                if not filename:
                    continue
                payload = part.get_payload(decode=True) or b""
                if not filename.lower().endswith(allowed_ext):
                    skipped.append(SkippedAttachment(filename, "extension", len(payload)))
                    continue
                if not payload:
                    skipped.append(SkippedAttachment(filename, "empty", 0))
                    continue
                if len(payload) > config.max_bytes:
                    skipped.append(SkippedAttachment(filename, "too_big", len(payload)))
                    continue
                taken.append(FetchedAttachment(
                    filename=filename, content=payload, sender=sender,
                    subject=subject, message_uid=uid_str, body_text=body_text,
                ))

            messages.append(FetchedMessage(
                uid=uid_str, sender=sender, subject=subject, date=date,
                message_id=message_id, body_text=body_text,
                attachments=tuple(taken), skipped=tuple(skipped),
            ))
            _mark_seen(conn, uid, config)
    finally:
        for close in (getattr(conn, "close", None), getattr(conn, "logout", None)):
            if close is not None:
                try:
                    close()
                except Exception:
                    pass
    return messages


def fetch_invoice_attachments(config: MailboxConfig, *, open_connection=None) -> list[FetchedAttachment]:
    """Jen přijaté přílohy (tenká vrstva nad :func:`fetch_invoice_messages`).

    Ponecháno kvůli volajícím, které nezajímá, co se nevzalo.
    """
    return [
        att
        for msg in fetch_invoice_messages(config, open_connection=open_connection)
        for att in msg.attachments
    ]


def _mark_seen(conn, uid, config: MailboxConfig) -> None:
    if config.mark_seen:
        try:
            conn.store(uid, "+FLAGS", "\\Seen")
        except Exception:
            pass
