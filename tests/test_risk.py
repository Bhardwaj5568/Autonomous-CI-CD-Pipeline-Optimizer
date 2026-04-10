from app.risk import calculate_risk


def test_calculate_risk_returns_block_for_high_failure_pattern():
    events = [
        {"run_id": "r1", "status": "failed", "retry_count": 2, "duration_ms": 600000},
        {"run_id": "r1", "status": "failed", "retry_count": 1, "duration_ms": 500000},
        {"run_id": "r1", "status": "completed", "retry_count": 0, "duration_ms": 450000},
    ]

    result = calculate_risk(events)

    assert result.run_id == "r1"
    assert result.risk_score >= 70
    assert result.recommendation in {"delay", "block"}


def test_calculate_risk_returns_deploy_for_clean_pattern():
    events = [
        {"run_id": "r2", "status": "completed", "retry_count": 0, "duration_ms": 60000},
        {"run_id": "r2", "status": "completed", "retry_count": 0, "duration_ms": 45000},
    ]

    result = calculate_risk(events)

    assert result.run_id == "r2"
    assert result.risk_score < 50
    assert result.recommendation == "deploy"
