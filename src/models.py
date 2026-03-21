from pydantic import BaseModel


class FileAttachment(BaseModel):
    filename: str
    content_base64: str
    mime_type: str


class TripletexCredentials(BaseModel):
    base_url: str = "https://kkpqfuj-amager.tripletex.dev/v2"
    session_token: str = ""


class SolveRequest(BaseModel):
    prompt: str
    files: list[FileAttachment] = []
    tripletex_credentials: TripletexCredentials
    task_id: str | None = None  # Optional: used by simulator for log routing


class SolveResponse(BaseModel):
    status: str = "completed"


class PlannedCall(BaseModel):
    step: int
    method: str
    path: str
    purpose: str
    body_sketch: str = ""


class TaskPlan(BaseModel):
    task_summary: str
    planned_calls: list[PlannedCall]
    total_estimated_calls: int
    notes: str = ""
