import base64, json, time, urllib.request, urllib.error
from pathlib import Path
from PIL import Image, ImageOps
BASE='https://go.workflowapi.com.br'; ADMIN='ab7493b4527e089973ad0cfed079d118'
ROOT=Path('/home/sistemabritto/Documentos/evo-nexus'); OUT=ROOT/'workspace/assets/images/zapclub-groups/processed-small'; OUT.mkdir(parents=True,exist_ok=True)
LOGO=Path('/home/sistemabritto/.hermes/image_cache/img_52d95475f17f.jpg')
PARTICIPANT='557199841612@s.whatsapp.net'
GROUPS=[('Conquistador de Tokens','conquistador-de-tokens.png'),('Triangulador de Modelos','triangulador-de-modelos.png'),('Manipulador de Ferramentas e Integrações','manipulador-de-ferramentas-e-integracoes.png'),('Desenvolvedor de Skills','desenvolvedor-de-skills.png'),('Maestro de Rotinas','maestro-de-rotinas.png')]

def req(method,path,token,body=None,timeout=180,retries=2):
    data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None
    for attempt in range(retries+1):
        r=urllib.request.Request(BASE+path,data=data,method=method,headers={'apikey':token,'Content-Type':'application/json','Accept':'application/json','User-Agent':'Mozilla/5.0'})
        try:
            raw=urllib.request.urlopen(r,timeout=timeout).read().decode(); return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            txt=e.read().decode(errors='ignore')
            if ('429' in txt or e.code==429 or 'rate-overlimit' in txt) and attempt<retries:
                time.sleep(30); continue
            raise RuntimeError(f'{method} {path} HTTP {e.code}: {txt[:800]}')
        except TimeoutError:
            if attempt<retries: time.sleep(10); continue
            raise

def token():
    data=req('GET','/instance/all',ADMIN,timeout=60)
    for i in data.get('data',[]):
        if i.get('name')=='felipe': return i['token']
    raise RuntimeError('felipe token not found')

def groups(tok):
    d=req('GET','/group/list',tok,timeout=180)
    return d.get('data') if isinstance(d.get('data'),list) else []

def find(gs,name,parent=None):
    for g in gs:
        if g.get('Name')==name and (parent is None or bool(g.get('IsParent'))==parent): return g.get('JID')

def find_child_same_community(gs,community_jid):
    for g in gs:
        if g.get('LinkedParentJID')==community_jid: return g.get('JID')

def extract_jid(resp):
    d=resp.get('data') or resp.get('group') or resp.get('community') or resp
    return d.get('JID') or d.get('jid') if isinstance(d,dict) else None

def prep(src,dst,size=320):
    im=Image.open(src).convert('RGB')
    im=ImageOps.pad(im,(size,size),method=Image.Resampling.LANCZOS,color=(8,12,10))
    im.save(dst,'JPEG',quality=75,optimize=True)
    return dst

def imgstr(path): return 'data:image/jpeg;base64,'+base64.b64encode(Path(path).read_bytes()).decode()

def set_photo(tok,jid,path):
    return req('POST','/group/photo',tok,{'groupJid':jid,'image':imgstr(path)},timeout=240,retries=1)

def main():
    tok=token(); print('token ok',flush=True)
    community_img=prep(LOGO,OUT/'community.jpg')
    imgmap={title:prep(ROOT/'workspace/assets/images/zapclub-groups'/file,OUT/(Path(file).stem+'.jpg')) for title,file in GROUPS}
    gs=groups(tok)
    community=find(gs,'IA para Negócios',True)
    if not community:
        r=req('POST','/community/create',tok,{'communityName':'IA para Negócios'},timeout=180); community=extract_jid(r)
    if not community: raise RuntimeError('community jid missing')
    print('community',community,flush=True)
    try: print('community photo',set_photo(tok,community,community_img),flush=True)
    except Exception as e: print('WARN community photo',e,flush=True)
    gs=groups(tok)
    first=find(gs,'Conquistador de Tokens',False)
    temp=find(gs,'Teste ZapClub TEMP',False)
    default_child=find_child_same_community(gs,community)
    if not first and temp:
        req('POST','/group/name',tok,{'groupJid':temp,'name':'Conquistador de Tokens'},timeout=180); first=temp; print('renamed temp to first',first,flush=True); time.sleep(5)
    elif not first and default_child:
        req('POST','/group/name',tok,{'groupJid':default_child,'name':'Conquistador de Tokens'},timeout=180); first=default_child; print('renamed default child to first',first,flush=True); time.sleep(5)
    result=[]
    for title,_ in GROUPS:
        gs=groups(tok); jid=find(gs,title,False)
        if not jid:
            print('creating',title,flush=True)
            r=req('POST','/group/create',tok,{'groupName':title,'participants':[PARTICIPANT]},timeout=240,retries=2)
            print('create resp',json.dumps(r,ensure_ascii=False)[:500],flush=True)
            jid=extract_jid(r)
            time.sleep(20)
        if not jid: raise RuntimeError('missing jid '+title)
        try: print('photo',title,set_photo(tok,jid,imgmap[title]),flush=True)
        except Exception as e: print('WARN photo',title,e,flush=True)
        result.append((title,jid))
    print('adding to community',flush=True)
    r=req('POST','/community/add',tok,{'communityJid':community,'groupJid':[j for _,j in result]},timeout=240,retries=2)
    print('add resp',json.dumps(r,ensure_ascii=False)[:1000],flush=True)
    out={'community':{'name':'IA para Negócios','jid':community},'groups':[{'name':t,'jid':j} for t,j in result]}
    (OUT/'result.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
    print('RESULT_JSON',json.dumps(out,ensure_ascii=False),flush=True)
if __name__=='__main__': main()
