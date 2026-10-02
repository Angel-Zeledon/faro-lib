"""Which version of the Terms and the Privacy Policy a user accepted.

The documents themselves live in the frontend (`Frontend/src/i18n/legal.ts`,
rendered at /terminos and /privacidad). The backend only records WHEN a person
accepted them and WHICH version — the date the documents carry as their "last
updated" line. Bump this constant in the same commit that changes either
document in a way users must accept again; every acceptance recorded from then
on carries the new version, and the old rows keep the one their owner saw.

Users created before acceptance was recorded (2026-10-02) have NULL in both
columns. That is the truth about them, not a gap to backfill: nobody can be
said to have accepted a document after the fact.
"""

TERMS_VERSION = "2026-10-02"
