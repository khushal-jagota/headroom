"""What a failed codex turn says about the login, read from the turn's own words.

A codex turn that fails because the saved login is dead does not say so. It names the
subsystem that got the refusal — workspace routing discovery, model listing, whatever
asked first — and the reason sits in the child's standard error, where only an operator
reading server logs will find it. That is how a dead login spent twelve days looking like
a routing fault.

So the turn is classified before its ending is recorded, from the two things already in
hand: codex's own error text, and what this turn wrote on standard error.

The grading is deliberately cautious, because the two wrong answers cost different
amounts. Telling a person to sign in again when the real fault was a network blip sends
them to spend a login they did not need to. Saying nothing when the login is genuinely
rejected is this ticket. So an explicit refusal is reported as one, a bare refusal is
reported as a refusal and nothing more, and everything else keeps codex's own words.
"""

from __future__ import annotations

import re
from enum import StrEnum

# Codex's own login machinery, which is the only thing on this wire whose word about the
# login counts. A turn's standard error carries everything the child writes, including
# output from tools the agent ran, and plenty of command-line tools say "not logged in"
# about accounts that have nothing to do with codex. A phrase is read as codex's verdict
# only on a line codex's auth manager wrote.
_CODEX_AUTH_SPEAKER = re.compile(r"codex_login::auth|\bauth:")

# Codex said the saved login itself is finished: nothing but a new sign-in will fix it.
_LOGIN_IS_REJECTED_PHRASES = (
    "refresh token was already used",
    "log out and sign in again",
    "not logged in",
    "you are logged out",
    "invalid_grant",
)

# The request was refused for authorization, without anything naming the saved login.
# The status codes are matched as whole words: a duration of 1403ms and a timestamp
# ending 401Z are not refusals, and they are what unanchored digits would have found.
_AUTHORIZATION_WAS_REFUSED = re.compile(
    r"unauthorized|forbidden|authentication failed|\b401\b|\b403\b"
)


class CodexAuthReading(StrEnum):
    """What a failed turn establishes about the login.

    ``login_is_rejected`` is the only reading that speaks for the login, and the only one
    remembered. ``authorization_was_refused`` is the honest middle: a refusal happened and
    its cause is not stated. ``says_nothing_about_the_login`` covers every other failure,
    including a timeout or a dead child, and keeps codex's own account of it.
    """

    login_is_rejected = "login_is_rejected"
    authorization_was_refused = "authorization_was_refused"
    says_nothing_about_the_login = "says_nothing_about_the_login"


LOGIN_IS_REJECTED_SUMMARY = (
    "Codex is signed out: it has a saved login and the server rejected it. "
    "Run `codex login` on this machine, then send this again."
)


def read_a_failed_turn(
    *, error_summary: str | None, standard_error_this_turn: str | None
) -> CodexAuthReading:
    """Grade one failed turn. Both inputs belong to that turn and no other.

    Codex's error text is codex speaking about its own turn, so it is read whole. Standard
    error is not: it is whatever the child and its tools wrote, so only the lines codex's
    login machinery wrote can establish that the saved login was refused.
    """
    codex_said = (error_summary or "").lower()
    about_the_login = "\n".join(
        (
            codex_said,
            *(
                line.lower()
                for line in (standard_error_this_turn or "").splitlines()
                if _CODEX_AUTH_SPEAKER.search(line)
            ),
        )
    )
    if any(phrase in about_the_login for phrase in _LOGIN_IS_REJECTED_PHRASES):
        return CodexAuthReading.login_is_rejected
    if _AUTHORIZATION_WAS_REFUSED.search(codex_said):
        return CodexAuthReading.authorization_was_refused
    return CodexAuthReading.says_nothing_about_the_login


def error_summary_for(reading: CodexAuthReading, error_summary: str | None) -> str | None:
    """The line a person reads under a failed turn.

    A rejected login replaces codex's text, because codex's text sent the last diagnosis
    in the wrong direction for twelve days. A bare refusal keeps the text and adds the one
    thing worth adding, which is that the sign-in is a candidate and not a conclusion.
    """
    if reading is CodexAuthReading.login_is_rejected:
        return LOGIN_IS_REJECTED_SUMMARY
    if reading is CodexAuthReading.authorization_was_refused:
        refused = "the request was refused; the Codex sign-in may need attention"
        return f"{error_summary} — {refused}" if error_summary else refused.capitalize()
    return error_summary
