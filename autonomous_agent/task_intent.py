from __future__ import annotations
from .task_plan_models import TaskIntent
def classify_intent(task:str)->TaskIntent:
    text=" ".join(task.strip().split()).lower()
    if not text:return TaskIntent.UNKNOWN
    if any(term in text for term in ("email","gmail","mailbox","message thread","email thread","send email","draft email")):return TaskIntent.EMAIL
    if any(term in text for term in ("calendar","calendars","event","events","meeting","meetings","appointment","schedule a meeting","free time","available time")):return TaskIntent.CALENDAR
    if any(term in text for term in ("research","search web","look up","find information","investigate")):return TaskIntent.RESEARCH
    if any(term in text for term in ("file","files","workspace","directory","folder","read file","write file","transform file")):return TaskIntent.WORKSPACE
    if any(term in text for term in ("test","pytest","run tests","validate")):return TaskIntent.TEST
    if any(term in text for term in ("inspect","analyze","analyse","audit","review")):return TaskIntent.INSPECT
    if any(term in text for term in ("automate","automation","schedule","browser")):return TaskIntent.AUTOMATE
    if any(term in text for term in ("fix","change","edit","modify","update code")):return TaskIntent.CHANGE
    if any(term in text for term in ("improve","implement","add feature","build","refactor")):return TaskIntent.IMPROVE
    return TaskIntent.UNKNOWN
