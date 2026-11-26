"""Publish only the checked public derivative using a GitHub Actions ephemeral OIDC JWT."""
import json, os, time
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[1]
PUBLIC='https://taiwan-d6m-radar.kaoelvmpvm.chatgpt.site'
ENDPOINT=PUBLIC+'/d6m-sync'
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
OPENER=build_opener(NoRedirect())
def get_json(url,headers=None,body=None):
    try:
        with OPENER.open(Request(url,data=body,headers=headers or {}),timeout=40) as res:
            return json.loads(res.read())
    except HTTPError as e:
        # Never expose request headers, tokens, or upstream body.
        origin=urlsplit(url).hostname
        label=''
        if origin==urlsplit(PUBLIC).hostname:
            # Only log a fixed diagnostic classification, never the response body or request headers.
            try:
                body=e.read(8192).decode('utf-8',errors='replace')
                obj=json.loads(body)
                label='; site worker rejected credential' if obj=={'error':'Forbidden'} else '; JSON rejection'
            except Exception:
                flags=('cloudflare','access denied','forbidden','blocked','authentication','unauthorized','rate limit')
                low=body.lower() if 'body' in locals() else ''
                label='; response markers='+','.join(x for x in flags if x in low)
            label+='; path='+urlsplit(url).path
        raise RuntimeError(f'HTTP {e.code} from {origin}{label}') from None
def main():
    raw=(ROOT/'site/data.json').read_bytes()
    data=json.loads(raw)
    if len(data.get('rows',[]))!=150 or data.get('expected')!=150:raise ValueError('Wrong snapshot size')
    public_before=get_json(PUBLIC+'/data.json')
    print('Public anonymous read reachable; prior asof '+public_before['asof'],flush=True)
    request_url=os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']
    params=urlencode({'audience':ENDPOINT})
    token=get_json(request_url+('&' if '?' in request_url else '?')+params,
                   {'Authorization':'Bearer '+os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN'],'Accept':'application/json'})['value']
    if not isinstance(token,str) or not token:raise ValueError('Empty OIDC token')
    result=get_json(ENDPOINT,{'X-GitHub-OIDC':token,'Content-Type':'application/json'},raw)
    if result.get('asof')!=data['asof'] or result.get('coverage')!=150 or result.get('status')!=data['status']:
        raise RuntimeError('Public write acknowledgement mismatch')
    # Independently read the anonymous public endpoint, compare the exact decoded JSON.
    published=get_json(PUBLIC+'/data.json',{'Cache-Control':'no-cache'})
    if published!=data:raise RuntimeError('Public read-back did not match source')
    print(f"Published and independently read back {data['asof']} / {data['status']} / 150 rows")
if __name__=='__main__':
    for attempt in range(3):
        try:main();break
        except Exception as exc:
            if attempt==2:raise
            print(f'Sync attempt {attempt+1} failed: {exc}; retrying',flush=True);time.sleep(10*(attempt+1))
