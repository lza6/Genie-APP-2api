import json,urllib.request,urllib.error
KEY="AIzaSyBkanDK4DjniXPjw7TWA1dp9bUeOyHov6c"
def signup():
    r=urllib.request.Request("https://identitytoolkit.googleapis.com/v1/accounts:signUp?key="+KEY,
        data=json.dumps({"returnSecureToken":True,"clientType":"CLIENT_TYPE_ANDROID"}).encode(),
        headers={'Content-Type':'application/json','X-Android-Package':'co.appnation.geniechat'})
    with urllib.request.urlopen(r,timeout=25) as f: d=json.loads(f.read())
    return d['idToken'],d['localId'],d.get('refreshToken')
B="https://genie-production-yfvxbm4e6q-uc.a.run.app/v2/chats/local/completions"
def call(tok,msg="hi"):
    H={'Authorization':'Bearer '+tok,'X-App-Platform':'android','X-App-Version':'7.9.2','X-App-Id':'1658377526','Content-Type':'application/json'}
    body={"model":"gpt-4o-mini","messages":[{"type":"prompt","content":msg,"fromUser":True}]}
    r=urllib.request.Request(B,data=json.dumps(body).encode(),headers=H)
    try:
        with urllib.request.urlopen(r,timeout=90) as f: return f.status,dict(f.headers),f.read().decode('utf-8','replace')
    except urllib.error.HTTPError as e: return e.code,dict(e.headers),e.read().decode('utf-8','replace')
tok,uid,rt=signup(); print("anon uid",uid,"len(token)",len(tok))
st,h,b=call(tok,"ping")
print("status",st,"ctype",h.get('Content-Type'))
print(b[:200])
# 耗尽账号的错误格式
old=open('idtoken_new.txt').read().strip()
st,h,b=call(old,"ping")
print("\n=== exhausted account ===")
print("status",st,"ctype",h.get('Content-Type'))
print(b[:200])
