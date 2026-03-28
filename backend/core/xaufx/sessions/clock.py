from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo


@dataclass
class NYSessionClock:
    tz_name: str = "America/New_York"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    def now_ny(self) -> datetime:
        return datetime.now(tz=self.tz)

    def to_ny(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(self.tz)
        return dt.astimezone(self.tz)

    def is_between(self, dt: datetime, start_h: int, start_m: int, end_h: int, end_m: int) -> bool:
        local = self.to_ny(dt)
        current = local.time()
        start = time(start_h, start_m)
        end = time(end_h, end_m)

        if start <= end:
            return start <= current <= end
        return current >= start or current <= end

    def label(self, dt: datetime) -> str:
        if self.is_between(dt, 18, 0, 23, 59) or self.is_between(dt, 0, 0, 2, 59):
            return "ASIA"
        if self.is_between(dt, 3, 0, 6, 59):
            return "LONDON_PREP"
        if self.is_between(dt, 7, 0, 9, 59):
            return "AM_KILLZONE"
        if self.is_between(dt, 10, 0, 11, 59):
            return "LATE_AM"
        return "OFF_HOURS"
