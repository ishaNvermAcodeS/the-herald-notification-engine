from typing import Any, Dict, List, Union, Optional
from pydantic import BaseModel, Field, field_validator


class EventTriggerRequest(BaseModel):
    workflowId: str = Field(
        ...,
        min_length=1,
        description="Identifier of the workflow template",
        examples=["campus-maintenance-alert"],
    )
    to: Union[str, List[str]] = Field(
        ...,
        description="Target subscriber identifier or array of subscriber identifiers (max 100)",
        examples=["student-10492"],
    )
    payload: Dict[str, Any] = Field(
        ...,
        description="Arbitrary JSON key-value data for template compilation",
        examples=[{"title": "Water Outage", "location": "West Dorm", "severity": "medium"}],
    )
    transactionId: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Unique client transaction identifier for idempotency",
        examples=["tx-campus-alert-20261005-01"],
    )

    @field_validator("to")
    @classmethod
    def validate_recipients(cls, v: Union[str, List[str]]) -> Union[str, List[str]]:
        if isinstance(v, str):
            cleaned = v.strip()
            if not cleaned:
                raise ValueError("Recipient 'to' string cannot be empty")
            return cleaned
        elif isinstance(v, list):
            if len(v) == 0:
                raise ValueError("Recipient 'to' list cannot be empty")
            if len(v) > 100:
                raise ValueError("Recipient 'to' list cannot exceed 100 recipients per call")
            cleaned_list = []
            for item in v:
                if not isinstance(item, str) or not item.strip():
                    raise ValueError("Each recipient identifier in 'to' must be a non-empty string")
                cleaned_list.append(item.strip())
            return cleaned_list
        raise ValueError("Recipient 'to' must be a string or array of strings")

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, v: Any) -> Dict[str, Any]:
        if not isinstance(v, dict):
            raise ValueError("Payload must be a JSON object")
        return v


class EventTriggerResponse(BaseModel):
    acknowledged: bool = Field(default=True, description="Receipt acknowledgement status")
    status: str = Field(
        default="processed",
        description="Ingestion status ('processed', 'in-progress', or 'duplicate_ignored')",
        examples=["processed"],
    )
    transactionId: str = Field(..., description="Transaction identifier associated with this event")
    workflowId: str = Field(..., description="Target workflow identifier")


class BulkEventTriggerRequest(BaseModel):
    events: List[EventTriggerRequest] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Array of event triggers to ingest in bulk",
    )


class BulkEventTriggerResponse(BaseModel):
    acknowledged: bool = Field(default=True, description="Receipt acknowledgement status")
    count: int = Field(..., description="Number of events ingested")
    transactionIds: List[str] = Field(..., description="List of generated or provided transaction IDs")
