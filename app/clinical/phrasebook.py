"""
Shared phone-only phrasing used across pathways.

Goal: prevent "clinic visit" language from creeping into summaries/plans.
"""

PHONE_ONLY = {
    "care_setting": (
        "Because this is a phone-only visit, I can’t examine you or run tests."
    ),
    "no_scheduling": (
        "I can’t schedule appointments, but I can tell you what level of care to seek."
    ),
    "escalation_general": (
        "If any urgent warning signs happen, don’t wait—go to urgent care or the ER now."
    ),
    "followup_general": (
        "If you’re not improving within the next 24–48 hours, you should get an in-person exam."
    ),
}
