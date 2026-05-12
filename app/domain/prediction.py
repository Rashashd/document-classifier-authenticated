from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class DocumentLabel(str, Enum):
    """RVL-CDIP 16-class taxonomy."""
    letter                 = "letter"
    memo                   = "memo"
    email                  = "email"
    filefolder             = "filefolder"
    form                   = "form"
    handwritten            = "handwritten"
    invoice                = "invoice"
    advertisement          = "advertisement"
    budget                 = "budget"
    news_article           = "news_article"
    presentation           = "presentation"
    scientific_publication = "scientific_publication"
    scientific_report      = "scientific_report"
    specification          = "specification"
    resume                 = "resume"
    questionnaire          = "questionnaire"


class PredictionBase(BaseModel):
    filename:     str
    label:        DocumentLabel
    confidence:   float = Field(ge=0.0, le=1.0)
    overlay_path: str | None = Field(
        default=None,
        description="MinIO blob path for the PNG overlay, populated after inference."
    )


class PredictionCreate(PredictionBase):
    batch_id: uuid.UUID


class PredictionRead(PredictionBase):
    model_config = ConfigDict(from_attributes=True)

    id:         uuid.UUID
    batch_id:   uuid.UUID
    created_at: datetime


class PredictionUpdate(BaseModel):
    """Used for analyst relabelling."""
    label:        DocumentLabel | None = None
    overlay_path: str | None          = None
