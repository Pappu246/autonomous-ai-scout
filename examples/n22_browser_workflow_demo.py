from autonomous_agent.browser_connector import ControlledBrowser
from autonomous_agent.browser_workflow import BrowserAction, BrowserWorkflow


def main() -> int:
    def transport(action, request):
        return {
            "action": action,
            "url": request["url"],
            "title": "Example Demo",
            "text": f"simulated {action} result",
            "status_code": 200,
            "verification_status": "verified",
        }

    browser = ControlledBrowser({"example.com"}, transport)
    result = BrowserWorkflow(browser).run((
        BrowserAction("open", "https://example.com"),
        BrowserAction("click", "https://example.com", selector="#continue"),
        BrowserAction("extract", "https://example.com", fields=("title", "text")),
    ))
    print("N22 Browser Interaction capability demo")
    print(f"success: {result.success}")
    print(f"completed_steps: {result.completed_steps}")
    for item in result.results:
        print(f"{item.action}: {item.verification_status} status={item.status_code}")
    return 0 if result.success and result.completed_steps == 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())
