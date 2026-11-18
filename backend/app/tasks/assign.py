"""Suggest an assignee for tasks from skills and workload (AR-06). Only a suggestion: the lead accepts, changes or
rejects it, and only then is the task assigned (CLAUDE.md rule 2).

- Skill match: a skill matches when its words, or a synonym (app/nlp/skill_words.py), appear in the task text, or
  when its embedding is similar enough to the task text. Without the embedding model, words alone are used.
- Workload: the member's open tasks (assigned or in progress), plus the suggestions given to them earlier in the
  same run, so one run spreads the work.
- score = SKILL_WEIGHT * skill + WORKLOAD_WEIGHT / (1 + open). The best member is suggested, the second best kept
  as the alternative. Members the lead rejected for a task are not suggested for it again.
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Project, Task, TeamMember
from app.models.task import OPEN_STATUSES
from app.nlp import similarity
from app.nlp.similarity import ModelUnavailable
from app.nlp.skill_words import SKILL_SYNONYMS

SKILL_WEIGHT = 0.7
WORKLOAD_WEIGHT = 0.3
# Cosine similarity between a skill and the task text that counts as a match
EMBEDDING_MATCH = 0.45
EXTRA_SKILL_BONUS = 0.1
MAX_REASON_SKILLS = 3
NO_MEMBERS = "Add team members (with their skills) to get assignee suggestions."
WORDS_ONLY = "skills matched by words only"

_TOKEN = re.compile(r"[\w#+]+(?:\.[\w#+]+)*", re.UNICODE)


def _singular(token: str) -> str:
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def normalise(text: str) -> str:
    """Lower-case words with a plural "s" removed, joined by single spaces and padded, for phrase search."""
    tokens = _TOKEN.findall(text.lower().replace("-", " ").replace("/", " "))
    return " " + " ".join(_singular(t) for t in tokens) + " "


def has_phrase(normal_text: str, phrase: str) -> bool:
    words = normalise(phrase)
    return words.strip() != "" and words in normal_text


def word_match(skill: str, normal_text: str) -> bool:
    """The skill, or a synonym of it, appears in the (normalised) task text."""
    if has_phrase(normal_text, skill):
        return True
    key = normalise(skill)
    for synonyms in SKILL_SYNONYMS:
        if any(normalise(s) == key for s in synonyms) and any(has_phrase(normal_text, s) for s in synonyms):
            return True
    return False


def task_text(task: Task, project: Project) -> str:
    """What the task is about: the requirement, its actor, the group, and the module's description."""
    requirement = task.requirement
    module = task.group.module_name or requirement.module_name or ""
    description = next(
        (str(m.get("description", "")) for m in project.modules or [] if str(m.get("name", "")).casefold() == module.casefold()),
        "",
    )
    parts = [requirement.current.statement, requirement.current.actor or "", task.group.name, module, description]
    return ". ".join(p for p in parts if p)


@dataclass
class Candidate:
    member: TeamMember
    skills: list[tuple[str, float]]  # matched skills with their similarity (a word match counts as 1.0)
    open_tasks: int
    planned: int
    score: float = 0.0

    @property
    def skill_score(self) -> float:
        if not self.skills:
            return 0.0
        best = max(s for _, s in self.skills)
        return min(1.0, best + EXTRA_SKILL_BONUS * (len(self.skills) - 1))


@dataclass
class SuggestRun:
    suggested: int = 0
    notices: list[str] = field(default_factory=list)


def open_task_counts(db: Session, project: Project) -> Counter[int]:
    rows = db.execute(
        select(Task.assignee_id, func.count())
        .where(Task.project_id == project.id, Task.assignee_id.is_not(None), Task.status.in_(OPEN_STATUSES))
        .group_by(Task.assignee_id)
    ).all()
    return Counter({member_id: count for member_id, count in rows})


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def reason_for(best: Candidate, others: Sequence[Candidate], words_only: bool) -> str:
    workload = _plural(best.open_tasks, "open task")
    if best.planned:
        workload += f" (+{best.planned} suggested in this run)"
    if best.skills:
        names = [name for name, _ in sorted(best.skills, key=lambda s: -s[1])[:MAX_REASON_SKILLS]]
        reason = f"{', '.join(names)} skill{'' if len(names) == 1 else 's'}; {workload}"
    else:
        lowest = all(best.open_tasks + best.planned <= o.open_tasks + o.planned for o in others)
        reason = f"No matching skills; {workload}" + (" (lowest workload)" if lowest else "")
    return f"{reason} ({WORDS_ONLY})" if words_only else reason


class _Embeddings:
    """Embeddings of skills and task texts, made in batches; None when the model is unavailable."""

    def __init__(self) -> None:
        self.reason: str | None = None
        try:
            self.embedder = similarity.get_embedder()
        except ModelUnavailable as exc:
            self.embedder = None
            self.reason = str(exc)
        self.cache: dict[str, np.ndarray] = {}

    def prepare(self, texts: Sequence[str]) -> None:
        if self.embedder is None:
            return
        missing = list(dict.fromkeys(t for t in texts if t not in self.cache))
        if missing:
            for text, vector in zip(missing, self.embedder.encode(missing), strict=True):
                self.cache[text] = np.asarray(vector, dtype=np.float32)

    def similarity(self, a: str, b: str) -> float | None:
        if self.embedder is None or a not in self.cache or b not in self.cache:
            return None
        return float(self.cache[a] @ self.cache[b])


def suggest(db: Session, project: Project, tasks: Sequence[Task]) -> SuggestRun:
    """Write a suggestion on each task that has no assignee. The caller commits."""
    run = SuggestRun()
    work = [t for t in tasks if t.assignee_id is None]
    if not work:
        return run
    members = list(project.members)
    if not members:
        run.notices.append(NO_MEMBERS)
        return run

    texts = {t.id or id(t): task_text(t, project) for t in work}
    embeddings = _Embeddings()
    skills = [s for m in members for s in m.skills or []]
    embeddings.prepare([*skills, *texts.values()])
    words_only = embeddings.embedder is None
    if words_only and any(m.skills for m in members):
        run.notices.append(f"Skills were matched by words only: {embeddings.reason}")

    open_counts = open_task_counts(db, project)
    planned: Counter[int] = Counter()
    for task in work:
        text = texts[task.id or id(task)]
        normal = normalise(text)
        rejected = set(task.rejected_member_ids or [])
        candidates: list[Candidate] = []
        for member in members:
            if member.id in rejected:
                continue
            matched: list[tuple[str, float]] = []
            for skill in member.skills or []:
                if word_match(skill, normal):
                    matched.append((skill, 1.0))
                    continue
                score = embeddings.similarity(skill, text)
                if score is not None and score >= EMBEDDING_MATCH:
                    matched.append((skill, round(score, 2)))
            candidate = Candidate(member, matched, open_counts[member.id], planned[member.id])
            candidate.score = SKILL_WEIGHT * candidate.skill_score + WORKLOAD_WEIGHT / (
                1 + candidate.open_tasks + candidate.planned
            )
            candidates.append(candidate)
        if not candidates:
            task.suggested_assignee_id = None
            task.alternative_assignee_id = None
            task.suggestion_reason = "Every team member was turned down for this task."
            task.suggestion_score = None
            task.suggestion_status = None
            continue
        candidates.sort(key=lambda c: (-c.score, c.open_tasks + c.planned, c.member.id))
        best, alternative = candidates[0], (candidates[1] if len(candidates) > 1 else None)
        task.suggested_assignee_id = best.member.id
        task.alternative_assignee_id = alternative.member.id if alternative else None
        task.suggestion_reason = reason_for(best, candidates[1:], words_only)
        task.suggestion_score = round(best.score, 2)
        task.suggestion_status = "suggested"
        planned[best.member.id] += 1
        run.suggested += 1
    return run
