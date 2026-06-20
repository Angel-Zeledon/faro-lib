"""
Documents API regression tests.

Covers the audit finding where a 0-byte file upload was silently accepted
and later marked INDEXED with a nonsensical page_count=1/chunk_count=0,
giving the user no indication the file had no indexable content.
"""

import pytest


class TestUploadValidation:

    def test_upload_empty_file_rejected(self, client, auth_headers):
        resp = client.post(
            "/api/v1/documents",
            files={"file": ("empty.txt", b"", "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 400, resp.text
        assert "empty" in resp.json()["detail"].lower()

    def test_upload_nonempty_file_accepted(self, client, auth_headers):
        resp = client.post(
            "/api/v1/documents",
            files={"file": ("note.txt", b"Some real content.", "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["file_size"] > 0
        assert data["status"] == "PENDING"

        # Cleanup: delete the doc (and its file on disk)
        client.delete(f"/api/v1/documents/{data['id']}", headers=auth_headers)
