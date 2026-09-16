#!/usr/bin/env python3
"""Smoke tests for the tv_schedule landing path in bq_ingest.py, using mocked
GCP clients (_land_and_load_rows takes storage_client/bq_client as plain
arguments, so no real GCP credentials are needed to exercise the tagging,
blob-path, and error-handling logic)."""

from unittest.mock import MagicMock

from bq_ingest import _land_and_load_rows, _fetch_tv_schedule_rows, FULL_SEASON_WEEK


def _mock_clients(num_rows=5):
    storage_client = MagicMock()
    bq_client = MagicMock()
    bq_client.get_table.return_value.num_rows = num_rows
    return storage_client, bq_client


def test_empty_rows_short_circuits():
    storage_client, bq_client = _mock_clients()
    result = _land_and_load_rows(
        storage_client, bq_client, "proj", "ds", "bucket",
        "tv_schedule", [], "tv_schedule", 2026, 3, "20260101T000000Z", "2026-01-01T00:00:00",
    )
    assert result == {"rows_loaded": 0}
    storage_client.bucket.assert_not_called()


def test_rows_get_tagged_and_landed():
    storage_client, bq_client = _mock_clients(num_rows=42)
    rows = [{"away_team": "A", "home_team": "B", "network": "ESPN"}]

    result = _land_and_load_rows(
        storage_client, bq_client, "my-project", "my_dataset", "my-bucket",
        "tv_schedule", rows, "tv_schedule", 2026, 3, "20260101T000000Z", "2026-01-01T00:00:00",
    )

    # Metadata tags applied in place
    assert rows[0]["_source_endpoint"] == "tv_schedule"
    assert rows[0]["_year"] == 2026
    assert rows[0]["_week"] == 3
    assert rows[0]["_ingested_at"] == "2026-01-01T00:00:00"

    # Landed at the expected GCS path, loaded into the expected BQ table
    blob_call = storage_client.bucket.return_value.blob.call_args[0][0]
    assert blob_call == "raw/tv_schedule/year=2026/week=3/20260101T000000Z.json"
    assert result == {
        "rows_loaded": 1,
        "table": "my-project.my_dataset.raw_tv_schedule",
        "table_total_rows": 42,
        "gcs_uri": "gs://my-bucket/raw/tv_schedule/year=2026/week=3/20260101T000000Z.json",
    }


def test_full_season_week_uses_all_label_in_path():
    storage_client, bq_client = _mock_clients()
    rows = [{"away_team": "A", "home_team": "B"}]

    _land_and_load_rows(
        storage_client, bq_client, "proj", "ds", "bucket",
        "tv_schedule", rows, "tv_schedule", 2026, FULL_SEASON_WEEK, "run-ts", "2026-01-01T00:00:00",
    )

    blob_call = storage_client.bucket.return_value.blob.call_args[0][0]
    assert "week=all" in blob_call, blob_call


def test_gcs_failure_reported_not_raised():
    storage_client, bq_client = _mock_clients()
    storage_client.bucket.return_value.blob.return_value.upload_from_string.side_effect = RuntimeError("gcs down")

    result = _land_and_load_rows(
        storage_client, bq_client, "proj", "ds", "bucket",
        "tv_schedule", [{"a": 1}], "tv_schedule", 2026, 1, "run-ts", "2026-01-01T00:00:00",
    )
    assert "error" in result and "gcs landing failed" in result["error"]


def test_bq_load_failure_reported_not_raised():
    storage_client, bq_client = _mock_clients()
    bq_client.load_table_from_uri.side_effect = RuntimeError("bq down")

    result = _land_and_load_rows(
        storage_client, bq_client, "proj", "ds", "bucket",
        "tv_schedule", [{"a": 1}], "tv_schedule", 2026, 1, "run-ts", "2026-01-01T00:00:00",
    )
    assert "error" in result and "bq load failed" in result["error"]
    assert "gcs_uri" in result, "GCS landing succeeded before the BQ failure -- should still report the gcs_uri"


def test_fetch_tv_schedule_rows_routes_full_season(monkeypatch):
    import fbschedules

    calls = {}

    def fake_full_season(year):
        calls["full_season"] = year
        return ["row"]

    def fake_single_week(year, week):
        calls["single_week"] = (year, week)
        return ["row"]

    monkeypatch.setattr(fbschedules, "fetch_full_season", fake_full_season)
    monkeypatch.setattr(fbschedules, "fetch_tv_schedule", fake_single_week)

    assert _fetch_tv_schedule_rows(2026, FULL_SEASON_WEEK) == ["row"]
    assert calls == {"full_season": 2026}

    calls.clear()
    assert _fetch_tv_schedule_rows(2026, 5) == ["row"]
    assert calls == {"single_week": (2026, 5)}


if __name__ == "__main__":
    test_empty_rows_short_circuits()
    test_rows_get_tagged_and_landed()
    test_full_season_week_uses_all_label_in_path()
    test_gcs_failure_reported_not_raised()
    test_bq_load_failure_reported_not_raised()

    # monkeypatch-style test run manually since this file has no pytest runner
    import fbschedules as _fb
    _orig_full, _orig_single = _fb.fetch_full_season, _fb.fetch_tv_schedule
    try:
        class _MP:
            def setattr(self, obj, name, val):
                setattr(obj, name, val)
        test_fetch_tv_schedule_rows_routes_full_season(_MP())
    finally:
        _fb.fetch_full_season, _fb.fetch_tv_schedule = _orig_full, _orig_single

    print("All bq_ingest tv_schedule landing tests passed.")
