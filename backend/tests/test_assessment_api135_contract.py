"""API-135 response contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.assessment import AssessmentAttemptRemindersResponse


def test_api_135_response_matches_frozen_notification_fields() -> None:
    response = AssessmentAttemptRemindersResponse(
        attempt_id=123,
        notifications=[
            {
                "notification_id": 456,
                "notification_type": "reminder",
                "status": "pending",
            }
        ],
    )

    assert response.model_dump() == {
        "attempt_id": 123,
        "notifications": [
            {
                "notification_id": 456,
                "notification_type": "reminder",
                "status": "pending",
            }
        ],
    }


def test_api_135_rejects_created_at_not_defined_by_tbl45() -> None:
    with pytest.raises(ValidationError):
        AssessmentAttemptRemindersResponse(
            attempt_id=123,
            notifications=[
                {
                    "notification_id": 456,
                    "notification_type": "reminder",
                    "status": "pending",
                    "created_at": "2026-10-01T12:00:00+08:00",
                }
            ],
        )
