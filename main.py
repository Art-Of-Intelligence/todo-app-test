from __future__ import annotations
from typing import Union
from typing import Any
from fastapi import FastAPI
from pydantic import BaseModel,field_validator

from datetime import date, datetime
from enum import Enum
from typing import Dict, List, Optional
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from model import *



# ============================================================
# Very Simple In-Memory Store
# ============================================================

TASKS: Dict[UUID, Task] = {}


# ============================================================
# Helpers
# ============================================================

def _fake_provider_event_id() -> str:
    return f"evt_{uuid4()}"

def _colombo_today() -> date:
    # Use Asia/Colombo for "today" semantics
    return datetime.now(ZoneInfo("Asia/Colombo")).date()

def _get_task_or_404(task_id: UUID) -> Task:
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

def _get_subtask_or_404(task: Task, subtask_id: UUID) -> SubTask:
    for st in task.subtasks:
        if st.id == subtask_id:
            return st
    raise HTTPException(status_code=404, detail="Subtask not found")

def _apply_task_update(task: Task, patch: TaskUpdate) -> Task:
    if patch.title is not None:
        task.title = patch.title
    if patch.note is not None:
        task.note = patch.note
    if patch.due_datetime is not None:
        task.due_datetime = patch.due_datetime
    if patch.completed is not None:
        task.completed = patch.completed

    if "calendar_event" in patch.model_fields_set:
        if patch.calendar_event is None:
            task.calendar_event = None
        elif isinstance(patch.calendar_event, CalendarEventCreate):
            task.calendar_event = CalendarEvent(
                **patch.calendar_event.model_dump(),
                event_id=_fake_provider_event_id(),
            )
        elif isinstance(patch.calendar_event, CalendarEvent):
            task.calendar_event = patch.calendar_event
    return task

def _matches_query(task: Task, q: Optional[str]) -> bool:
    if not q:
        return True
    ql = q.lower()
    return ql in task.title.lower() or (task.note and ql in task.note.lower())

def _in_due_range(task: Task, due_from: Optional[datetime], due_to: Optional[datetime]) -> bool:
    if task.due_datetime is None:
        return True  # If no due date, keep unless caller narrowed by list=today/upcoming
    if due_from and task.due_datetime < due_from:
        return False
    if due_to and task.due_datetime > due_to:
        return False
    return True

def _matches_list_filter(task: Task, list_filter: Optional[TaskListFilter]) -> bool:
    if not list_filter:
        return True
    if list_filter == TaskListFilter.done:
        return task.completed
    if list_filter == TaskListFilter.today:
        if not task.due_datetime:
            return False
        return task.due_datetime.astimezone(ZoneInfo("Asia/Colombo")).date() == _colombo_today()
    if list_filter == TaskListFilter.upcoming:
        if not task.due_datetime:
            return False
        return task.due_datetime.astimezone(ZoneInfo("Asia/Colombo")).date() > _colombo_today()
    return True


# ============================================================
# FastAPI App & Routes
# ============================================================

app = FastAPI(title="Task Manager (Simple, In-Memory)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# ---- Tasks ----

@app.get("/api/v1/tasks", response_model=List[Task])
def list_tasks(
    list_filter: Optional[TaskListFilter] = Query(None, alias="list"),
    q: Optional[str] = Query(None),
    due_from: Optional[datetime] = Query(None),
    due_to: Optional[datetime] = Query(None),
):
    query = TaskListQuery(list=list_filter, q=q, due_from=due_from, due_to=due_to)
    items = []
    for task in TASKS.values():
        if not _matches_query(task, query.q):
            continue
        if not _in_due_range(task, query.due_from, query.due_to):
            continue
        if not _matches_list_filter(task, query.list):
            continue
        items.append(task)
    # Sort: nearest due date first; tasks w/o due_date at end
    items.sort(key=lambda t: (t.due_datetime is None, t.due_datetime or datetime.max))
    return items


@app.post("/api/v1/tasks", response_model=Task, status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate):
    # Convert CalendarEventCreate -> CalendarEvent (simulating provider event creation)
    event = None
    if payload.calendar_event:
        event = CalendarEvent(
            **payload.calendar_event.model_dump(),
            event_id=_fake_provider_event_id(),
        )

    task = Task(
        id=uuid4(),
        title=payload.title,
        note=payload.note,
        due_datetime=payload.due_datetime,
        completed=payload.completed,
        calendar_event=event,
        subtasks=[],
    )
    TASKS[task.id] = task
    return task


@app.get("/api/v1/tasks/{task_id}", response_model=TaskDetail)
def get_task(task_id: UUID):
    return _get_task_or_404(task_id)


@app.patch("/api/v1/tasks/{task_id}", response_model=TaskDetail)
def update_task(task_id: UUID, patch: TaskUpdate):
    task = _get_task_or_404(task_id)
    task = _apply_task_update(task, patch)
    return task


@app.delete("/api/v1/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: UUID):
    _ = _get_task_or_404(task_id)
    del TASKS[task_id]
    return None


@app.post("/api/v1/tasks/{task_id}/complete", response_model=TaskDetail)
def complete_task(task_id: UUID):
    task = _get_task_or_404(task_id)
    task.completed = True
    return task


@app.post("/api/v1/tasks/{task_id}/undo", response_model=TaskDetail)
def undo_task(task_id: UUID):
    task = _get_task_or_404(task_id)
    task.completed = False
    return task


# ---- Subtasks (nested) ----

@app.get("/api/v1/tasks/{task_id}/subtasks", response_model=List[SubTask])
def list_subtasks(task_id: UUID):
    task = _get_task_or_404(task_id)
    # Sort by due then created_at
    return sorted(task.subtasks, key=lambda st: (st.due_datetime is None, st.due_datetime or datetime.max, st.created_at))


@app.post("/api/v1/tasks/{task_id}/subtasks", response_model=SubTask, status_code=status.HTTP_201_CREATED)
def create_subtask(task_id: UUID, payload: SubTaskCreate):
    task = _get_task_or_404(task_id)
    subtask = SubTask(
        id=uuid4(),
        task_id=task.id,
        title=payload.title,
        due_datetime=payload.due_datetime,
        completed=payload.completed,
        add_to_calendar=payload.add_to_calendar,
        event_duration_minutes=payload.event_duration_minutes,
        calendar_id=payload.calendar_id,
        calendar_event_id=payload.calendar_event_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    # Minimal "calendar create" simulation for subtasks if flagged
    if subtask.add_to_calendar and subtask.calendar_event_id is None and subtask.calendar_id:
        subtask.calendar_event_id = _fake_provider_event_id()

    task.subtasks.append(subtask)
    return subtask


@app.patch("/api/v1/tasks/{task_id}/subtasks/{subtask_id}", response_model=SubTask)
def update_subtask(task_id: UUID, subtask_id: UUID, patch: SubTaskUpdate):
    task = _get_task_or_404(task_id)
    subtask = _get_subtask_or_404(task, subtask_id)

    if patch.title is not None:
        subtask.title = patch.title
    if patch.due_datetime is not None:
        subtask.due_datetime = patch.due_datetime
    if patch.completed is not None:
        subtask.completed = patch.completed
    if patch.add_to_calendar is not None:
        subtask.add_to_calendar = patch.add_to_calendar
    if patch.event_duration_minutes is not None:
        subtask.event_duration_minutes = patch.event_duration_minutes
    if patch.calendar_id is not None:
        subtask.calendar_id = patch.calendar_id
    if patch.calendar_event_id is not None:
        subtask.calendar_event_id = patch.calendar_event_id

    # If they toggled add_to_calendar on and provided a calendar_id, assign an event id if missing
    if subtask.add_to_calendar and subtask.calendar_id and not subtask.calendar_event_id:
        subtask.calendar_event_id = _fake_provider_event_id()

    subtask.updated_at = datetime.utcnow()
    return subtask


@app.delete("/api/v1/tasks/{task_id}/subtasks/{subtask_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_subtask(task_id: UUID, subtask_id: UUID):
    task = _get_task_or_404(task_id)
    subtask = _get_subtask_or_404(task, subtask_id)
    task.subtasks = [st for st in task.subtasks if st.id != subtask.id]
    return None


@app.post("/api/v1/tasks/{task_id}/subtasks/{subtask_id}/complete", response_model=SubTask)
def complete_subtask(task_id: UUID, subtask_id: UUID):
    task = _get_task_or_404(task_id)
    subtask = _get_subtask_or_404(task, subtask_id)
    subtask.completed = True
    subtask.updated_at = datetime.utcnow()
    return subtask


@app.post("/api/v1/tasks/{task_id}/subtasks/{subtask_id}/undo", response_model=SubTask)
def undo_subtask(task_id: UUID, subtask_id: UUID):
    task = _get_task_or_404(task_id)
    subtask = _get_subtask_or_404(task, subtask_id)
    subtask.completed = False
    subtask.updated_at = datetime.utcnow()
    return subtask


# Optional: quick ping
@app.get("/")
def root():
    return {"ok": True, "service": "task-manager-simple"}