from autonomous_agent.task_intent import classify_intent
from autonomous_agent.task_plan_models import TaskIntent


def test_classify_natural_language_computer_phrase() -> None:
    assert classify_intent("use the computer to complete this task") is TaskIntent.COMPUTER


def test_classify_control_the_computer_phrase() -> None:
    assert classify_intent("control the computer and open the browser") is TaskIntent.COMPUTER
