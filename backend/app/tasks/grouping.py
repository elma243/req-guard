"""Group approved requirements into task groups and make one task per requirement (FR-11).

1. A requirement whose module (from validation, app/nlp/validate.py) is still one of the project's modules joins
   that module's group.
2. The others are embedded. One joins an existing cluster group when it is similar enough to that group's tasks;
   the rest are clustered (app/nlp/cluster.py). Clusters of two or more become new groups that the AI names
   (prompt "group_names"); a requirement similar to none goes to "Other requirements".

Only requirements without a task are grouped, so a run never changes an existing task or group (rule 2). The LLM
is optional: without it, or when it fails, a group is named from its most frequent words. Without the embedding
model, unmatched requirements all go to "Other requirements" and the result says why.
"""

from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ai.llm import LLMClient, LLMError, now
from app.models import Project, Requirement, Task, TaskGroup
from app.nlp import similarity
from app.nlp.cluster import cluster, rule_name
from app.nlp.similarity import ModelUnavailable, ensure_embeddings
from app.schemas.task import MAX_GROUP_NAME_CHARS, GeneratedGroupNames, GroupItem

# Average similarity to a cluster group's tasks that lets a new requirement join it
JOIN_SIMILARITY = 0.5
LLM_SECONDS = 60.0
MAX_NAME_WORDS = 5
MAX_STATEMENTS_PER_CLUSTER = 8
RULE_MODEL = "rule-based"
OTHER_NAME = "Other requirements"
MODULE_GROUP_REASON = "A module of the project."
OTHER_GROUP_REASON = "Requirements that match no module and are not similar to other unmatched requirements."


@dataclass
class Grouping:
    created_tasks: list[Task] = field(default_factory=list)
    created_groups: int = 0
    items: list[GroupItem] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


def _load_candidates(db: Session, project: Project) -> list[Requirement]:
    rows = db.scalars(
        select(Requirement)
        .where(Requirement.project_id == project.id)
        .order_by(Requirement.number)
        .options(
            selectinload(Requirement.versions),
            selectinload(Requirement.approvals),
            selectinload(Requirement.task),
        )
    ).all()
    return [r for r in rows if r.approval is not None and r.task is None]


def _load_groups(db: Session, project: Project) -> list[TaskGroup]:
    return list(
        db.scalars(
            select(TaskGroup)
            .where(TaskGroup.project_id == project.id)
            .order_by(TaskGroup.id)
            .options(
                selectinload(TaskGroup.tasks).selectinload(Task.requirement).selectinload(Requirement.versions)
            )
        ).all()
    )


class _Builder:
    def __init__(self, db: Session, project: Project, groups: list[TaskGroup]):
        self.db = db
        self.project = project
        self.groups = groups
        self.result = Grouping()

    def new_group(self, **values) -> TaskGroup:
        group = TaskGroup(project_id=self.project.id, origin="ai", status="suggested", **values)
        self.db.add(group)
        self.groups.append(group)
        self.result.created_groups += 1
        return group

    def unique_name(self, name: str) -> str:
        taken = {g.name.casefold() for g in self.groups}
        if name.casefold() not in taken:
            return name
        number = 2
        while f"{name} ({number})".casefold() in taken:
            number += 1
        return f"{name} ({number})"

    def module_group(self, module: str) -> TaskGroup:
        for group in self.groups:
            if group.kind == "module" and (group.module_name or "").casefold() == module.casefold():
                return group
        return self.new_group(
            name=self.unique_name(module[:MAX_GROUP_NAME_CHARS]),
            kind="module",
            module_name=module,
            reason=MODULE_GROUP_REASON,
        )

    def other_group(self) -> TaskGroup:
        for group in self.groups:
            if group.kind == "unmatched":
                return group
        return self.new_group(name=self.unique_name(OTHER_NAME), kind="unmatched", reason=OTHER_GROUP_REASON)

    def add_task(self, requirement: Requirement, group: TaskGroup, reason: str) -> None:
        self.project.task_counter = (self.project.task_counter or 0) + 1
        task = Task(
            project_id=self.project.id,
            number=self.project.task_counter,
            status="suggested",
            grouping_reason=reason,
            group_origin="ai",
            requirement=requirement,
            requirement_version=requirement.current.version,
            rejected_member_ids=[],
        )
        group.tasks.append(task)
        self.result.created_tasks.append(task)
        self.result.items.append(
            GroupItem(task_code=task.code, requirement_code=requirement.code, group_name=group.name, reason=reason)
        )


def _module_names(project: Project) -> dict[str, str]:
    return {str(m.get("name", "")).casefold(): str(m.get("name", "")) for m in project.modules or [] if m.get("name")}


def _codes(requirements: list[Requirement]) -> str:
    shown = ", ".join(r.code for r in requirements[:4])
    return shown + (f" and {len(requirements) - 4} more" if len(requirements) > 4 else "")


def _clean_name(name: str) -> str:
    name = " ".join(name.replace('"', "").split())
    if not name or len(name.split()) > MAX_NAME_WORDS or len(name) > MAX_GROUP_NAME_CHARS:
        return ""
    return name


def _name_clusters(
    clusters: list[list[Requirement]], llm: LLMClient | None, notices: list[str]
) -> list[tuple[str, str, str]]:
    """(name, reason, ai_model) per cluster, from the AI where it gave a usable name, else from the rule."""
    fallback = [
        (
            rule_name([r.current.statement for r in members]),
            "Similar requirements that match no module; named from their most frequent words.",
            RULE_MODEL,
        )
        for members in clusters
    ]
    if llm is None:
        notices.append("No AI model is configured, so new groups were named from their most frequent words.")
        return fallback
    payload = [
        {"ref": f"G{i + 1}", "statements": [r.current.statement for r in members[:MAX_STATEMENTS_PER_CLUSTER]]}
        for i, members in enumerate(clusters)
    ]
    try:
        answer = llm.complete_json(
            "group_names", {"clusters": payload}, GeneratedGroupNames, deadline=now() + LLM_SECONDS
        )
    except LLMError as exc:
        notices.append(f"The AI could not name the new groups ({exc}), so they were named from their most frequent words.")
        return fallback
    if answer.not_enough_information:
        return fallback
    by_ref = {g.ref: g for g in answer.groups}
    names = []
    for i, default in enumerate(fallback):
        given = by_ref.get(f"G{i + 1}")
        name = _clean_name(given.name) if given else ""
        if not name:
            names.append(default)
            continue
        reason = given.reason or "Similar requirements that match no module."
        names.append((name, reason[:500], llm.last_model or "ai"))
    return names


def _group_unmatched(builder: _Builder, requirements: list[Requirement], llm: LLMClient | None) -> None:
    if not requirements:
        return
    try:
        embedder = similarity.get_embedder()
    except ModelUnavailable as exc:
        builder.result.notices.append(
            f"Similar requirements were not grouped because the embedding model is unavailable: {exc}"
        )
        group = builder.other_group()
        for requirement in requirements:
            builder.add_task(requirement, group, "No module matched; the embedding model was unavailable to find similar requirements.")
        return

    cluster_groups = [g for g in builder.groups if g.kind == "cluster" and g.tasks]
    members = {g.id: [t.requirement for t in g.tasks] for g in cluster_groups}
    versions = [r.current for r in requirements] + [r.current for rs in members.values() for r in rs]
    vectors = ensure_embeddings(builder.db, embedder, versions)

    def vector(r: Requirement) -> np.ndarray | None:
        return vectors.get(r.current.id)

    rest: list[Requirement] = []
    for requirement in requirements:
        own = vector(requirement)
        best: tuple[float, TaskGroup] | None = None
        for group in cluster_groups:
            others = [v for v in (vector(r) for r in members[group.id]) if v is not None]
            if own is None or not others:
                continue
            score = float(np.mean([float(own @ o) for o in others]))
            if score >= JOIN_SIMILARITY and (best is None or score > best[0]):
                best = (score, group)
        if best is None:
            rest.append(requirement)
            continue
        score, group = best
        builder.add_task(requirement, group, f"No module matched; similar to the tasks of this group (similarity {score:.2f}).")

    with_vectors = [r for r in rest if vector(r) is not None]
    other = [r for r in rest if vector(r) is None]
    labels = cluster([vector(r) for r in with_vectors])
    by_label: dict[int, list[Requirement]] = {}
    for requirement, label in zip(with_vectors, labels, strict=True):
        by_label.setdefault(label, []).append(requirement)
    clusters = [rs for rs in by_label.values() if len(rs) >= 2]
    other += [rs[0] for rs in by_label.values() if len(rs) == 1]

    for members_, (name, reason, model) in zip(clusters, _name_clusters(clusters, llm, builder.result.notices), strict=True):
        group = builder.new_group(name=builder.unique_name(name), kind="cluster", reason=reason, ai_model=model)
        for requirement in members_:
            peers = [r for r in members_ if r is not requirement]
            score = float(np.mean([float(vector(requirement) @ vector(p)) for p in peers]))
            builder.add_task(requirement, group, f"No module matched; similar to {_codes(peers)} (similarity {score:.2f}).")

    if other:
        group = builder.other_group()
        for requirement in sorted(other, key=lambda r: r.number):
            builder.add_task(requirement, group, "No module matched and not similar to other unmatched requirements.")


def group_requirements(db: Session, project: Project, llm: LLMClient | None) -> Grouping:
    """Make tasks for the approved requirements that have none. The caller commits."""
    builder = _Builder(db, project, _load_groups(db, project))
    candidates = _load_candidates(db, project)
    if not candidates:
        builder.result.notices.append("Every approved requirement already has a task.")
        return builder.result
    modules = _module_names(project)
    unmatched: list[Requirement] = []
    for requirement in candidates:
        module = modules.get((requirement.module_name or "").casefold())
        if module is None:
            unmatched.append(requirement)
            continue
        score = f" (score {requirement.module_score:.2f})" if requirement.module_score is not None else ""
        builder.add_task(requirement, builder.module_group(module), f"Matches module '{module}'{score}.")
    _group_unmatched(builder, unmatched, llm)
    db.flush()
    return builder.result
