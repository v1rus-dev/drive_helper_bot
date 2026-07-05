"""FSM state groups."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    full_name = State()
    phone = State()


class AddSlots(StatesGroup):
    date = State()
    times = State()


class AssignModerator(StatesGroup):
    tg_id = State()


class RemoveModerator(StatesGroup):
    tg_id = State()


class DaySchedule(StatesGroup):
    date = State()


class AssignTeacher(StatesGroup):
    tg_id = State()


class RemoveTeacher(StatesGroup):
    tg_id = State()


class EditProfile(StatesGroup):
    full_name = State()
    phone = State()
