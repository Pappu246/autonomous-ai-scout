from autonomous_agent.ai_coding_brain import RepositoryContext, RepositoryFile
from autonomous_agent.coding_provider import ChatProviderConfig, OpenAICompatibleCodingModel

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
