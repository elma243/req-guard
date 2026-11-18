"""Task groups, tasks and assignee suggestions (Step 9: FR-11, AR-06). Owner only (the owner is the team lead).

Grouping makes one task per approved requirement; the AI suggests an assignee with a reason, and only the lead's
action assigns one (rule 2). Tasks can be exported as CSV and pushed to Jira, the latter only when the lead clicks.
"""

from collections import Counter
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import DbSession, OptionalLLM, OwnedProject
from app.connectors.http import ConnectorError, NotConfiguredError
from app.connectors.jira import to_adf
from app.connectors.registry import jira_base_url, jira_issue_creator, missing_env
from app.models import Project, Requirement, Task, TaskGroup, TeamMember
from app.reports.task_export import accepted_test_codes, build_csv
from app.reports.test_case_export import export_filename
from app.schemas.task import (
    AcceptAllResult,
    AssignRequest,
    GroupRename,
    GroupResult,
    JiraInfo,
    JiraPushed,
    JiraPushRequest,
    JiraPushResult,
    JiraSkipped,
    MemberRef,
    MemberWorkload,
    SuggestionOut,
    SuggestResult,
    TaskBoard,
    TaskGroupOut,
    TaskOut,
    TaskRequirement,
    TaskStatus,
    TaskTestCases,
    TaskUpdate,
    WorkloadMember,
)
from app.tasks.assign import suggest
from app.tasks.grouping import group_requirements

router = APIRouter(prefix="/projects/{project_id}", tags=["tasks"])

_REQUIREMENT = (
    selectinload(TaskGroup.tasks).selectinload(Task.requirement).selectinload(Requirement.versions),
    selectinload(TaskGroup.tasks).selectinload(Task.requirement).selectinload(Requirement.approvals),
    selectinload(TaskGroup.tasks).selectinload(Task.requirement).selectinload(Requirement.test_cases),
    selectinload(TaskGroup.tasks).selectinload(Task.requirement).selectinload(Requirement.criteria),
    selectinload(TaskGroup.tasks).selectinload(Task.assignee),
    selectinload(TaskGroup.tasks).selectinload(Task.suggested_assignee),
    selectinload(TaskGroup.tasks).selectinload(Task.alternative_assignee),
)


# ---------- loading and output ----------


def _groups(db: Session, project: Project) -> list[TaskGroup]:
    query = select(TaskGroup).where(TaskGroup.project_id == project.id).options(*_REQUIREMENT).order_by(TaskGroup.id)
    return list(db.scalars(query).all())


def _all_tasks(groups: list[TaskGroup]) -> list[Task]:
    return [t for g in groups for t in g.tasks]


def _get_task(db: Session, project: Project, task_id: int) -> Task:
    task = db.get(Task, task_id)
    if task is None or task.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


def _get_member(db: Session, project: Project, member_id: int) -> TeamMember:
    member = db.get(TeamMember, member_id)
    if member is None or member.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Team member not found")
    return member


def _member(m: TeamMember | None) -> MemberRef | None:
    return MemberRef(id=m.id, name=m.name) if m else None


def _suggestion(t: Task) -> SuggestionOut | None:
    if t.suggestion_status is None:
        return None
    return SuggestionOut(
        member=_member(t.suggested_assignee),
        alternative=_member(t.alternative_assignee),
        reason=t.suggestion_reason or "",
        score=t.suggestion_score,
        status=t.suggestion_status,
    )


def task_out(t: Task, base_url: str | None) -> TaskOut:
    r = t.requirement
    live = r.live_test_cases
    return TaskOut(
        id=t.id,
        code=t.code,
        group_id=t.group_id,
        status=t.status,
        requirement=TaskRequirement(
            id=r.id,
            code=r.code,
            statement=r.current.statement,
            version=r.current.version,
            approved=r.approval is not None,
            module_name=r.module_name,
        ),
        test_cases=TaskTestCases(total=len(live), accepted=len(accepted_test_codes(t)), codes=accepted_test_codes(t)),
        grouping_reason=t.grouping_reason,
        group_origin=t.group_origin,
        assignee=_member(t.assignee),
        suggestion=_suggestion(t),
        needs_review=t.needs_review,
        needs_review_reason=t.needs_review_reason,
        requirement_version=t.requirement_version,
        jira_issue_key=t.jira_issue_key,
        jira_url=f"{base_url}/browse/{t.jira_issue_key}" if base_url and t.jira_issue_key else None,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _workload(project: Project, tasks: list[Task]) -> list[MemberWorkload]:
    counts: Counter[tuple[int, str]] = Counter((t.assignee_id, t.status) for t in tasks if t.assignee_id)
    pending = Counter(t.suggested_assignee_id for t in tasks if t.has_pending_suggestion)
    return [
        MemberWorkload(
            member=WorkloadMember(id=m.id, name=m.name, role=m.role, skills=m.skills or []),
            assigned=counts[(m.id, "assigned")],
            in_progress=counts[(m.id, "in_progress")],
            done=counts[(m.id, "done")],
            pending_suggestions=pending[m.id],
        )
        for m in project.members
    ]


def _jira_info(project: Project) -> JiraInfo:
    missing = missing_env("jira")
    return JiraInfo(
        configured=not missing and bool(project.jira_task_project_key),
        project_key=project.jira_task_project_key,
        issue_type=project.jira_task_issue_type or "Task",
        missing_env=missing,
    )


def _matches(t: Task, group: TaskGroup, member_id: int | None, status_: str | None, module: str | None,
             needs_review: bool | None) -> bool:  # fmt: skip
    if member_id is not None and member_id not in (t.assignee_id, t.suggested_assignee_id if t.has_pending_suggestion else None):
        return False
    if status_ is not None and t.status != status_:
        return False
    if module is not None and (group.module_name or "").casefold() != module.casefold():
        return False
    return needs_review is None or t.needs_review == needs_review


@router.get("/tasks", response_model=TaskBoard)
def list_tasks(
    project: OwnedProject,
    db: DbSession,
    member_id: int | None = None,
    status_: Annotated[TaskStatus | None, Query(alias="status")] = None,
    module: str | None = None,
    needs_review: bool | None = None,
) -> TaskBoard:
    """The board: groups with their tasks (filtered), and every member's workload (not filtered).

    `member_id` matches the assignee or the member of a pending suggestion.
    """
    groups = _groups(db, project)
    tasks = _all_tasks(groups)
    base_url = jira_base_url()
    filtered = any(v is not None for v in (member_id, status_, module, needs_review))
    out: list[TaskGroupOut] = []
    for g in groups:
        shown = [t for t in g.tasks if _matches(t, g, member_id, status_, module, needs_review)]
        if filtered and not shown:
            continue
        out.append(
            TaskGroupOut(
                id=g.id,
                name=g.name,
                kind=g.kind,
                module_name=g.module_name,
                reason=g.reason,
                origin=g.origin,
                status=g.status,
                ai_model=g.ai_model,
                tasks=[task_out(t, base_url) for t in shown],
            )
        )
    modules = sorted({g.module_name for g in groups if g.module_name}, key=str.casefold)
    return TaskBoard(
        groups=out, workload=_workload(project, tasks), modules=modules, total_tasks=len(tasks), jira=_jira_info(project)
    )


# ---------- grouping and suggestions ----------


@router.post("/tasks/group", response_model=GroupResult)
def group(project: OwnedProject, db: DbSession, llm: OptionalLLM) -> GroupResult:
    """Make tasks for approved requirements that have none, then suggest assignees for them."""
    result = group_requirements(db, project, llm)
    run = suggest(db, project, result.created_tasks)
    db.commit()
    return GroupResult(
        created_tasks=len(result.created_tasks),
        created_groups=result.created_groups,
        suggested=run.suggested,
        items=result.items,
        notices=result.notices + run.notices,
    )


@router.post("/tasks/suggest", response_model=SuggestResult)
def suggest_all(project: OwnedProject, db: DbSession) -> SuggestResult:
    """Suggest for unassigned tasks that have no pending suggestion (new, rejected or never suggested)."""
    tasks = [t for t in _all_tasks(_groups(db, project)) if t.assignee_id is None and not t.has_pending_suggestion]
    run = suggest(db, project, tasks)
    db.commit()
    if not tasks:
        run.notices.append("Every unassigned task already has a suggestion.")
    return SuggestResult(suggested=run.suggested, notices=run.notices)


@router.post("/tasks/accept-suggestions", response_model=AcceptAllResult)
def accept_all(project: OwnedProject, db: DbSession) -> AcceptAllResult:
    """Accept every pending suggestion, except on tasks marked "needs review": those need a look first."""
    accepted = skipped = 0
    for task in _all_tasks(_groups(db, project)):
        if not task.has_pending_suggestion:
            continue
        if task.needs_review:
            skipped += 1
            continue
        _accept(task)
        accepted += 1
    db.commit()
    return AcceptAllResult(accepted=accepted, skipped_needs_review=skipped)


def _accept(task: Task) -> None:
    task.assignee_id = task.suggested_assignee_id
    task.status = "assigned"
    task.suggestion_status = "accepted"


def _one(db: Session, project: Project, task_id: int) -> TaskOut:
    db.expire_all()
    task = db.scalar(
        select(Task)
        .where(Task.id == task_id)
        .options(
            selectinload(Task.requirement).selectinload(Requirement.versions),
            selectinload(Task.requirement).selectinload(Requirement.approvals),
            selectinload(Task.requirement).selectinload(Requirement.test_cases),
        )
    )
    return task_out(task, jira_base_url())


@router.post("/tasks/{task_id}/suggestion/accept", response_model=TaskOut)
def accept_suggestion(task_id: int, project: OwnedProject, db: DbSession) -> TaskOut:
    task = _get_task(db, project, task_id)
    if not task.has_pending_suggestion:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"{task.code} has no suggestion to accept")
    _accept(task)
    db.commit()
    return _one(db, project, task_id)


@router.post("/tasks/{task_id}/assign", response_model=TaskOut)
def assign(task_id: int, body: AssignRequest, project: OwnedProject, db: DbSession) -> TaskOut:
    """The lead picks the member (a change of the suggestion, or a new assignee)."""
    task = _get_task(db, project, task_id)
    member = _get_member(db, project, body.member_id)
    if task.suggestion_status in ("suggested", "accepted"):
        task.suggestion_status = "accepted" if member.id == task.suggested_assignee_id else "changed"
    task.assignee_id = member.id
    if task.status == "suggested":
        task.status = "assigned"
    db.commit()
    return _one(db, project, task_id)


@router.post("/tasks/{task_id}/suggestion/reject", response_model=TaskOut)
def reject_suggestion(task_id: int, project: OwnedProject, db: DbSession) -> TaskOut:
    """The suggested member is not suggested for this task again."""
    task = _get_task(db, project, task_id)
    if not task.has_pending_suggestion:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"{task.code} has no suggestion to reject")
    task.rejected_member_ids = sorted({*(task.rejected_member_ids or []), task.suggested_assignee_id})
    task.suggestion_status = "rejected"
    db.commit()
    return _one(db, project, task_id)


@router.post("/tasks/{task_id}/suggest", response_model=TaskOut)
def suggest_again(task_id: int, project: OwnedProject, db: DbSession) -> TaskOut:
    task = _get_task(db, project, task_id)
    if task.assignee_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"{task.code} is already assigned")
    run = suggest(db, project, [task])
    if run.notices and not run.suggested:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=run.notices[0])
    db.commit()
    return _one(db, project, task_id)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: int, body: TaskUpdate, project: OwnedProject, db: DbSession) -> TaskOut:
    """Change the status (assigned → in progress → done; "suggested" unassigns) or move the task to another group."""
    task = _get_task(db, project, task_id)
    if body.status is not None and body.status != task.status:
        if body.status == "suggested":
            task.assignee_id = None
            if task.suggestion_status in ("accepted", "changed"):
                task.suggestion_status = "suggested" if task.suggested_assignee_id else None
        elif task.assignee_id is None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Assign {task.code} to someone first")
        task.status = body.status
    if body.group_id is not None and body.group_id != task.group_id:
        target = db.get(TaskGroup, body.group_id)
        if target is None or target.project_id != project.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Task group not found")
        old = task.group
        task.group = target
        task.group_origin = "user"
        task.grouping_reason = f"Moved here by the team lead (from '{old.name}')."
        db.flush()
        db.refresh(old)
        if not old.tasks and old.origin == "ai" and old.status == "suggested":
            db.delete(old)
    db.commit()
    return _one(db, project, task_id)


@router.post("/tasks/{task_id}/reviewed", response_model=TaskOut)
def mark_reviewed(task_id: int, project: OwnedProject, db: DbSession) -> TaskOut:
    task = _get_task(db, project, task_id)
    task.needs_review = False
    task.needs_review_reason = None
    task.requirement_version = task.requirement.current.version
    db.commit()
    return _one(db, project, task_id)


@router.patch("/task-groups/{group_id}", response_model=TaskGroupOut)
def rename_group(group_id: int, body: GroupRename, project: OwnedProject, db: DbSession) -> TaskGroupOut:
    group_ = db.get(TaskGroup, group_id)
    if group_ is None or group_.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Task group not found")
    if body.name != group_.name:
        group_.name = body.name
        group_.origin = "user"
        group_.status = "edited"
    db.commit()
    group_ = next(g for g in _groups(db, project) if g.id == group_id)
    base_url = jira_base_url()
    return TaskGroupOut(
        id=group_.id,
        name=group_.name,
        kind=group_.kind,
        module_name=group_.module_name,
        reason=group_.reason,
        origin=group_.origin,
        status=group_.status,
        ai_model=group_.ai_model,
        tasks=[task_out(t, base_url) for t in group_.tasks],
    )


# ---------- export and Jira ----------


@router.get("/tasks/export.csv")
def export_csv(project: OwnedProject, db: DbSession) -> Response:
    at = datetime.now(UTC)
    content = build_csv(project.name, at, _groups(db, project))
    filename = export_filename(project.name, at, "csv", kind="tasks")
    return Response(
        content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _issue_description(task: Task) -> dict:
    r = task.requirement
    criteria = [f"{c.code}: {c.current.text}" for c in r.criteria if c.status in ("accepted", "edited")]
    tests = [
        f"{t.code}: {t.current.title} ({t.current.type})" for t in r.test_cases if t.status in ("accepted", "edited")
    ]
    group_ = task.group
    return to_adf(
        [
            ("Requirement", [f"{r.code} (version {r.current.version}): {r.current.statement}"]),
            ("Group", [group_.name + (f" (module {group_.module_name})" if group_.module_name else "")]),
            ("Assigned in ReqGuard to", [task.assignee.name if task.assignee else ""]),
            ("Acceptance criteria", criteria or ["None accepted yet."]),
            ("Test cases", tests or ["None accepted yet."]),
            ("", [f"Created by ReqGuard from task {task.code}."]),
        ]
    )


@router.post("/tasks/jira-push", response_model=JiraPushResult)
def push_to_jira(body: JiraPushRequest, project: OwnedProject, db: DbSession) -> JiraPushResult:
    """Create a Jira issue for each chosen task that is assigned and not pushed yet. Only called on the user's click.

    Each issue key is saved as soon as Jira returns it, so an error part-way keeps the earlier ones.
    """
    if not project.jira_task_project_key:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Set the Jira project for tasks on the project page first.")
    try:
        creator = jira_issue_creator()
    except NotConfiguredError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None

    tasks = {t.id: t for t in _all_tasks(_groups(db, project))}
    pushed: list[JiraPushed] = []
    skipped: list[JiraSkipped] = []
    error: str | None = None
    with creator:
        for task_id in dict.fromkeys(body.task_ids):
            task = tasks.get(task_id)
            if task is None:
                skipped.append(JiraSkipped(task_id=task_id, code="", reason="Task not found"))
                continue
            if task.jira_issue_key:
                skipped.append(JiraSkipped(task_id=task.id, code=task.code, reason=f"Already in Jira as {task.jira_issue_key}"))
                continue
            if task.assignee_id is None:
                skipped.append(JiraSkipped(task_id=task.id, code=task.code, reason="Not assigned yet"))
                continue
            if error is not None:
                skipped.append(JiraSkipped(task_id=task.id, code=task.code, reason="Not sent because of the error"))
                continue
            try:
                key = creator.create(
                    project_key=project.jira_task_project_key,
                    issue_type=project.jira_task_issue_type or "Task",
                    summary=f"{task.requirement.code}: {task.requirement.current.statement}",
                    description=_issue_description(task),
                )
            except ConnectorError as exc:
                error = str(exc)
                skipped.append(JiraSkipped(task_id=task.id, code=task.code, reason="Not sent because of the error"))
                continue
            task.jira_issue_key = key
            task.jira_pushed_at = datetime.now(UTC)
            db.commit()
            pushed.append(JiraPushed(task_id=task.id, code=task.code, key=key))
    return JiraPushResult(pushed=pushed, skipped=skipped, error=error)
