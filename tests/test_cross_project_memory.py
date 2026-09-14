from pathlib import Path

from autonomous_agent.cross_project_memory import CrossProjectMemory, MemoryEvent


def test_project_task_and_tool_history_is_persistent_and_secret_safe(tmp_path: Path):
    path = tmp_path / "memory.json"
    memory = CrossProjectMemory(path)
    memory.record_task("owner/repo", "inspect with token=super-secret", intent="inspect", outcome="success")
    memory.record_tool_execution("owner/repo", "github.inspect", execution_id="exec-1", outcome="success", attempts=1)
    memory.record_tool_execution("owner/repo", "tests.run", execution_id="exec-2", outcome="failure", attempts=2)

    loaded = CrossProjectMemory(path)
    entries = loaded.learn("owner/repo")
    assert len(entries) == 3
    text = path.read_text(encoding="utf-8")
    assert "super-secret" not in text
    assert "token=super-secret" not in text
    assert loaded.has(project="owner/repo", kind="task", fingerprint=entries[0]["fingerprint"])


def test_nested_secret_keys_and_raw_fingerprints_are_never_persisted(tmp_path: Path):
    path = tmp_path / "memory.json"
    memory = CrossProjectMemory(path)
    memory.record(
        MemoryEvent(
            "owner/repo", "task", "credential=raw-secret", "success",
            {"api_key": "raw-api-secret", "nested": {"authorization": "Bearer raw-token"}, "items": [{"token": "raw-item-secret"}]},
        )
    )
    text = path.read_text(encoding="utf-8")
    for secret in ("raw-secret", "raw-api-secret", "raw-token", "raw-item-secret"):
        assert secret not in text
    assert "[REDACTED]" in text


def test_recommendation_history_prevents_duplicate_recommendations(tmp_path: Path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    recommendation = "add a license"
    assert memory.recommendation_needed("owner/repo", recommendation)
    memory.record_recommendation("owner/repo", recommendation, status="rejected")
    assert not memory.recommendation_needed("owner/repo", recommendation)


def test_finding_deduplication_uses_stable_fingerprint(tmp_path: Path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    assert memory.record_finding("owner/repo", "missing license", severity="medium")
    fingerprint = memory.learn("owner/repo", kind="finding")[0]["fingerprint"]
    assert memory.has(project="owner/repo", kind="finding", fingerprint=fingerprint)
    assert not memory.record_finding("owner/repo", "missing license", severity="medium", status="repeated")
    assert len(memory.learn("owner/repo", kind="finding")) == 1


def test_health_baseline_and_change_detection_are_deterministic(tmp_path: Path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    baseline = {"tests": 1, "license": 1, "reproducible": 1}
    memory.record_health_baseline("owner/repo", baseline)
    assert memory.change_detected("owner/repo", "head", "sha-1")
    memory.record_change("owner/repo", "head", "sha-1")
    assert not memory.change_detected("owner/repo", "head", "sha-1")
    assert memory.change_detected("owner/repo", "head", "sha-2")


def test_provider_and_benchmark_history_are_global_or_project_scoped(tmp_path: Path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    memory.record_provider_availability("free-provider", available=True)
    memory.record_benchmark("owner/repo", "latency", score=0.9, provider="free-provider")
    assert len(memory.learn("_global", kind="provider_availability")) == 1
    assert len(memory.learn("owner/repo", kind="benchmark")) == 1


def test_bounded_storage_and_per_project_limit(tmp_path: Path):
    memory = CrossProjectMemory(tmp_path / "memory.json", max_entries=5, max_entries_per_project=2)
    for index in range(10):
        memory.record(MemoryEvent("owner/repo", "task", str(index), "success", {"index": index}))
    entries, valid = memory._load()
    assert valid
    assert len(memory.learn("owner/repo")) == 2
    assert len(entries) <= 5


def test_learning_returns_only_recorded_evidence(tmp_path: Path):
    memory = CrossProjectMemory(tmp_path / "memory.json")
    memory.record_improvement("owner/repo", "enable tests", status="accepted")
    memory.record_improvement("owner/repo", "remove obsolete check", status="rejected")
    outcomes = {item["outcome"] for item in memory.learn("owner/repo", kind="improvement")}
    assert outcomes == {"accepted", "rejected"}
