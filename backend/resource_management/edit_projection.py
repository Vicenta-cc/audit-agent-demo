"""Read-only locations and actual persisted differences for resource tools."""


def item_index(kind,content):
    if kind=='lexicon':
        return [{'position':i,'target_id':e['id'],'term':e['term'],'kind':e['kind'],
                 'parent_id':e['parent_id']} for i,e in enumerate(content['entries'],1)]
    rows=[]
    for category in content['categories']:
        for rule in category['rules']:
            rows.append({'position':len(rows)+1,'target_id':rule['rule_id'],
                         'category_id':category['category_id'],'name':rule['name']})
    return rows


def differences(before,after,path=''):
    if before==after:return []
    if isinstance(before,dict) and isinstance(after,dict):
        return [item for key in sorted(before.keys()|after.keys())
                for item in differences(before.get(key),after.get(key),path+'/'+key)]
    if isinstance(before,list) and isinstance(after,list) and len(before)==len(after):
        return [item for i,(a,b) in enumerate(zip(before,after))
                for item in differences(a,b,path+'/'+str(i))]
    return [{'path':path,'before':before,'after':after}]
