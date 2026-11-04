from __future__ import annotations

from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, model_validator


class PartialUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    _nullable_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _reject_nulls(self) -> Self:
        nulls = sorted(
            name
            for name in self.model_fields_set
            if name not in self._nullable_fields and getattr(self, name) is None
        )
        if nulls:
            raise ValueError(f"Fields cannot be null: {', '.join(nulls)}")
        return self
