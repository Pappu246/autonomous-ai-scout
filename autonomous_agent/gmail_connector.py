from __future__ import annotations
import base64,hashlib,json,re
from dataclasses import dataclass
from email.message import EmailMessage
from email import policy
from email.parser import BytesParser
from html import unescape
from typing import Any,Mapping,Protocol
from urllib.parse import quote
GMAIL_API_ROOT="https://gmail.googleapis.com/gmail/v1/users/me"
MAX_QUERY_LENGTH=500;MAX_RESULTS=20;MAX_MESSAGE_BYTES=256*1024;MAX_THREAD_MESSAGES=25;MAX_ATTACHMENT_METADATA=20;MAX_RETRIES=2;MAX_TIMEOUT_SECONDS=30;MAX_BODY_CHARS=100_000
_SECRET=re.compile(r"(?i)(?:bearer\s+|api[_-]?key\s*[:=]\s*|access[_-]?token\s*[:=]\s*|refresh[_-]?token\s*[:=]\s*|password\s*[:=]\s*|token\s*[:=]\s*|secret\s*[:=]\s*)[^\s,;]+")
_AUTH=re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+")
READ_SCOPE="https://www.googleapis.com/auth/gmail.readonly";COMPOSE_SCOPE="https://www.googleapis.com/auth/gmail.compose";SEND_SCOPE="https://www.googleapis.com/auth/gmail.send"
class GmailError(ValueError):pass
class GmailTransport(Protocol):
    def request(self,method:str,url:str,*,params:Mapping[str,Any]|None=None,body:Mapping[str,Any]|None=None,timeout_seconds:int=10)->Mapping[str,Any]:...
class CredentialResolver(Protocol):
    def resolve(self,credential_reference:str)->str:...
@dataclass(frozen=True)
class GmailOAuthConfig:
    credential_reference:str;scopes:tuple[str,...]
    def __post_init__(self):
        if not self.credential_reference or any(x in self.credential_reference.lower() for x in ("token","password","secret","key=")):raise GmailError("OAuth configuration must contain a reference, not credential material")
        if not self.scopes:raise GmailError("at least one Gmail OAuth scope is required")
@dataclass(frozen=True)
class GmailEvidence:
    operation:str;data:Mapping[str,Any];fingerprint:str
    def safe_dict(self):return {"operation":self.operation,"data":self.data,"fingerprint":self.fingerprint}
def _redact(value):
    if isinstance(value,Mapping):return {str(k):_redact(v) for k,v in value.items() if str(k).lower() not in {"access_token","refresh_token","authorization","password","client_secret"}}
    if isinstance(value,list):return [_redact(v) for v in value[:MAX_ATTACHMENT_METADATA]]
    if isinstance(value,str):return _SECRET.sub("[REDACTED]",_AUTH.sub(r"\1[REDACTED]",value))[:MAX_BODY_CHARS]
    return value
def _fingerprint(value):return hashlib.sha256(json.dumps(_redact(value),sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def _bounded_text(value,limit=MAX_BODY_CHARS):return str(value or "")[:limit]
def _safe_query(query):
    if not isinstance(query,str):raise GmailError("query must be a string")
    query=" ".join(query.split())
    if len(query)>MAX_QUERY_LENGTH or any(ord(c)<32 and c not in "\t" for c in query):raise GmailError("malformed or oversized Gmail search query")
    return query
def _safe_id(value,label):
    if not isinstance(value,str) or not value.strip() or len(value)>512 or any(c in value for c in "\r\n"):raise GmailError(f"invalid {label}")
    return value.strip()
def _parse_mime(raw:bytes):
    if len(raw)>MAX_MESSAGE_BYTES:raise GmailError("message exceeds the bounded size")
    message=BytesParser(policy=policy.default).parsebytes(raw);attachments=[];texts=[];htmls=[]
    for part in message.walk():
        if part.is_multipart():continue
        filename=part.get_filename()
        if filename:
            attachments.append({"filename":_bounded_text(filename,256),"content_type":part.get_content_type(),"size":len(part.get_payload(decode=True) or b"")})
            if len(attachments)>=MAX_ATTACHMENT_METADATA:break
        else:
            try:content=part.get_content()
            except Exception:content=""
            if part.get_content_type()=="text/plain":texts.append(_bounded_text(content))
            elif part.get_content_type()=="text/html":htmls.append(_bounded_text(re.sub(r"<[^>]+>"," ",unescape(str(content)))))
    return {"subject":_bounded_text(message.get("subject",""),512),"from":_bounded_text(message.get("from",""),512),"to":_bounded_text(message.get("to",""),512),"date":_bounded_text(message.get("date",""),128),"text":_redact("\n".join(texts)),"html_text":_redact("\n".join(htmls)),"attachments":attachments}
def _payload_to_content(payload):
    if not isinstance(payload,Mapping):return {"text":"","html_text":"","attachments":[]}
    texts=[];htmls=[];attachments=[]
    def walk(part):
        if not isinstance(part,Mapping):return
        filename=part.get("filename");body=part.get("body") if isinstance(part.get("body"),Mapping) else {}
        if filename:attachments.append({"filename":_bounded_text(filename,256),"content_type":_bounded_text(part.get("mimeType",""),128),"size":int(body.get("size",0) or 0),"attachmentId":_bounded_text(body.get("attachmentId",""),512)})
        elif body.get("data"):
            try:raw=base64.urlsafe_b64decode(str(body["data"])+"===");text=raw.decode("utf-8",errors="replace")[:MAX_BODY_CHARS]
            except Exception:text=""
            if str(part.get("mimeType","")).lower()=="text/plain":texts.append(text)
            elif str(part.get("mimeType","")).lower()=="text/html":htmls.append(re.sub(r"<[^>]+>"," ",unescape(text)))
        for child in part.get("parts",[]) if isinstance(part.get("parts"),list) else []:walk(child)
    walk(payload);return {"text":_redact("\n".join(texts)),"html_text":_redact("\n".join(htmls)),"attachments":attachments[:MAX_ATTACHMENT_METADATA]}
def _raw_message(to,subject,body,thread_id=None):
    if not isinstance(to,str) or "\n" in to or "\r" in to or len(to)>2048:raise GmailError("invalid recipient")
    if not isinstance(subject,str) or "\n" in subject or "\r" in subject or len(subject)>998:raise GmailError("invalid subject")
    message=EmailMessage();message["To"]=to.strip();message["Subject"]=subject;message.set_content(_bounded_text(body));payload={"raw":base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")}
    if thread_id:payload["threadId"]=_safe_id(thread_id,"thread id")
    return payload
class GmailConnector:
    def __init__(self,transport:GmailTransport,*,credential_reference="gmail:oauth:user",credential_resolver:CredentialResolver|None=None,timeout_seconds=10):
        if not credential_reference or any(x in credential_reference.lower() for x in ("token","password","secret","key=")):raise GmailError("credential reference is invalid")
        self.transport=transport;self.credential_reference=credential_reference;self.credential_resolver=credential_resolver;self.timeout_seconds=max(1,min(int(timeout_seconds),MAX_TIMEOUT_SECONDS));self._send_keys:set[str]=set()
    def _request(self,method,path,*,params=None,body=None,retries=MAX_RETRIES):
        if not path.startswith(GMAIL_API_ROOT+"/"):raise GmailError("request escaped the official Gmail API root")
        last=None
        for _ in range(max(1,min(int(retries)+1,MAX_RETRIES+1))):
            try:
                result=self.transport.request(method,path,params=params,body=body,timeout_seconds=self.timeout_seconds)
                if not isinstance(result,Mapping):raise GmailError("malformed Gmail transport response")
                return _redact(result)
            except Exception as exc:last=exc
        raise GmailError(f"Gmail request failed after bounded retries: {type(last).__name__}")
    def search(self,query,*,results=10):
        query=_safe_query(query);results=max(1,min(int(results),MAX_RESULTS));payload=self._request("GET",f"{GMAIL_API_ROOT}/messages",params={"q":query,"maxResults":results});items=payload.get("messages",[]) if isinstance(payload.get("messages",[]),list) else [];messages=[{"id":_safe_id(str(x.get("id","")),"message id"),"threadId":_safe_id(str(x.get("threadId","")),"thread id")} for x in items[:results] if isinstance(x,Mapping) and x.get("id")];data={"messages":messages,"resultSizeEstimate":int(payload.get("resultSizeEstimate",0) or 0)};return GmailEvidence("email.search",data,_fingerprint(data))
    def read(self,message_id):
        message_id=_safe_id(message_id,"message id");payload=self._request("GET",f"{GMAIL_API_ROOT}/messages/{quote(message_id,safe='')}",params={"format":"full"});headers={str(h.get("name","")).lower():_bounded_text(h.get("value",""),512) for h in payload.get("payload",{}).get("headers",[]) if isinstance(h,Mapping)};data={"id":message_id,"threadId":_bounded_text(payload.get("threadId",""),512),"labelIds":[str(x) for x in payload.get("labelIds",[])[:20]],"snippet":_bounded_text(payload.get("snippet","")),"content":_redact({"subject":headers.get("subject",""),"from":headers.get("from",""),"to":headers.get("to",""),**_payload_to_content(payload.get("payload",{}))})};return GmailEvidence("email.read",data,_fingerprint(data))
    def thread(self,thread_id):
        thread_id=_safe_id(thread_id,"thread id");payload=self._request("GET",f"{GMAIL_API_ROOT}/threads/{quote(thread_id,safe='')}",params={"format":"full"});items=payload.get("messages",[]) if isinstance(payload.get("messages",[]),list) else [];messages=[{"id":_bounded_text(x.get("id",""),512),"threadId":_bounded_text(x.get("threadId",thread_id),512),"internalDate":_bounded_text(x.get("internalDate",""),32),"snippet":_bounded_text(x.get("snippet",""))} for x in items[:MAX_THREAD_MESSAGES] if isinstance(x,Mapping)];messages.sort(key=lambda x:(x["internalDate"],x["id"]));data={"threadId":thread_id,"messages":messages};return GmailEvidence("email.thread",data,_fingerprint(data))
    def draft(self,*,to,subject,body,thread_id=None):
        message=_raw_message(to,subject,body,thread_id);response=self._request("POST",f"{GMAIL_API_ROOT}/drafts",body={"message":message});data={"draft":response,"content_fingerprint":_fingerprint(message)};return GmailEvidence("email.draft",data,_fingerprint(data))
    def send(self,*,to,subject,body,idempotency_key,approved,thread_id=None):
        if not approved:raise GmailError("email.send requires explicit human approval")
        message=_raw_message(to,subject,body,thread_id);digest=_fingerprint(message)
        if idempotency_key!=digest:raise GmailError("idempotency key does not match the message digest")
        if digest in self._send_keys:raise GmailError("duplicate email.send operation blocked by idempotency guard")
        response=self._request("POST",f"{GMAIL_API_ROOT}/messages/send",body=message,retries=0)
        self._send_keys.add(digest)
        data={"recipient":to,"message_fingerprint":digest,"response":response};return GmailEvidence("email.send",data,_fingerprint(data))
def gmail_oauth_scopes(*,include_send=False):return (READ_SCOPE,COMPOSE_SCOPE,SEND_SCOPE) if include_send else (READ_SCOPE,COMPOSE_SCOPE)
