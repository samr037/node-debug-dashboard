"""Parsing of the etcd /v3/maintenance/status response."""

import json

from app.collectors.kubernetes import _parse_etcd_status


def test_raft_index_is_read_from_top_level(fixture_text):
    """raftIndex sits beside dbSize, not inside header.

    The original code read header["raft_index"], which does not exist on any
    etcd release — the header carries cluster_id, member_id, revision and
    raft_term only. The field silently reported 0 forever.
    """
    status = _parse_etcd_status(json.loads(fixture_text("etcd-status.json")))
    assert status.raft_index == 103678272
    assert status.raft_applied_index == 103678272
    assert status.raft_term == 2891


def test_int64_fields_arrive_as_strings(fixture_text):
    """etcd serialises int64 as JSON strings; sizes must still be numeric."""
    status = _parse_etcd_status(json.loads(fixture_text("etcd-status.json")))
    assert status.db_size_mb == 1066.8
    assert status.db_size_in_use_mb == 25.0
    assert status.db_size_quota_mb == 2048.0
    assert status.version == "3.6.8"


def test_leader_detection(fixture_text):
    """member_id != leader means this node is a follower."""
    data = json.loads(fixture_text("etcd-status.json"))
    assert _parse_etcd_status(data).is_leader is False

    data["leader"] = data["header"]["member_id"]
    assert _parse_etcd_status(data).is_leader is True


def test_blank_ids_do_not_read_as_leader():
    """An empty response must not make every node look like the leader."""
    status = _parse_etcd_status({})
    assert status.is_leader is False


def test_snake_case_response_is_accepted():
    """Older gateway builds emit snake_case for the same fields."""
    status = _parse_etcd_status(
        {
            "header": {"member_id": "42"},
            "db_size": "2097152",
            "db_size_in_use": "1048576",
            "raft_index": "99",
        }
    )
    assert status.raft_index == 99
    assert status.db_size_mb == 2.0


def test_defrag_flagged_when_free_list_dominates(fixture_text):
    """Space held but unused is only reclaimable by a defrag — surface it."""
    status = _parse_etcd_status(json.loads(fixture_text("etcd-status.json")))
    assert status.defrag_reclaimable_mb == 1041.8
    assert status.quota_used_percent == 52.1
    assert status.defrag_severity == "warning"


def test_healthy_db_is_not_flagged():
    status = _parse_etcd_status(
        {"dbSize": "10485760", "dbSizeInUse": "9437184", "dbSizeQuota": "2147483648"}
    )
    assert status.defrag_severity == "ok"


def test_near_quota_is_critical():
    """Hitting the quota puts etcd into a read-only alarm; warn before that."""
    status = _parse_etcd_status(
        {"dbSize": "1932735283", "dbSizeInUse": "1000", "dbSizeQuota": "2147483648"}
    )
    assert status.defrag_severity == "critical"
