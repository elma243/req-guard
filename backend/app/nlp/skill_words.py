"""Words that link a member's skill to a task's text (app/tasks/assign.py).

Each set holds phrases that mean about the same area of work. A skill matches a task when the skill itself appears
in the task text, or when the skill is in a set and any phrase of that set appears in the text. So a "Backend" skill
matches a task about an "API", and a "UI" skill a task about a "screen". Write phrases in lower case; matching
ignores case, a plural "s" and hyphens. To tune the suggestion, change the sets here.
"""

SKILL_SYNONYMS: list[set[str]] = [
    {"backend", "back end", "server side", "server", "api", "rest api", "endpoint", "web service"},
    {"frontend", "front end", "ui", "user interface", "page", "screen", "form", "button", "dashboard", "layout"},
    {"database", "db", "sql", "postgresql", "postgres", "mysql", "query", "table", "schema", "migration"},
    {"testing", "test", "qa", "quality assurance", "test automation", "automation testing"},
    {"security", "authentication", "auth", "login", "log in", "password", "encryption", "permission",
     "access control", "two factor", "otp"},
    {"payment", "payments", "checkout", "billing", "invoice", "refund", "bkash", "nagad", "stripe"},
    {"mobile", "android", "ios", "mobile app", "react native", "flutter"},
    {"devops", "deployment", "deploy", "docker", "hosting", "ci", "cd", "pipeline", "monitoring"},
    {"design", "ux", "ui ux", "ui design", "figma", "usability", "accessibility"},
    {"reporting", "report", "analytics", "chart", "export", "csv", "excel", "pdf"},
    {"notification", "notifications", "email", "sms", "push notification"},
    {"search", "filter", "elasticsearch"},
    {"performance", "load time", "response time", "caching", "cache", "optimization"},
]  # fmt: skip
