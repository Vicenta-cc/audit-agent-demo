import fs from 'fs/promises';
import path from 'path';
import { buildMiniprogramDist } from './build-miniprogram.js';
import { supplementalCounties } from './supplemental-counties.js';
import { toCityShortCode } from './city-code.js';

/**
 * 数据构建脚本 - 将原始数据转换为各种格式
 */

// 配置常量
const CONFIG = {
  municipalityCodes: ['11', '12', '31', '50'], // 北京、天津、上海、重庆
  outputDir: 'dist',
  tempDir: 'temp'
};

function mergeSupplementalCounties(data) {
  const cityCodes = new Set(data.cities.map(city => city.code));
  const countyCodes = new Map(data.counties.map(county => [county.code, county]));
  const countyKeys = new Set(data.counties.map(county => `${county.cityCode}:${county.name}`));
  const additions = [];

  supplementalCounties.forEach(county => {
    if (!cityCodes.has(county.cityCode)) {
      throw new Error(`补充区县缺少上级城市: ${county.name} (${county.cityCode})`);
    }

    const existingByCode = countyCodes.get(county.code);
    if (existingByCode) {
      if (existingByCode.name !== county.name || existingByCode.cityCode !== county.cityCode) {
        throw new Error(`补充区县编码冲突: ${county.code} ${county.name}`);
      }
      return;
    }

    const key = `${county.cityCode}:${county.name}`;
    if (countyKeys.has(key)) {
      return;
    }

    additions.push(county);
    countyCodes.set(county.code, county);
    countyKeys.add(key);
  });

  if (additions.length > 0) {
    data.counties = data.counties.concat(additions);
    console.log(`补充接口缺失区划数据: ${additions.length} 个区县`);
  }
}

/**
 * 读取原始数据并合并特殊城市数据
 */
async function loadRawData() {
  const rawDataPath = path.join(CONFIG.tempDir, 'raw-data.json');
  const rawData = await fs.readFile(rawDataPath, 'utf8');
  const data = JSON.parse(rawData);

  mergeSupplementalCounties(data);

  // 修复重庆市数据问题：为cityCode=500200的市辖区添加虚拟城市映射
  const counties500200 = data.counties.filter(a => a.cityCode === '500200');
  const city500200 = data.cities.find(c => c.code === '500200');
  if (counties500200.length > 0 && !city500200) {
    // 添加虚拟城市"县"
    const virtualCity = {
      code: '500200',
      name: '县',
      provinceCode: '500000'
    };
    data.cities = data.cities.concat([virtualCity]);
    console.log(`为重庆市添加虚拟城市"县": ${counties500200.length} 个区县`);
  }

  return data;
}

/**
 * 生成带编码版本的数据文件
 */
async function generateCodeVersion(rawData) {
  console.log('生成带编码版本...');

  const { provinces, cities, counties } = rawData;

  // 1. provinces.json - 使用短键名
  const provincesCode = provinces.map(p => ({
    c: parseInt(p.code.substring(0, 2), 10), // 只保留2位省级代码，转为数字
    n: p.name
  }));

  // 2. cities.json（普通地级市 4 位；省直辖县级市 6 位，避免 6590 碰撞）
  const citiesCode = cities.map(c => ({
    c: toCityShortCode(c.code),
    n: c.name,
    p: c.provinceCode.substring(0, 2) // 省级代码2位
  }));

  // 3. counties.json
  const countiesCode = counties.map(a => ({
    c: a.code, // 保留6位区县代码
    n: a.name,
    cc: toCityShortCode(a.cityCode)
  }));


  // 4. 构建pca.json - 带编码的三级结构
  const pcaCode = [];
  provincesCode.forEach(province => {
    const provinceCities = citiesCode.filter(c => c.p === province.c.toString().padStart(2, '0'));

    const provinceData = {
      c: province.c,
      n: province.n,
      ch: []
    };

    provinceCities.forEach(city => {
      const cityCounties = countiesCode.filter(a => a.cc === city.c);

      const cityData = {
        c: city.c,
        n: city.n,
        ch: cityCounties.length > 0 ? cityCounties.map(county => ({
          c: county.c,
          n: county.n
        })) : [{
          // 对于特殊城市（东莞、中山、儋州）和其他没有下级区县的地级市，
          // 将城市本身作为区县级显示
          c: city.c + '01',
          n: city.n
        }]
      };

      provinceData.ch.push(cityData);
    });

    pcaCode.push(provinceData);
  });

  // 5. 构建pc.json - 带编码的二级结构
  const pcCode = [];
  provincesCode.forEach(province => {
    const provinceCities = citiesCode.filter(c => c.p === province.c.toString().padStart(2, '0'));

    const provinceData = {
      c: province.c,
      n: province.n,
      ch: provinceCities.map(city => ({
        c: city.c,
        n: city.n
      }))
    };

    pcCode.push(provinceData);
  });

  // 保存文件
  const codeDir = path.join(CONFIG.outputDir, 'code');
  await fs.mkdir(codeDir, { recursive: true });

  await Promise.all([
    fs.writeFile(path.join(codeDir, 'provinces.json'), JSON.stringify(provincesCode, null, 0)),
    fs.writeFile(path.join(codeDir, 'cities.json'), JSON.stringify(citiesCode, null, 0)),
    fs.writeFile(path.join(codeDir, 'counties.json'), JSON.stringify(countiesCode, null, 0)),
    fs.writeFile(path.join(codeDir, 'pca.json'), JSON.stringify(pcaCode, null, 0)),
    fs.writeFile(path.join(codeDir, 'pc.json'), JSON.stringify(pcCode, null, 0))
  ]);

  console.log(`带编码版本生成完成:`);
  console.log(`  provinces.json: ${provincesCode.length} 个省份`);
  console.log(`  cities.json: ${citiesCode.length} 个城市`);
  console.log(`  counties.json: ${countiesCode.length} 个区县`);

  return { provincesCode, citiesCode, countiesCode, pcaCode, pcCode };
}

/**
 * 生成不带编码版本的数据文件
 */
async function generateNoCodeVersion(rawData) {
  console.log('生成不带编码版本...');

  const { provinces, cities, counties } = rawData;

  // 1. provinces.json - 纯名称数组
  const provincesNoCode = provinces.map(p => p.name);

  // 2. cities.json - 纯名称数组（去重）
  const cityNames = new Set();
  cities.forEach(c => {
    cityNames.add(c.name);
  });
  const citiesNoCode = Array.from(cityNames).sort();

  // 3. counties.json - 纯名称数组（去重）
  const countyNames = new Set();
  counties.forEach(a => {
    countyNames.add(a.name);
  });
  const countiesNoCode = Array.from(countyNames).sort();

  // 4. 构建pca.json - 不带编码的三级结构
  const pca = {};
  provinces.forEach(province => {
    pca[province.name] = {};

    const provinceCities = cities.filter(c => c.provinceCode === province.code);

    provinceCities.forEach(city => {
      const cityCounties = counties.filter(a => a.cityCode === city.code);
      const countyNames = cityCounties.map(a => a.name);

      if (countyNames.length > 0) {
        pca[province.name][city.name] = countyNames;
      } else {
        // 对于特殊城市（东莞、中山、儋州）和其他没有下级区县的地级市，
        // 将城市本身作为区县级显示
        pca[province.name][city.name] = [city.name];
      }
    });
  });

  // 5. 构建pc.json - 不带编码的二级结构
  const pc = {};
  provinces.forEach(province => {
    pc[province.name] = {};

    const provinceCities = cities.filter(c => c.provinceCode === province.code);
    provinceCities.forEach(city => {
      pc[province.name][city.name] = 1;
    });
  });

  // 保存文件
  const noCodeDir = path.join(CONFIG.outputDir, 'no-code');
  await fs.mkdir(noCodeDir, { recursive: true });

  await Promise.all([
    fs.writeFile(path.join(noCodeDir, 'provinces.json'), JSON.stringify(provincesNoCode, null, 0)),
    fs.writeFile(path.join(noCodeDir, 'cities.json'), JSON.stringify(citiesNoCode, null, 0)),
    fs.writeFile(path.join(noCodeDir, 'counties.json'), JSON.stringify(countiesNoCode, null, 0)),
    fs.writeFile(path.join(noCodeDir, 'pca.json'), JSON.stringify(pca, null, 0)),
    fs.writeFile(path.join(noCodeDir, 'pc.json'), JSON.stringify(pc, null, 0))
  ]);

  console.log(`不带编码版本生成完成:`);
  console.log(`  provinces.json: ${provincesNoCode.length} 个省份`);
  console.log(`  cities.json: ${citiesNoCode.length} 个城市`);
  console.log(`  counties.json: ${countiesNoCode.length} 个区县`);

  return { provincesNoCode, citiesNoCode, countiesNoCode, pca, pc };
}

/**
 * 计算文件大小
 */
async function calculateFileSize(filePath) {
  try {
    const stats = await fs.stat(filePath);
    return stats.size;
  } catch (error) {
    return 0;
  }
}

/**
 * 打印文件大小统计
 */
async function printFileSizeStats() {
  console.log('\n=== 文件大小统计 ===');

  const files = [
    { path: 'dist/code/provinces.json', name: 'provinces (带编码)' },
    { path: 'dist/code/cities.json', name: 'cities (带编码)' },
    { path: 'dist/code/counties.json', name: 'counties (带编码)' },
    { path: 'dist/code/pca.json', name: 'pca (带编码)' },
    { path: 'dist/code/pc.json', name: 'pc (带编码)' },
    { path: 'dist/no-code/provinces.json', name: 'provinces (无编码)' },
    { path: 'dist/no-code/cities.json', name: 'cities (无编码)' },
    { path: 'dist/no-code/counties.json', name: 'counties (无编码)' },
    { path: 'dist/no-code/pca.json', name: 'pca (无编码)' },
    { path: 'dist/no-code/pc.json', name: 'pc (无编码)' }
  ];

  let totalSize = 0;
  for (const file of files) {
    const size = await calculateFileSize(file.path);
    totalSize += size;
    console.log(`  ${file.name}: ${(size / 1024).toFixed(1)}KB`);
  }

  console.log(`  总大小: ${(totalSize / 1024).toFixed(1)}KB`);
}

/**
 * 主函数
 */
async function main() {
  try {
    console.log('开始构建数据文件...');

    // 1. 读取原始数据
    const rawData = await loadRawData();
    console.log(`原始数据: ${rawData.provinces.length} 省, ${rawData.cities.length} 市, ${rawData.counties.length} 区县`);

    // 2. 生成带编码版本
    await generateCodeVersion(rawData);

    // 3. 生成不带编码版本
    await generateNoCodeVersion(rawData);

    // 4. 打印文件大小统计
    await printFileSizeStats();

    // 5. 构建小程序专用模块
    await buildMiniprogramDist();

    console.log('\n数据构建完成！');

  } catch (error) {
    console.error('数据构建失败:', error.message);
    process.exit(1);
  }
}

// 运行脚本
if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}

export { main as buildData };
