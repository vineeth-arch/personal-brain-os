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


def test_the_draft_endpoint_returns_raw_channels_not_links():
    """The API hands back the phone number and the email address; turning those
    into a wa.me/mailto URL is the browser's job, one tap from the human."""
    from api import people

    source = (REPO_ROOT / "api" / "people.py").read_text()
    assert "wa.me" not in source and "mailto:" not in source
    assert '"channels": person.channels' in source, (
        "draft() must return the raw channel values so the link is built client-side")
