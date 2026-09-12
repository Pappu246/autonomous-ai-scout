from autonomous_agent.rest_connector import RestConnector, RestResponse
from autonomous_agent.rest_tooling import execute_rest_tool

class T:
    def request(self, method, url, *, headers, body, timeout):
        return RestResponse(200, {"Content-Type":"application/json"}, b'{"ok":true}', url)

def test_rest_tool_adapter():
    connector = RestConnector({"api.example.com"}, transport=T())
    result = execute_rest_tool(connector, {"method":"GET","url":"https://api.example.com/v1","headers":{}})
    assert result["status_code"] == 200
    assert result["json"] == {"ok": True}
