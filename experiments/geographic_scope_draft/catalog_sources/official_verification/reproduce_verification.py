"""Reproduce comparison from preserved official web extracts; performs no network I/O."""
from pathlib import Path
import collections
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parent
BASE_URL = 'https://tjj.wuhu.gov.cn/xwzx/ztzl/tjcd/tjbz/7765477.html'
ALIAS_URL = 'https://www.locpg.gov.cn/zggq/2014-01/04/c_125956269.htm'
NAGQU_URL = 'https://mzt.xizang.gov.cn/zxzx/tzgg/202607/t20260729_551733.html'
LAIWU_URL = 'https://www.saac.gov.cn/daj/c100330/201906/6aa654e7a22649c599643d9138f7dad9.shtml'

def write(name, value):
    (ROOT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

extract = json.loads((ROOT/'wuhu_2016_web_full_extract.json').read_text())
text = '\n'.join(extract['results'])
pairs = re.findall(r'L\d+:\s*(\d{6})\s+([^\s]+)', text)
identities = {}
for code, name in pairs:
    if code in identities and identities[code] != name:
        raise ValueError(f'Conflicting extracted name: {code}')
    identities[code] = name
province = {c:n for c,n in identities.items() if c.endswith('0000')}
assert len(province) == 34
prefecture = {c:n for c,n in identities.items() if c.endswith('00') and not c.endswith('0000') and n.endswith(('市','自治州','地区','盟'))}
assert len(prefecture) == 334
assert prefecture.pop('371200') == '莱芜市'
assert prefecture.pop('542400') == '那曲地区'
prefecture['540600'] = '那曲市'

aliases = '北京 天津 河北 山西 内蒙古 辽宁 吉林 黑龙江 上海 江苏 浙江 安徽 福建 江西 山东 河南 湖北 湖南 广东 广西 海南 重庆 四川 贵州 云南 西藏 陕西 甘肃 青海 宁夏 新疆 台湾 香港 澳门'.split()
province_rows = []
for (code,name),alias in zip(sorted(province.items()), aliases):
    kind = '直辖市' if code[:2] in {'11','12','31','50'} else '自治区' if name.endswith('自治区') else '特别行政区' if name.endswith('特别行政区') else '省'
    province_rows.append(dict(code=code,name=name,level='province',kind=kind,province_code=code,aliases=[alias],evidence_url=BASE_URL,alias_evidence_url=ALIAS_URL))
write('province_34_benchmark.json', dict(artifact_kind='locally_derived_official_evidence_not_official_json', source_date='2016-07-31',alias_source_date='2014-01-04',records=province_rows))
rows = [dict((k,v) for k,v in r.items() if k not in ('aliases','alias_evidence_url')) for r in province_rows]
for code,name in sorted(prefecture.items()):
    kind='地级市' if name.endswith('市') else '自治州' if name.endswith('自治州') else '地区' if name.endswith('地区') else '盟'
    rows.append(dict(code=code,name=name,level='prefecture',kind=kind,province_code=code[:2]+'0000',evidence_url=NAGQU_URL if code=='540600' else BASE_URL))
write('province_prefecture_benchmark.json', dict(artifact_kind='historical_official_table_plus_verified_changes_not_2026_official_full_list',base_date='2016-07-31',retrieved_date='2026-10-15',changes=[dict(remove='371200',name='莱芜市',evidence_url=LAIWU_URL),dict(remove='542400',add='540600',name='那曲市',evidence_url=NAGQU_URL)],records=rows))
actual=json.loads((ROOT.parent/'cn_division/normalized.json').read_text())['records']
fields=('code','name','level','kind','province_code')
expected={r['code']:{k:r[k] for k in fields} for r in rows}
observed={r['code']:{k:r[k] for k in fields} for r in actual if r['level'] in ('province','prefecture')}
missing=[expected[c] for c in expected.keys()-observed.keys()]
extra=[observed[c] for c in observed.keys()-expected.keys()]
changes=[dict(expected=expected[c],observed=observed[c]) for c in expected.keys()&observed.keys() if expected[c]!=observed[c]]
report=dict(comparison_fields=fields,province_count=34,prefecture_count=333,prefecture_types=dict(collections.Counter(r['kind'] for r in rows if r['level']=='prefecture')),supported_identity_count=sum(r['kind'] in {'地级市', '直辖市'} for r in rows),target_policy='single_prefecture_city_or_municipality',compared_identity_count=len(expected),missing=missing,extra=extra,mismatches=changes,exact_identity_match=not(missing or extra or changes),benchmark_basis='2016 historical official full table plus independently verified Laiwu removal and Nagqu reclassification; corroborated by official 2024 aggregate counts; not a fresh 2026 official national full list',county_review='Upstream county auxiliaries are not all independently checked against official sources; they never become supported P1 scope.')
write('comparison_report.json',report)
assert report['exact_identity_match'], report
print(json.dumps(report,ensure_ascii=False,indent=2))
