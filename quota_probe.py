import json,urllib.request,urllib.error
tok=open('idtoken_new.txt').read().strip()
B="https://genie-production-yfvxbm4e6q-uc.a.run.app/v2/chats/local/completions"
H={'Authorization':'Bearer '+tok,'X-App-Platform':'android','X-App-Version':'7.9.2','X-App-Id':'1658377526','Content-Type':'application/json'}
def post(body):
    r=urllib.request.Request(B,data=json.dumps(body).encode(),headers=H)
    try:
        with urllib.request.urlopen(r,timeout=90) as f: return f.status,f.read().decode('utf-8','replace')
    except urllib.error.HTTPError as e: return e.code,e.read().decode('utf-8','replace')
    except Exception as e: return 'ERR',str(e)
n=0
while n<40:
    n+=1
    st,b=post({"model":"gpt-4o-mini","messages":[{"type":"prompt","content":"say ok","fromUser":True}]})
    if 'RATE_LIMIT' in b:
        print("LIMIT at request #%d: %s"%(n,b[:160])); break
    parts=[]
    for line in b.split('\n'):
        if line.startswith('data: ') and 'content' in line:
            try: parts.append(json.loads(line[6:]).get('content',''))
            except: pass
    print("#%d [%s] %r"%(n,st,''.join(parts).strip()[:40]))
print("total ok",n-1)
