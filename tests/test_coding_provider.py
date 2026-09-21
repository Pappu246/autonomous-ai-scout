from autonomous_agent.ai_coding_brain import RepositoryContext, RepositoryFile
import httpx

from autonomous_agent.coding_provider import (
    ChatProviderConfig,
    OpenAICompatibleCodingModel,
    ProviderRequestError,
)

def test_openai_compatible_provider_parses_candidate(monkeypatch):
    calls=[]
    def post(endpoint,headers,payload,timeout):
        calls.append((endpoint,headers,payload,timeout))
        return {"choices":[{"message":{"content":'{"unified_diff":"diff --git a/app.py b/app.py\\n--- a/app.py\\n+++ b/app.py\\n@@ -1 +1 @@\\n-print(1)\\n+print(2)\\n","file_contents":{"app.py":"print(2)\\n"},"summary":"fix","test_commands":["python -m pytest -q"]}'}}]}
    monkeypatch.setenv("TEST_KEY","secret")
    model=OpenAICompatibleCodingModel(ChatProviderConfig("https://example.test/v1/chat/completions","demo","TEST_KEY"),http_post=post)
    candidate=model.generate_patch(proposal=type("P",(),{"problem":"fix","proposed_solution":"fix","validation_strategy":("python -m pytest -q",),"affected_area":("app.py",)})(),context=RepositoryContext("owner/repo",(RepositoryFile("app.py","print(1)\\n"),)))
    assert candidate is not None and candidate.summary=="fix"
    assert calls[0][1]["Authorization"]=="Bearer secret"
    assert "temperature" not in calls[0][2]
def test_provider_fails_closed_on_malformed_response(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "secret")
    def post(endpoint, headers, payload, timeout):
        return {"choices": [{"message": {"content": "not-json"}}]}
    model = OpenAICompatibleCodingModel(ChatProviderConfig("https://example.test", "demo", "TEST_KEY"), http_post=post)
    candidate = model.generate_patch(proposal=type("P", (), {"problem": "fix", "proposed_solution": "fix", "validation_strategy": (), "affected_area": ()})(), context=RepositoryContext("owner/repo", ()))
    assert candidate is None

def test_provider_accepts_fenced_json(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "secret")
    def post(endpoint, headers, payload, timeout):
        return {"choices": [{"message": {"content": "```json\\n{\"unified_diff\": \"diff\", \"file_contents\": {}, \"summary\": \"fix\", \"test_commands\": []}\\n```"}}]}
    model = OpenAICompatibleCodingModel(ChatProviderConfig("https://example.test", "demo", "TEST_KEY"), http_post=post)
    candidate = model.generate_patch(proposal=type("P", (), {"problem": "fix", "proposed_solution": "fix", "validation_strategy": (), "affected_area": ()})(), context=RepositoryContext("owner/repo", ()))
    assert candidate is not None and candidate.summary == "fix"


def test_provider_can_opt_in_to_temperature(monkeypatch):
    calls = []

    def post(endpoint, headers, payload, timeout):
        calls.append(payload)
        return {
            "choices": [{
                "message": {
                    "content": '{"unified_diff":"diff","file_contents":{},"summary":"fix","test_commands":[]}'
                }
            }]
        }

    monkeypatch.setenv("TEST_KEY", "secret")
    model = OpenAICompatibleCodingModel(
        ChatProviderConfig(
            "https://example.test",
            "demo",
            "TEST_KEY",
            temperature=0.2,
        ),
        http_post=post,
    )
    candidate = model.generate_patch(
        proposal=type(
            "P",
            (),
            {
                "problem": "fix",
                "proposed_solution": "fix",
                "validation_strategy": (),
                "affected_area": (),
            },
        )(),
        context=RepositoryContext("owner/repo", ()),
    )
    assert candidate is not None
    assert calls[0]["temperature"] == 0.2


def test_provider_exposes_safe_http_error(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "secret")

    response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://example.test"),
        text='{"error":{"message":"quota exceeded","token":"secret-value"}}',
    )

    def post(endpoint, headers, payload, timeout):
        raise httpx.HTTPStatusError(
            "request failed",
            request=response.request,
            response=response,
        )

    model = OpenAICompatibleCodingModel(
        ChatProviderConfig("https://example.test", "demo", "TEST_KEY"),
        http_post=post,
    )

    proposal = type(
        "P",
        (),
        {
            "problem": "fix",
            "proposed_solution": "fix",
            "validation_strategy": (),
            "affected_area": (),
        },
    )()

    try:
        model.generate_patch(
            proposal=proposal,
            context=RepositoryContext("owner/repo", ()),
        )
    except ProviderRequestError as exc:
        assert exc.status_code == 429
        assert "quota exceeded" in str(exc)
        assert "secret-value" not in str(exc)
    else:
        raise AssertionError("ProviderRequestError was not raised")


def test_provider_requests_structured_output(monkeypatch):
    calls = []

    def post(endpoint, headers, payload, timeout):
        calls.append(payload)
        return {
            "choices": [{
                "message": {
                    "content": '{"unified_diff":"diff","file_contents":{},"summary":"fix","test_commands":[]}'
                }
            }]
        }

    monkeypatch.setenv("TEST_KEY", "secret")
    model = OpenAICompatibleCodingModel(
        ChatProviderConfig(
            "https://example.test",
            "demo",
            "TEST_KEY",
            structured_output=True,
        ),
        http_post=post,
    )
    candidate = model.generate_patch(
        proposal=type(
            "P",
            (),
            {
                "problem": "fix",
                "proposed_solution": "fix",
                "validation_strategy": (),
                "affected_area": (),
            },
        )(),
        context=RepositoryContext("owner/repo", ()),
    )

    assert candidate is not None
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[0]["response_format"]["json_schema"]["name"] == "patch_candidate"


def test_provider_prompt_separates_prose_validation_from_commands(monkeypatch):
    calls = []

    def post(endpoint, headers, payload, timeout):
        calls.append(payload)
        return {"choices": [{"message": {"content": '{"unified_diff":"diff","file_contents":{},"summary":"fix","test_commands":[]}'}}]}

    monkeypatch.setenv("TEST_KEY", "secret")
    model = OpenAICompatibleCodingModel(
        ChatProviderConfig("https://example.test", "demo", "TEST_KEY"),
        http_post=post,
    )
    candidate = model.generate_patch(
        proposal=type(
            "P", (), {
                "problem": "fix",
                "proposed_solution": "fix",
                "validation_strategy": ("Run the complete existing test suite.",),
                "affected_area": (),
            }
        )(),
        context=RepositoryContext("owner/repo", ()),
    )
    assert candidate is not None
    prompt = calls[0]["messages"][1]["content"]
    assert "Prefer python -m pytest" in prompt
    assert "validation steps" in prompt
