from autonomous_agent.autonomous_goal import (
    AutonomousGoalRunner,
    GoalPlan,
    GoalState,
    GoalStep,
    StepObservation,
)
from autonomous_agent.capability_policy import Capability
from autonomous_agent.task_orchestrator import TaskOrchestrator


class Planner:
    def __init__(self):
        self.calls = []

    def plan(self, task, observations=()):
        self.calls.append(tuple(observations))
        return GoalPlan(
            task.task,
            (
                GoalStep("one", "inspect", "github.inspect", Capability.INSPECT),
                GoalStep("two", "test", "tests.run", Capability.TEST),
            ),
            "plan",
        )


class Executor:
    def __init__(self):
        self.calls = []
        self.fail_once = False

    def execute(self, step):
        self.calls.append(step.step_id)
        if self.fail_once and len(self.calls) == 2:
            return StepObservation(False, "test failed", "failed")
        return StepObservation(True, "ok", "verified")


class Verifier:
    def verify(self, task, step, observation):
        return observation.success and observation.verification == "verified"


def _runner(planner, executor):
    return AutonomousGoalRunner(
        orchestrator=TaskOrchestrator(),
        planner=planner,
        executor=executor,
        verifier=Verifier(),
    )


def test_goal_runs_multiple_steps_and_verifies_each():
    planner, executor = Planner(), Executor()
    result = _runner(planner, executor).run(
        "inspect and test the project",
        granted=(Capability.INSPECT, Capability.TEST),
    )
    assert result.state is GoalState.COMPLETED
    assert executor.calls == ["one", "two"]
    assert result.attempts == 2


def test_goal_retries_a_failed_step_and_completes():
    planner, executor = Planner(), Executor()
    executor.fail_once = True
    result = _runner(planner, executor).run(
        "inspect and test the project",
        granted=(Capability.INSPECT, Capability.TEST),
    )
    assert result.state is GoalState.COMPLETED
    assert result.attempts == 3
    assert len(planner.calls) == 1


def test_goal_adapts_after_exhausted_retries():
    class AdaptivePlanner(Planner):
        def plan(self, task, observations=()):
            self.calls.append(tuple(observations))
            if observations:
                return GoalPlan(
                    task.task,
                    (GoalStep("recovery", "recover", "github.inspect", Capability.INSPECT),),
                    "recovery",
                )
            return super().plan(task, observations)

    class AlwaysFailOnce(Executor):
        def execute(self, step):
            self.calls.append(step.step_id)
            if step.step_id == "one":
                return StepObservation(False, "broken", "failed")
            return StepObservation(True, "ok", "verified")

    planner, executor = AdaptivePlanner(), AlwaysFailOnce()
    result = _runner(planner, executor).run(
        "inspect and test the project",
        granted=(Capability.INSPECT, Capability.TEST),
    )
    assert result.state is GoalState.COMPLETED
    assert result.attempts == 4
    assert planner.calls[1]


def test_goal_stops_at_approval_boundary():
    class ApprovalPlanner(Planner):
        def plan(self, task, observations=()):
            return GoalPlan(
                task.task,
                (GoalStep("send", "send email", "email.send", Capability.EMAIL, True),),
                "approval",
            )

    result = _runner(ApprovalPlanner(), Executor()).run(
        "send the email",
        granted=(Capability.EMAIL,),
    )
    assert result.state is GoalState.BLOCKED or result.state is GoalState.WAITING_APPROVAL
    assert result.attempts == 0
