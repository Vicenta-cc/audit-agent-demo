import https from 'https';
import fs from 'fs/promises';
import path from 'path';

// 配置常量
const CONFIG = {
  url: 'https://dmfw.mca.gov.cn/9095/xzqh/getList',
  interfaceDocUrl: 'https://dmfw.mca.gov.cn/interface.html',
  maxLevel: 3,
  year: undefined,
  excludedProvinceCodes: ['710000', '810000', '820000'], // 台湾省、香港特别行政区、澳门特别行政区
  municipalityProvinceCodes: ['110000', '120000', '310000', '500000']
};

const REQUEST_TIMEOUT_MS = 20000;
const MAX_RETRY_COUNT = 3;

function buildRequestUrl({ code = '', maxLevel = CONFIG.maxLevel } = {}) {
  const url = new URL(CONFIG.url);
  url.searchParams.set('code', code);
  url.searchParams.set('maxLevel', String(maxLevel));
  if (CONFIG.year) {
    url.searchParams.set('year', String(CONFIG.year));
  }
  return url;
}

/**
 * 获取民政部行政区划接口 JSON。
 */
function fetchJson(url) {
  const requestOptions = {
    headers: {
      'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
      Referer: CONFIG.interfaceDocUrl,
      Accept: 'application/json,text/plain,*/*'
    }
  };

  return new Promise((resolve, reject) => {
    const req = https.get(url, requestOptions, (res) => {
      res.setEncoding('utf8');
      let body = '';

      res.on('data', (chunk) => {
        body += chunk;
      });

      res.on('end', () => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          reject(new Error(`接口请求失败: HTTP ${res.statusCode}, ${body.slice(0, 200)}`));
          return;
        }

        try {
          resolve(JSON.parse(body));
        } catch (error) {
          reject(new Error(`接口返回不是有效 JSON: ${error.message}`));
        }
      });

      res.on('error', (err) => {
        reject(err);
      });
    }).on('error', (err) => {
      reject(err);
    });

    req.setTimeout(REQUEST_TIMEOUT_MS, () => {
      req.destroy(new Error(`接口请求超时: ${REQUEST_TIMEOUT_MS}ms`));
    });
  });
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function fetchJsonWithRetry(url) {
  let lastError;

  for (let attempt = 1; attempt <= MAX_RETRY_COUNT; attempt += 1) {
    try {
      return await fetchJson(url);
    } catch (error) {
      lastError = error;
      if (attempt < MAX_RETRY_COUNT) {
        console.warn(`接口请求失败，准备重试 ${attempt}/${MAX_RETRY_COUNT}: ${error.message}`);
        await sleep(attempt * 500);
      }
    }
  }

  throw lastError;
}

function normalizeCode(code) {
  const codeStr = String(code || '');
  if (/^\d{12}$/.test(codeStr)) {
    return codeStr.substring(0, 6);
  }
  if (/^\d{6}$/.test(codeStr)) {
    return codeStr;
  }
  throw new Error(`行政区划代码格式异常: ${codeStr}`);
}

function getCityCode(code) {
  return code.substring(0, 4) + '00';
}

function normalizeCountyCode(node) {
  const codeStr = String(node.code || '');
  if (/^\d{12}$/.test(codeStr) && Number(node.level) >= 4) {
    return codeStr;
  }
  return normalizeCode(codeStr);
}

function sortByCode(items) {
  return items.sort((a, b) => a.code.localeCompare(b.code));
}

function pushUnique(target, item) {
  if (!target.some(existing => existing.code === item.code)) {
    target.push(item);
  }
}

function createMunicipalityCityName(provinceName, cityCode) {
  if (cityCode.endsWith('0200')) {
    return '县';
  }
  return provinceName;
}

/**
 * 对没有县级子节点的地级市继续下钻查询街道/镇级节点。
 */
async function drillCityChildren(cityNode) {
  const requestUrl = buildRequestUrl({ code: String(cityNode.code), maxLevel: 2 });
  console.log(`下钻查询: ${cityNode.name} (${cityNode.code})`);
  const response = await fetchJsonWithRetry(requestUrl);

  if (!response || response.status !== 200) {
    throw new Error(`下钻接口状态异常: ${cityNode.code} ${response?.status ?? 'unknown'} ${response?.message ?? ''}`.trim());
  }

  const children = response.data?.children;
  if (!Array.isArray(children)) {
    throw new Error(`下钻接口响应缺少 data.children: ${cityNode.code}`);
  }

  return children;
}

/**
 * 将接口树转换为当前项目的原始数据结构。
 */
async function parseData(response) {
  console.log('开始解析接口数据...');

  if (!response || response.status !== 200) {
    throw new Error(`接口状态异常: ${response?.status ?? 'unknown'} ${response?.message ?? ''}`.trim());
  }

  const root = response.data;
  if (!root || !Array.isArray(root.children)) {
    throw new Error('接口响应缺少 data.children');
  }

  const provinces = [];
  const cities = [];
  const counties = [];

  for (const provinceNode of root.children) {
    const provinceCode = normalizeCode(provinceNode.code);
    const province = {
      code: provinceCode,
      name: provinceNode.name
    };
    provinces.push(province);

    const children = Array.isArray(provinceNode.children) ? provinceNode.children : [];
    for (const childNode of children) {
      const childCode = normalizeCode(childNode.code);
      const childLevel = Number(childNode.level);

      if (childLevel === 2) {
        const city = {
          code: childCode,
          name: childNode.name,
          provinceCode
        };
        pushUnique(cities, city);

        let cityChildren = Array.isArray(childNode.children) ? childNode.children : [];
        if (cityChildren.length === 0) {
          cityChildren = await drillCityChildren(childNode);
        }

        cityChildren.forEach(countyNode => {
          const countyCode = normalizeCountyCode(countyNode);
          pushUnique(counties, {
            code: countyCode,
            name: countyNode.name,
            cityCode: city.code,
            provinceCode
          });
        });
        continue;
      }

      if (childLevel === 3) {
        if (CONFIG.municipalityProvinceCodes.includes(provinceCode)) {
          const cityCode = getCityCode(childCode);
          pushUnique(cities, {
            code: cityCode,
            name: createMunicipalityCityName(province.name, cityCode),
            provinceCode
          });
          pushUnique(counties, {
            code: childCode,
            name: childNode.name,
            cityCode,
            provinceCode
          });
          continue;
        }

        // 省直辖县级（石河子、济源、仙桃、五指山等）：用接口原名当市，不合成省名容器。
        pushUnique(cities, {
          code: childCode,
          name: childNode.name,
          provinceCode
        });
        pushUnique(counties, {
          code: childCode,
          name: childNode.name,
          cityCode: childCode,
          provinceCode
        });
        continue;
      }

      console.warn(`跳过未知层级节点: ${childNode.code} ${childNode.name} level=${childNode.level}`);
    }
  }

  console.log(`解析完成: 省份 ${provinces.length} 个, 城市 ${cities.length} 个, 区县 ${counties.length} 个`);

  return {
    provinces: sortByCode(provinces),
    cities: sortByCode(cities),
    counties: sortByCode(counties)
  };
}

/**
 * 验证数据完整性。
 */
function validateData(data) {
  const { provinces, cities, counties } = data;

  console.log('验证数据完整性...');

  // 过滤掉台湾、香港、澳门
  const originalProvinceCount = provinces.length;
  data.provinces = provinces.filter(p => !CONFIG.excludedProvinceCodes.includes(p.code));
  data.cities = cities.filter(c => !CONFIG.excludedProvinceCodes.includes(c.provinceCode));
  data.counties = counties.filter(a => !CONFIG.excludedProvinceCodes.includes(a.provinceCode));

  if (originalProvinceCount !== data.provinces.length) {
    console.log(`过滤了 ${originalProvinceCount - data.provinces.length} 个省级行政区（台湾、香港、澳门）`);
  }

  if (data.provinces.length !== 31) {
    console.warn(`省份数量异常: 期望31个，实际${data.provinces.length}个`);
  }

  const provinceMap = new Map(data.provinces.map(p => [p.code, p.name]));
  const invalidCities = data.cities.filter(c => !provinceMap.has(c.provinceCode));
  if (invalidCities.length > 0) {
    console.warn(`发现${invalidCities.length}个城市缺少对应省份`);
  }

  const cityMap = new Map(data.cities.map(c => [c.code, c.name]));
  const invalidAreas = data.counties.filter(a => !cityMap.has(a.cityCode));
  if (invalidAreas.length > 0) {
    console.warn(`发现${invalidAreas.length}个区县缺少对应城市`);
  }

  console.log('数据验证完成');

  return data;
}

/**
 * 保存原始数据。
 */
async function saveRawData(data) {
  const rawDataPath = path.join(process.cwd(), 'temp', 'raw-data.json');

  await fs.mkdir(path.dirname(rawDataPath), { recursive: true });
  await fs.writeFile(rawDataPath, JSON.stringify(data, null, 2), 'utf8');
  console.log(`原始数据已保存到: ${rawDataPath}`);

  const { provinces, cities, counties } = data;
  console.log('\n=== 数据统计 ===');
  console.log(`省份数量: ${provinces.length}`);
  console.log(`城市数量: ${cities.length}`);
  console.log(`区县数量: ${counties.length}`);
  console.log(`总计: ${provinces.length + cities.length + counties.length}`);

  console.log('\n=== 数据示例 ===');
  console.log('省份示例:', provinces.slice(0, 3));
  console.log('城市示例:', cities.slice(0, 3));
  console.log('区县示例:', counties.slice(0, 3));
}

/**
 * 主函数。
 */
async function main() {
  try {
    const requestUrl = buildRequestUrl();
    console.log('开始从民政部获取最新行政区划数据...');
    console.log(`数据源: ${requestUrl.toString()}`);

    console.log('正在获取接口数据...');
    const response = await fetchJsonWithRetry(requestUrl);
    console.log('接口数据获取成功');

    const rawData = await parseData(response);
    const validatedData = validateData(rawData);

    await saveRawData(validatedData);

    console.log('\n数据抓取完成！');
  } catch (error) {
    console.error('数据抓取失败:', error.message);
    process.exit(1);
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}

export { main as fetchData };
