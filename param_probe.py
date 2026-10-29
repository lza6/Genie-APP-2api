import json,urllib.request,urllib.error
KEY="AIzaSyBkanDK4DjniXPjw7TWA1dp9bUeOyHov6c"
def newtok():
    r=urllib.request.Request("https://identitytoolkit.googleapis.com/v1/accounts:signUp?key="+KEY,
        data=json.dumps({"returnSecureToken":True}).encode(),headers={'Content-Type':'application/json','X-Android-Package':'co.appnation.geniechat'})
    with urllib.request.urlopen(r,timeout=25) as f: d=json.loads(f.read())
    return d['idToken'],d['localId']
B="https://genie-production-yfvxbm4e6q-uc.a.run.app/v2/chats/local/completions"
def call(tok,body):
    H={'Authorization':'Bearer '+tok,'X-App-Platform':'android','X-App-Version':'7.9.2','X-App-Id':'1658377526','Content-Type':'application/json'}
    r=urllib.request.Request(B,data=json.dumps(body).encode(),headers=H)
    try:
        with urllib.request.urlopen(r,timeout=90) as f: return f.status,f.read().decode('utf-8','replace')
    except urllib.error.HTTPError as e: return e.code,e.read().decode('utf-8','replace')
def txt(b):
    parts=[]
    for line in b.split('\n'):
        if line.startswith('data: ') and 'content' in line:
            try: parts.append(json.loads(line[6:]).get('content',''))
            except: pass
    return ''.join(parts)
tok,uid=newtok(); print('uid',uid)
st,b=call(tok,{"model":"gpt-4o-mini","messages":[{"type":"prompt","content":"Who are you? One short sentence.","fromUser":True}],
    "parameters":{"systemPrompt":"You are ZORBLAX-9, a pirate robot. Always mention the word ZORBLAX.","maxTokens":50}})
print('P1 systemPrompt+maxTokens [%s]:'%st, txt(b)[:200])
st,b=call(tok,{"model":"gpt-4o-mini","messages":[
    {"type":"prompt","content":"My name is Kaito.","fromUser":True},
    {"type":"response","content":"Nice to meet you Kaito.","fromUser":False},
    {"type":"prompt","content":"What is my name?","fromUser":True}]})
print('P2 多轮 [%s]:'%st, txt(b)[:200])
st,b=call(tok,{"model":"gpt-4o-mini","messages":[{"type":"prompt","content":"count to 30","fromUser":True}],"parameters":{"maxTokens":10}})
print('P3 maxTokens=10 [%s]:'%st, txt(b)[:200])
