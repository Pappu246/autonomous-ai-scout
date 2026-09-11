from __future__ import annotations
import hashlib,json,re
from dataclasses import dataclass
from datetime import datetime,timezone
from typing import Any,Mapping,Protocol
from urllib.parse import quote
CALENDAR_API_ROOT="https://www.googleapis.com/calendar/v3/calendars"
MAX_QUERY=500;MAX_RESULTS=50;MAX_EVENTS=100;MAX_EVENT_BYTES=128*1024;MAX_ATTENDEES=50;MAX_RETRIES=2;MAX_TIMEOUT=30;MAX_RECURRENCE=20
class CalendarError(ValueError):pass
class CalendarTransport(Protocol):
    def request(self,method:str,url:str,*,params:Mapping[str,Any]|None=None,body:Mapping[str,Any]|None=None,headers:Mapping[str,str]|None=None,timeout_seconds:int=10)->Mapping[str,Any]:...
@dataclass(frozen=True)
class CalendarEvidence:
    operation:str;data:Mapping[str,Any];fingerprint:str
    def safe_dict(self):return {"operation":self.operation,"data":self.data,"fingerprint":self.fingerprint}
def _redact(v):
    if isinstance(v,Mapping):return {str(k):_redact(x) for k,x in v.items() if str(k).lower() not in {"access_token","refresh_token","authorization","password","client_secret","cookie"}}
    if isinstance(v,list):return [_redact(x) for x in v[:MAX_ATTENDEES]]
    if isinstance(v,str):return re.sub(r"(?i)(bearer\s+|api[_-]?key\s*[:=]\s*|access[_-]?token\s*[:=]\s*|refresh[_-]?token\s*[:=]\s*|password\s*[:=]\s*|secret\s*[:=]\s*)[^\s,;]+","[REDACTED]",v)[:MAX_EVENT_BYTES]
    return v
def _fp(v):return hashlib.sha256(json.dumps(_redact(v),sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def _id(v,label):
    if not isinstance(v,str) or not v.strip() or len(v)>512 or any(c in v for c in "\r\n"):raise CalendarError(f"invalid {label}")
    return v.strip()
def _iso(v,label):
    if not isinstance(v,str):raise CalendarError(f"invalid {label}")
    try:dt=datetime.fromisoformat(v.replace("Z","+00:00"))
    except ValueError as e:raise CalendarError(f"invalid {label}") from e
    if dt.tzinfo is None:raise CalendarError(f"{label} must include timezone")
    return dt.astimezone(timezone.utc).isoformat()
class CalendarConnector:
    def __init__(self,transport:CalendarTransport,*,credential_reference="calendar:oauth:user",timeout_seconds=10):
        if not credential_reference or any(x in credential_reference.lower() for x in ("token","password","secret","key=")):raise CalendarError("credential reference must not contain credential material")
        self.transport=transport;self.credential_reference=credential_reference;self.timeout_seconds=max(1,min(int(timeout_seconds),MAX_TIMEOUT));self._mutations=set()
    def _request(self,method,url,*,params=None,body=None,headers=None,retries=MAX_RETRIES):
        if not url.startswith(CALENDAR_API_ROOT+"/"):raise CalendarError("request escaped official Calendar API root")
        last=None
        for _ in range(max(1,min(int(retries)+1,MAX_RETRIES+1))):
            try:
                out=self.transport.request(method,url,params=params,body=_redact(body) if body else None,headers=dict(headers or {}),timeout_seconds=self.timeout_seconds)
                if not isinstance(out,Mapping):raise CalendarError("malformed calendar response")
                return _redact(out)
            except Exception as exc:last=exc
        raise CalendarError(f"calendar request failed after bounded retries: {type(last).__name__}")
    def list(self,calendar_id="primary",*,time_min=None,time_max=None,query="",results=20):
        calendar_id=_id(calendar_id,"calendar id");query=" ".join(str(query).split())
        if len(query)>MAX_QUERY:raise CalendarError("query exceeds limit")
        n=max(1,min(int(results),MAX_RESULTS));params={"maxResults":n,"singleEvents":True,"orderBy":"startTime"}
        if time_min:params["timeMin"]=_iso(time_min,"time_min")
        if time_max:params["timeMax"]=_iso(time_max,"time_max")
        if query:params["q"]=query
        data=self._request("GET",f"{CALENDAR_API_ROOT}/{quote(calendar_id,safe='')}/events",params=params);items=data.get("items",[]) if isinstance(data.get("items"),list) else []
        events=[_event_summary(x) for x in items[:n] if isinstance(x,Mapping)];return CalendarEvidence("calendar.list",{"calendar_id":calendar_id,"events":events},_fp(events))
    def read(self,event_id,calendar_id="primary"):
        event_id=_id(event_id,"event id");calendar_id=_id(calendar_id,"calendar id");data=self._request("GET",f"{CALENDAR_API_ROOT}/{quote(calendar_id,safe='')}/events/{quote(event_id,safe='')}");return CalendarEvidence("calendar.read",_bounded_event(data),_fp(data))
    def find_free_time(self,*,time_min,time_max,calendar_id="primary",duration_minutes=30):
        start=_iso(time_min,"time_min");end=_iso(time_max,"time_max");duration=max(1,min(int(duration_minutes),1440));events=self.list(calendar_id,time_min=start,time_max=end,results=MAX_RESULTS).data["events"]
        points=[];cursor=datetime.fromisoformat(start);limit=datetime.fromisoformat(end)
        for e in sorted(events,key=lambda x:x.get("start","")):
            try:s=datetime.fromisoformat(e["start"]);f=datetime.fromisoformat(e["end"])
            except Exception:continue
            if s<=cursor:cursor=max(cursor,f);continue
            if (s-cursor).total_seconds()>=duration*60:points.append({"start":cursor.isoformat(),"end":s.isoformat()})
            if f>cursor:cursor=f
        if (limit-cursor).total_seconds()>=duration*60:points.append({"start":cursor.isoformat(),"end":limit.isoformat()})
        return CalendarEvidence("calendar.find_free_time",{"slots":points[:20]},_fp(points[:20]))
    def _mutation(self,method,path,event,operation,idempotency_key,approved,*,headers=None):
        if not approved:raise CalendarError(f"{operation} requires explicit approval")
        key=_id(idempotency_key,"idempotency key");digest=_fp({"operation":operation,"path":path,"event":event,"headers":headers or {}})
        if key!=digest:raise CalendarError("idempotency key does not match operation digest")
        if key in self._mutations:raise CalendarError("duplicate calendar mutation blocked")
        self._mutations.add(key);return self._request(method,path,body=event,headers=headers,retries=0)
    def create(self,*,calendar_id="primary",event,idempotency_key,approved=False):
        body=_validate_event(event,creating=True);path=f"{CALENDAR_API_ROOT}/{quote(_id(calendar_id,'calendar id'),safe='')}/events";out=self._mutation("POST",path,body,"calendar.event.create",idempotency_key,approved);return CalendarEvidence("calendar.event.create",{"event":_bounded_event(out),"event_fingerprint":_fp(body)},_fp(out))
    def update(self,*,calendar_id="primary",event_id,event,etag=None,idempotency_key,approved=False):
        body=_validate_event(event,creating=False);path=f"{CALENDAR_API_ROOT}/{quote(_id(calendar_id,'calendar id'),safe='')}/events/{quote(_id(event_id,'event id'),safe='')}";headers={"If-Match":_id(etag,"etag")} if etag else {};out=self._mutation("PUT",path,body,"calendar.event.update",idempotency_key,approved,headers=headers);return CalendarEvidence("calendar.event.update",{"event":_bounded_event(out)},_fp(out))
    def cancel(self,*,calendar_id="primary",event_id,etag,idempotency_key,approved=False):
        event_id=_id(event_id,"event id");etag=_id(etag,"etag");path=f"{CALENDAR_API_ROOT}/{quote(_id(calendar_id,'calendar id'),safe='')}/events/{quote(event_id,safe='')}";body={"event_id":event_id};headers={"If-Match":etag};out=self._mutation("DELETE",path,body,"calendar.event.cancel",idempotency_key,approved,headers=headers);return CalendarEvidence("calendar.event.cancel",body,_fp(body))
def _bounded_event(v):
    if not isinstance(v,Mapping):return {}
    out={k:_redact(x) for k,x in v.items() if k not in {"conferenceData","attachments"}}
    if isinstance(v.get("attendees"),list):out["attendees"]=[_redact(x) for x in v["attendees"][:MAX_ATTENDEES]]
    if isinstance(v.get("recurrence"),list):out["recurrence"]=[str(x)[:500] for x in v["recurrence"][:MAX_RECURRENCE]]
    return out
def _event_summary(v):
    v=_bounded_event(v);start=v.get("start",{});end=v.get("end",{});return {"id":_id(str(v.get("id","")),"event id"),"etag":str(v.get("etag",""))[:256],"summary":str(v.get("summary",""))[:500],"start":str(start.get("dateTime") or start.get("date") or ""),"end":str(end.get("dateTime") or end.get("date") or ""),"status":str(v.get("status",""))[:64]}
def _validate_event(event,*,creating):
    if not isinstance(event,Mapping):raise CalendarError("event must be an object")
    out=dict(event);start=out.get("start");end=out.get("end")
    if not isinstance(start,Mapping) or not isinstance(end,Mapping):raise CalendarError("event start/end are required")
    if "dateTime" in start:_iso(start["dateTime"],"event start")
    if "dateTime" in end:_iso(end["dateTime"],"event end")
    if not (start.get("date") or start.get("dateTime")) or not (end.get("date") or end.get("dateTime")):raise CalendarError("event start/end are required")
    if start.get("dateTime") and end.get("dateTime") and _iso(end["dateTime"],"event end")<=_iso(start["dateTime"],"event start"):raise CalendarError("event end must be after start")
    attendees=out.get("attendees",[])
    if not isinstance(attendees,list) or len(attendees)>MAX_ATTENDEES:raise CalendarError("attendee limit exceeded")
    for a in attendees:
        if not isinstance(a,Mapping) or not isinstance(a.get("email"),str) or "@" not in a["email"]:raise CalendarError("invalid attendee")
    rec=out.get("recurrence",[])
    if not isinstance(rec,list) or len(rec)>MAX_RECURRENCE:raise CalendarError("recurrence limit exceeded")
    raw=json.dumps(out,sort_keys=True,default=str)
    if len(raw.encode())>MAX_EVENT_BYTES:raise CalendarError("event exceeds size limit")
    return out
def calendar_oauth_scopes():return ("https://www.googleapis.com/auth/calendar.events","https://www.googleapis.com/auth/calendar.readonly")
