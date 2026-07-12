"""
Integration tests for granularity detection wired into the inspect/columns
wizard endpoints (Data Alignment Wizard — detection phase).
"""
import io


def _csv_bytes(rows: list[tuple[str, str, float]]) -> bytes:
    buf = io.StringIO()
    buf.write("date,sku,sales\n")
    for d, sku, sales in rows:
        buf.write(f"{d},{sku},{sales}\n")
    return buf.getvalue().encode("utf-8")


def _daily_and_weekly_rows() -> list[tuple[str, str, float]]:
    import pandas as pd
    rows = []
    for d in pd.date_range("2024-01-01", periods=10, freq="D"):
        rows.append((d.strftime("%Y-%m-%d"), "DAILY_SKU", 5.0))
    for d in pd.date_range("2024-01-01", periods=6, freq="W"):
        rows.append((d.strftime("%Y-%m-%d"), "WEEKLY_SKU", 20.0))
    return rows


class TestInspectGranularity:
    def test_inspect_reports_conflict_for_mixed_frequencies(self, client, auth_headers, test_session):
        sid = test_session["id"]
        csv_bytes = _csv_bytes(_daily_and_weekly_rows())
        up = client.post(
            "/api/v1/datasets",
            files={"file": ("mixed_freq.csv", csv_bytes, "text/csv")},
            headers=auth_headers,
        )
        assert up.status_code == 201, up.text
        dataset_id = up.json()["data"]["id"]

        client.post(f"/api/v1/sessions/{sid}/dataset", json={"dataset_id": dataset_id}, headers=auth_headers)
        r = client.get(f"/api/v1/sessions/{sid}/inspect", headers=auth_headers)
        assert r.status_code == 200, r.text
        granularity = r.json()["data"]["granularity"]
        assert granularity["status"] == "conflict"
        assert granularity["detected"] == ["D", "W"]
        assert "DAILY_SKU" in granularity["skus_by_frequency"]["D"]
        assert "WEEKLY_SKU" in granularity["skus_by_frequency"]["W"]
        assert granularity["suggested_target"] == "W"

    def test_inspect_reports_homogeneous_for_uniform_frequency(
        self, client, auth_headers, test_session, uploaded_dataset,
    ):
        sid = test_session["id"]
        client.post(
            f"/api/v1/sessions/{sid}/dataset",
            json={"dataset_id": uploaded_dataset["id"]},
            headers=auth_headers,
        )
        r = client.get(f"/api/v1/sessions/{sid}/inspect", headers=auth_headers)
        assert r.status_code == 200, r.text
        granularity = r.json()["data"]["granularity"]
        assert granularity["status"] == "homogeneous"

    def test_configure_columns_revalidates_with_confirmed_columns(
        self, client, auth_headers, test_session,
    ):
        """
        Confirming "sku" explicitly as the group column in configure/columns
        must make the re-validation see the true per-SKU frequency split,
        independent of whatever the profiler auto-detected at /inspect time.
        """
        sid = test_session["id"]
        csv_bytes = _csv_bytes(_daily_and_weekly_rows())
        up = client.post(
            "/api/v1/datasets",
            files={"file": ("mixed_freq2.csv", csv_bytes, "text/csv")},
            headers=auth_headers,
        )
        dataset_id = up.json()["data"]["id"]
        client.post(f"/api/v1/sessions/{sid}/dataset", json={"dataset_id": dataset_id}, headers=auth_headers)
        client.get(f"/api/v1/sessions/{sid}/inspect", headers=auth_headers)

        r = client.post(
            f"/api/v1/sessions/{sid}/configure/columns",
            json={"date_column": "date", "target_column": "sales", "sku_column": "sku"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        granularity = r.json()["data"]["granularity"]
        assert granularity["status"] == "conflict"
        assert granularity["detected"] == ["D", "W"]

    def test_configure_columns_finds_conflict_auto_detection_missed(
        self, client, auth_headers, test_session,
    ):
        """
        Genuine proof of independence: the group/SKU column here ("code") has
        only 2 numeric values, so the profiler's auto-detector skips it (its
        cardinality heuristic requires >=10 distinct values for a purely
        numeric column) — /inspect sees no group column at all and reports
        the whole file as a single (homogeneous) series. Only once the user
        explicitly CONFIRMS "code" as the SKU column in configure/columns does
        the true per-SKU daily/weekly split surface. If configure_columns
        silently reused /inspect's auto-detected (missing) group column
        instead of the confirmed one, this test would see "homogeneous", not
        "conflict".
        """
        sid = test_session["id"]
        import pandas as pd
        buf_rows = ["date,code,sales"]
        for d in pd.date_range("2024-01-01", periods=10, freq="D"):
            buf_rows.append(f"{d.strftime('%Y-%m-%d')},1,5.0")
        for d in pd.date_range("2024-01-01", periods=6, freq="W"):
            buf_rows.append(f"{d.strftime('%Y-%m-%d')},2,20.0")
        csv_bytes = ("\n".join(buf_rows) + "\n").encode("utf-8")

        up = client.post(
            "/api/v1/datasets",
            files={"file": ("hidden_group_col.csv", csv_bytes, "text/csv")},
            headers=auth_headers,
        )
        assert up.status_code == 201, up.text
        dataset_id = up.json()["data"]["id"]
        client.post(f"/api/v1/sessions/{sid}/dataset", json={"dataset_id": dataset_id}, headers=auth_headers)

        inspect_r = client.get(f"/api/v1/sessions/{sid}/inspect", headers=auth_headers)
        assert inspect_r.status_code == 200, inspect_r.text
        # Auto-detection missed the group column entirely — the mixed daily/
        # weekly rows collapse into one undifferentiated series.
        assert inspect_r.json()["data"]["granularity"]["status"] == "homogeneous"

        configure_r = client.post(
            f"/api/v1/sessions/{sid}/configure/columns",
            json={"date_column": "date", "target_column": "sales", "sku_column": "code"},
            headers=auth_headers,
        )
        assert configure_r.status_code == 200, configure_r.text
        granularity = configure_r.json()["data"]["granularity"]
        assert granularity["status"] == "conflict"
        assert granularity["detected"] == ["D", "W"]
        assert granularity["skus_by_frequency"]["D"] == ["1"]
        assert granularity["skus_by_frequency"]["W"] == ["2"]

    def test_configure_columns_viewer_denied(
        self, client, auth_headers, viewer_headers, test_session, uploaded_dataset,
    ):
        sid = test_session["id"]
        client.post(
            f"/api/v1/sessions/{sid}/dataset",
            json={"dataset_id": uploaded_dataset["id"]},
            headers=auth_headers,
        )
        client.get(f"/api/v1/sessions/{sid}/inspect", headers=auth_headers)

        vr = client.post(
            f"/api/v1/sessions/{sid}/configure/columns",
            json={"date_column": "date", "target_column": "sales", "sku_column": "sku"},
            headers=viewer_headers,
        )
        assert vr.status_code == 403
