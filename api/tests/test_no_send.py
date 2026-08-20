"""CLAUDE.md §4, structurally — the server never delivers a message.

Pass MW gives the cockpit one-tap reconnection drafts, which is exactly the
feature that would tempt someone to "just fire the WhatsApp link from the
server". The deep link is built in the BROWSER, from raw channel values the API
returns; the human presses send. This test fails the build if a
message-delivery URL ever appears in server-side code, and proves the scanner
itself works by running it over a planted offender.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# every way a server could hand a message to a person
SEND_PATTERNS = re.compile(
    r"wa\.me|api\.whatsapp\.com|web\.whatsapp\.com|messages/send|/send\b|sendMessage|"
    r"smtplib|twilio|/messages\.json",
    re.IGNORECASE)

# server-side only: the browser is where deep links belong, and web/src is
# checked by the reviewer, not by this scanner
SERVER_FOLDERS = ("api", "pipeline", "scripts")
# the two guard tests spell the patterns out in order to forbid them
GUARD_FILES = {Path(__file__).name, "test_google.py"}


def scan(root: Path, folders=SERVER_FOLDERS) -> list[str]:
    offenders = []
    for folder in folders:
        target = root / folder
        if not target.is_dir():
            continue
        for path in target.rglob("*"):
            if path.suffix != ".py" or not path.is_file() or path.name in GUARD_FILES:
                continue
            for i, line in enumerate(path.read_text().splitlines(), start=1):
                if SEND_PATTERNS.search(line):
                    offenders.append(f"{path.relative_to(root)}:{i}: {line.strip()}")
    return offenders


def test_no_server_side_send_path():
    offenders = scan(REPO_ROOT)
    assert not offenders, (
        "Server-side code can now deliver a message to a person. CLAUDE.md §4 "
        "forbids it — drafts are prepared here and sent by the human, outside "
        "this app. Offending lines:\n" + "\n".join(offenders))


def test_the_scanner_actually_catches_a_planted_send(tmp_path):
    """A guard nobody has seen fail is not a guard."""
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "helpful.py").write_text(
        'def nudge(person, text):\n'
        '    url = f"https://wa.me/{person.phone}?text={text}"\n'
        '    urllib.request.urlopen(url)\n')
    offenders = scan(tmp_path)
    assert len(offenders) == 1 and "wa.me" in offenders[0]


# Pass D added two modules that talk to external services about PEOPLE. That is
# exactly the shape that would tempt a future session to add "…and message
# them while we're in there". These modules write PROFILE DATA — a CRM
# description, an address-book notes field — and nothing else.
PROFILE_ONLY_MODULES = ("pipeline/dex.py", "api/google_contacts.py")

# NB: reading a person's phone number out of `channels` is how a contact is
# MATCHED, so the channel key itself is legitimate here — what's forbidden is a
# way to deliver something to that number.
MESSAGING_PATTERNS = re.compile(
    r"wa\.me|api\.whatsapp|web\.whatsapp|/send\b|:send\b|sendMessage|send_message|"
    r"sendMail|messages\.send|smtplib|twilio|mailto:|/drafts\b|\bsms\b",
    re.IGNORECASE)


def scan_profile_modules(root: Path) -> list[str]:
    offenders = []
    for relative in PROFILE_ONLY_MODULES:
        path = root / relative
        if not path.is_file():
            continue
        for i, line in enumerate(path.read_text().splitlines(), start=1):
            if MESSAGING_PATTERNS.search(line):
                offenders.append(f"{relative}:{i}: {line.strip()}")
    return offenders


def test_the_push_modules_write_profile_data_never_messages():
    offenders = scan_profile_modules(REPO_ROOT)
    assert not offenders, (
        "A Dex/Contacts push module now references a messaging surface. These "
        "modules update a contact record and nothing else — pushing a profile "
        "must never become a way to contact somebody (CLAUDE.md §4). "
        "Offending lines:\n" + "\n".join(offenders))


def test_the_profile_scanner_catches_a_planted_send(tmp_path):
    """The same 'a guard nobody has seen fail is not a guard' rule."""
    (tmp_path / "pipeline").mkdir()
    (tmp_path / "pipeline" / "dex.py").write_text(
        'def push_and_ping(contact, text):\n'
        '    update_description(contact, text)\n'
        '    urllib.request.urlopen(f"https://wa.me/{contact.phone}?text=hi")\n')
    offenders = scan_profile_modules(tmp_path)
    assert len(offenders) == 1 and "wa.me" in offenders[0]


def test_the_draft_endpoint_returns_raw_channels_not_links():
    """The API hands back the phone number and the email address; turning those
    into a wa.me/mailto URL is the browser's job, one tap from the human."""
    from api import people

    source = (REPO_ROOT / "api" / "people.py").read_text()
    assert "wa.me" not in source and "mailto:" not in source
    assert '"channels": person.channels' in source, (
        "draft() must return the raw channel values so the link is built client-side")
