"""Roadmap 1.17: nightly memory self-report (tray snapshot ->
diagnostics bundle). Hermetic: every path lives under tmp_path."""

import io
import os
import zipfile

from focuscore import memreport


def _fake_snapshot(pid=4242, epoch=1_700_000_000.0, rss=80 * 1024 * 1024,
                   uptime=3600.0):
    return {
        "pid": pid,
        "recorded_at": "2023-11-15 12:00:00",
        "recorded_epoch": epoch,
        "uptime_s": uptime,
        "rss_bytes": rss,
    }


def test_snapshot_now_describes_this_process():
    snap = memreport.snapshot_now()
    assert snap["pid"] == os.getpid()
    assert snap["recorded_epoch"] > 0
    assert snap["recorded_at"]
    # This box runs the tests, so RSS must be measurable here.
    assert isinstance(snap["rss_bytes"], int) and snap["rss_bytes"] > 0
    assert snap["uptime_s"] is None or snap["uptime_s"] >= 0


def test_record_then_load_roundtrip(tmp_path):
    snap = _fake_snapshot()
    returned = memreport.record_snapshot(tmp_path, snapshot=snap)
    assert returned == snap
    assert memreport.load_snapshots(tmp_path) == [snap]


def test_record_is_bounded(tmp_path):
    for i in range(memreport.MAX_SNAPSHOTS + 3):
        memreport.record_snapshot(
            tmp_path, snapshot=_fake_snapshot(epoch=1000.0 + i))
    snaps = memreport.load_snapshots(tmp_path)
    assert len(snaps) == memreport.MAX_SNAPSHOTS
    # The oldest entries were dropped, the newest kept.
    assert snaps[-1]["recorded_epoch"] == 1000.0 + memreport.MAX_SNAPSHOTS + 2


def test_load_tolerates_missing_and_corrupt_file(tmp_path):
    assert memreport.load_snapshots(tmp_path) == []
    memreport.snapshot_path(tmp_path).write_text("not json",
                                                  encoding="utf-8")
    assert memreport.load_snapshots(tmp_path) == []
    # Recording over the corrupt file replaces it with a valid one.
    memreport.record_snapshot(tmp_path, snapshot=_fake_snapshot())
    assert len(memreport.load_snapshots(tmp_path)) == 1


def test_summarize_reports_growth_vs_20h_older_same_pid():
    older = _fake_snapshot(epoch=1_700_000_000.0, rss=80 * 1024 * 1024)
    newer = _fake_snapshot(epoch=1_700_000_000.0 + 25 * 3600,
                           rss=85 * 1024 * 1024, uptime=90000.0)
    summary = memreport.summarize([older, newer])
    assert summary["latest"] == newer
    assert summary["growth_bytes"] == 5 * 1024 * 1024
    assert summary["compared_to"] == older


def test_summarize_growth_needs_same_pid_and_enough_gap():
    base = _fake_snapshot(epoch=1_700_000_000.0)
    other_pid = _fake_snapshot(pid=9999, epoch=1_700_000_000.0 + 30 * 3600)
    assert memreport.summarize([base, other_pid])["growth_bytes"] is None
    too_soon = _fake_snapshot(epoch=1_700_000_000.0 + 3600)
    assert memreport.summarize([base, too_soon])["growth_bytes"] is None
    assert memreport.summarize([])["latest"] is None


# ------------------------------------------------------------ diagnostics

def _zip_members(blob):
    zf = zipfile.ZipFile(io.BytesIO(blob))
    return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}


def _build_zip(data_dir):
    from focuscore import diagnostics
    return diagnostics.build_diagnostics_zip(
        db_path=str(data_dir / "missing.db"),
        log_file=str(data_dir / "missing.log"),
        data_dir=str(data_dir), app_root=str(data_dir))


def test_diagnostics_bundle_carries_the_memory_report(tmp_path):
    memreport.record_snapshot(
        tmp_path,
        snapshot=_fake_snapshot(epoch=1_700_000_000.0,
                                rss=80 * 1024 * 1024))
    memreport.record_snapshot(
        tmp_path,
        snapshot=_fake_snapshot(epoch=1_700_000_000.0 + 25 * 3600,
                                rss=85 * 1024 * 1024))
    members = _zip_members(_build_zip(tmp_path))
    assert "memory.txt" in members
    assert "Memory self-report" in members["memory.txt"]
    assert "4242" in members["memory.txt"]
    assert "+5.0 MB" in members["memory.txt"]


def test_diagnostics_memory_section_is_honest_when_empty(tmp_path):
    members = _zip_members(_build_zip(tmp_path))
    assert "(no memory snapshot recorded yet)" in members["memory.txt"]


# ------------------------------------------------------------------- tray

def test_nightly_checks_records_a_memory_snapshot(tmp_path, monkeypatch):
    from focuscore import store, tray
    db = tmp_path / "focuscore.db"
    calls = {"checkpoint": 0, "integrity": 0}

    def _checkpoint(path):
        calls["checkpoint"] += 1

    def _integrity(path):
        calls["integrity"] += 1
        return True, []

    monkeypatch.setattr(store, "checkpoint_wal", _checkpoint)
    monkeypatch.setattr(store, "run_integrity_check", _integrity)
    tray.TrayApp(db_path=str(db))._run_nightly_checks()
    assert calls == {"checkpoint": 1, "integrity": 1}
    snaps = memreport.load_snapshots(tmp_path)
    assert len(snaps) == 1
    assert snaps[0]["pid"] == os.getpid()


def test_nightly_snapshot_failure_never_blocks_the_checks(tmp_path,
                                                         monkeypatch):
    from focuscore import store, tray

    def _boom(data_dir):
        raise RuntimeError("disk is gone")

    monkeypatch.setattr(memreport, "record_snapshot", _boom)
    monkeypatch.setattr(store, "checkpoint_wal", lambda path: None)
    monkeypatch.setattr(store, "run_integrity_check",
                        lambda path: (True, []))
    # Must not raise: the snapshot is observational only.
    tray.TrayApp(db_path=str(tmp_path / "focuscore.db"))._run_nightly_checks()
