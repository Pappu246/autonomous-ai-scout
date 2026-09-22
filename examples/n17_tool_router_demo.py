from autonomous_agent.tool_router import DynamicToolRouter


def main() -> int:
    router = DynamicToolRouter()
    requests = (
        "run tests",
        "run lint",
        "read this page: https://example.com",
        "research this topic",
        "draft an email to finance",
        "list files in the workspace",
        "automate browser navigation",
        "automate the deployment",
    )
    print("N17 Dynamic Tool Router capability demo")
    print("=" * 46)
    for request in requests:
        selection = router.select_names(request)
        print(f"request: {request}")
        print(f"intent:  {selection.intent.value}")
        print(f"tools:   {selection.tool_names or 'NONE'}")
        print(f"reason:  {selection.reason}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
