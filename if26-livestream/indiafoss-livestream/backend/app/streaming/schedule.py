"""Which scheduled session each room plays, and when it moves on.

A room (Audi 1, Devroom 2, ...) plays a sequence of YouTube broadcasts. Rules (approved):
1. A room points at its next session in advance (Streamlink waits until the broadcast goes live).
2. It stays on a live session, even past the next session's scheduled start.
3. It moves on once YouTube reports the broadcast ended (a recording, not live), seen twice at
   least ENDED_CONFIRM_SECONDS apart so a hiccup isn't mistaken for the end.
4. It moves on if the session never went live and the next session's start time has arrived.
5. The operator can switch manually at any time (handled by the API, not here).
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
from typing import Sequence

ENDED_CONFIRM_SECONDS = 120.0


@dataclass(frozen=True)
class ScheduledSession:
    slug: str
    starts_at: float  # epoch seconds


@dataclass
class RoomTracker:
    """Runtime observations about a room's current session."""

    session: str | None = None
    went_live: bool = False
    first_ended_at: float | None = None
    last_ended_at: float | None = None

    def reset(self, session: str | None) -> None:
        self.session = session
        self.went_live = False
        self.first_ended_at = None
        self.last_ended_at = None

    @property
    def ended_confirmed(self) -> bool:
        return (
            self.first_ended_at is not None
            and self.last_ended_at is not None
            and self.last_ended_at - self.first_ended_at >= ENDED_CONFIRM_SECONDS
        )


def parse_start(value: str) -> float:
    return dt.datetime.fromisoformat(value).timestamp()


def next_session(sessions: Sequence[ScheduledSession], current: str | None) -> ScheduledSession | None:
    slugs = [s.slug for s in sessions]
    if current not in slugs:
        return sessions[0] if sessions else None
    index = slugs.index(current)
    return sessions[index + 1] if index + 1 < len(sessions) else None


def initial_session(sessions: Sequence[ScheduledSession], now: float) -> str | None:
    """Session to play when a room has none yet: the latest one that has started, else the first.
    If that one already ended, rule 3 moves the room on after a couple of minutes."""
    if not sessions:
        return None
    started = [s for s in sessions if s.starts_at <= now]
    return (started[-1] if started else sessions[0]).slug


def record_playability(tracker: RoomTracker, now: float, status: str | None, is_live: bool) -> None:
    """Feed a YouTube playability result for the room's current session into its tracker."""
    if is_live:
        tracker.went_live = True
        tracker.first_ended_at = tracker.last_ended_at = None
    elif status == "OK":
        # Playable but not live: the broadcast has ended and is now a recording
        if tracker.first_ended_at is None:
            tracker.first_ended_at = now
        tracker.last_ended_at = now
    else:
        # Not started / blocked / unknown: says nothing about the broadcast having ended
        tracker.first_ended_at = tracker.last_ended_at = None


def decide_advance(
    sessions: Sequence[ScheduledSession],
    current: str | None,
    tracker: RoomTracker,
    now: float,
    restream_live: bool,
) -> tuple[str, str] | None:
    """(next session slug, reason) if the room should move on now, else None."""
    upcoming = next_session(sessions, current)
    if upcoming is None or current is None:
        return None
    if restream_live:
        return None  # rule 2: never cut a live session
    if tracker.ended_confirmed:
        return upcoming.slug, "Previous broadcast ended"  # rule 3
    if not tracker.went_live and now >= upcoming.starts_at:
        return upcoming.slug, "Previous session never went live; next session's start time arrived"  # rule 4
    return None
