from dataclasses import dataclass
from datetime import datetime
from typing import Literal


Rule = Literal["ARRIVAL_MINUS_4", "SHIP_PLUS_2", "SHIP_PLUS_3", "NONE"]
Issue = Literal["NONE", "MISSING_ARRIVAL", "DATE_CONFLICT"]


@dataclass(frozen=True)
class DeadlineResult:
    deadline_at: datetime | None
    rule: Rule
    issue: Issue

