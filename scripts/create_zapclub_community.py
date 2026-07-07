import base64, json, time, urllib.request, urllib.error
from pathlib import Path
from PIL import Image, ImageOps

BASE='https://go.workflowapi.com.br'
ADMIN='ab7493b4527e089973ad0cfed079d118'
ROOT=Path('/home/sistemabritto/Documentos/evo-nexus')
SRC_LOGO=Path('/home/sistemabritto/.hermes/image_cache/img_52d95475f17f.jpg')
OUT=ROOT/'workspace/assets/images/zapclub-groups/processed'
OUT.mkdir(parents=True, exist_ok=True)
GROUPS=[
 ('Conquistador de Tokens','conquistador-de-tokens.png'),
 ('Triangulador de Modelos','triangulador-de-modelos.png'),
 ('Manipulador de Ferramentas e Integrações','manipulador-de-ferramentas-e-integracoes.png'),
 ('Desenvolvedor de Skills','desenvolvedor-de-skills.png'),
 ('Maestro de Rotinas','maestro-de-rotinas.png'),
]

def req(method,path,token,body=None,timeout=45):
    data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None
    r=urllib.request.Request(BASE+path,data=data,method=method,headers={'apikey':token,'Content-Type':'application/json','Accept':'application/json','User-Agent':'Mozilla/5.0'})
    try:
        raw=urllib.request.urlopen(r,timeout=timeout).read().decode()
        return json.loads(raw) if raw else {'message':'success'}
    except urllib.error.HTTPError as e:
        txt=e.read().decode(errors='ignore')
        raise RuntimeError(f'{method} {path} HTTP {e.code}: {txt[:800]}')

def get_felipe_token():
    data=req('GET','/instance/all',ADMIN)
    items = data.get('data', []) if isinstance(data, dict) else data
    for inst in items:
        if isinstance(inst, dict) and inst.get('name') == 'felipe':
            return str(inst['token'])
    raise RuntimeError('instância felipe não encontrada')

def square_jpeg(src,dst,size=640):
    im=Image.open(src).convert('RGB')
    im=ImageOps.pad(im,(size,size),method=Image.Resampling.LANCZOS,color=(8,12,10),centering=(0.5,0.5))
    im.save(dst,'JPEG',quality=86,optimize=True)
    return dst

def data_url(path):
    return 'data:image/jpeg;base64,'+base64.b64encode(Path(path).read_bytes()).decode()

def extract_jid(resp):
    data=resp.get('data', resp.get('group', resp.get('community', resp)))
    if isinstance(data,dict):
        return data.get('JID') or data.get('jid') or data.get('id')
    return None

def group_list(token):
    resp=req('GET','/group/list',token,timeout=60)
    data=resp.get('data') or resp.get('groups') or []
    return data if isinstance(data,list) else []

def find_by_name(groups,name,is_parent=None):
    for g in groups:
        if (g.get('Name') or g.get('name')) == name:
            if is_parent is None or bool(g.get('IsParent'))==is_parent:
                return g.get('JID') or g.get('jid')
    return None

def set_photo(token,jid,img_path):
    return req('POST','/group/photo',token,{'groupJid':jid,'image':data_url(img_path)},timeout=60)

def main():
    token=get_felipe_token()
    # process images
    logo_jpg=square_jpeg(SRC_LOGO, OUT/'community-ia-para-negocios.jpg')
    processed={}
    for title,file in GROUPS:
        processed[title]=square_jpeg(ROOT/'workspace/assets/images/zapclub-groups'/file, OUT/(Path(file).stem+'.jpg'))
    print('processed images ok')

    existing=group_list(token)
    community_jid=find_by_name(existing,'IA para Negócios',True)
    if community_jid:
        print('community exists', community_jid)
    else:
        resp=req('POST','/community/create',token,{'communityName':'IA para Negócios'},timeout=60)
        print('community create resp', json.dumps(resp,ensure_ascii=False)[:1000])
        community_jid=extract_jid(resp)
        if not community_jid:
            # refresh list fallback
            time.sleep(3); community_jid=find_by_name(group_list(token),'IA para Negócios',True)
        if not community_jid: raise RuntimeError('Não consegui extrair JID da comunidade')
    print('community_jid',community_jid)
    try:
        print('set community photo', json.dumps(set_photo(token,community_jid,logo_jpg),ensure_ascii=False)[:500])
    except Exception as e:
        print('WARN community photo failed:', e)

    existing=group_list(token)
    group_jids=[]
    for title,_ in GROUPS:
        jid=find_by_name(existing,title,False)
        if jid:
            print('group exists',title,jid)
        else:
            # Try empty participant list first; if API rejects, retry with known Felipe phone from previous config.
            payload={'groupName':title,'participants':[]}
            try:
                resp=req('POST','/group/create',token,payload,timeout=60)
            except Exception as e:
                print('empty participants failed for', title, e)
                resp=req('POST','/group/create',token,{'groupName':title,'participants':['557196815772']},timeout=60)
            print('group create',title,json.dumps(resp,ensure_ascii=False)[:800])
            jid=extract_jid(resp)
            if not jid:
                time.sleep(3); jid=find_by_name(group_list(token),title,False)
            if not jid: raise RuntimeError(f'Não consegui extrair JID do grupo {title}')
        try:
            print('set group photo',title,json.dumps(set_photo(token,jid,processed[title]),ensure_ascii=False)[:500])
        except Exception as e:
            print('WARN group photo failed',title,e)
        group_jids.append((title,jid))

    resp=req('POST','/community/add',token,{'communityJid':community_jid,'groupJid':[jid for _,jid in group_jids]},timeout=90)
    print('community add resp',json.dumps(resp,ensure_ascii=False)[:1200])
    result={'community':{'name':'IA para Negócios','jid':community_jid},'groups':[{'name':t,'jid':j} for t,j in group_jids]}
    (OUT/'zapclub-community-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print('RESULT_JSON',json.dumps(result,ensure_ascii=False))

if __name__=='__main__': main()
