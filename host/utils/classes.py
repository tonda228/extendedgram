from pydantic import BaseModel, Field


class SummaryTopic(BaseModel):
    start_message_id: int = Field(gt=0)
    end_message_id: int = Field(gt=0)
    topic: str
    summary: str
