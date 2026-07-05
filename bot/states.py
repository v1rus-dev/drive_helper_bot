"""FSM state groups."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    full_name = State()


class DefaultTimes(StatesGroup):
    # Picking the default preset in the button-based time grid.
    picking = State()


class SlotEditor(StatesGroup):
    # The button-driven weekly editor. The week-view screen itself is stateless
    # (its offset rides in callback data); ``picking`` is active only while the
    # time grid is shown, with mode ("day"/"week"), date and offset in FSM data.
    picking = State()
    # Awaiting confirmation of an override that would cancel one or more bookings.
    # The pending selection + context (mode/date/offset/selected) stay in FSM data.
    confirming = State()


class EditProfile(StatesGroup):
    full_name = State()
