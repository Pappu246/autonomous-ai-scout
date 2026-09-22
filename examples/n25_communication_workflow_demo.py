from autonomous_agent.communication_workflow import CommunicationWorkflow


def main() -> int:
    workflow = CommunicationWorkflow()
    plan = workflow.plan_meeting_coordination(
        "coordinate a meeting", draft_email=True, create_event=True
    )
    report = dict(workflow.authorization_report(plan))
    print("N25 Gmail / Calendar communication capability demo")
    for step in plan.steps:
        decision = report[step.tool_name]
        print(f"{step.tool_name}: allowed={decision.allowed} reason={decision.reason}")
    print(f"safe read steps: {plan.safe_read_steps}")
    print(f"send implied: {not workflow.send_is_never_implied(plan)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
