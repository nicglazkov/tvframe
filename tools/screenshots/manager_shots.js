// Screenshots of the photo manager (site/index.html) filled with the demo library from make_demo.py.
// Every request to the API, the bridge and the media folder is answered here, so no real server or photo is involved.
//   node manager_shots.js <demo-dir> <out-dir>      (needs puppeteer-core and Chrome; CHROME=/path/to/chrome)
const fs = require('fs'), path = require('path');
const puppeteer = require('puppeteer-core');
const [DEMO, OUT] = process.argv.slice(2).map(p => path.resolve(p));
const SITE = path.join(__dirname, '..', '..', 'site', 'index.html');
const HOST = 'tvframe.demo';

const prod = JSON.parse(fs.readFileSync(path.join(DEMO, 'prod.json')));
const FOLDERS = {
  Coast: ['10', '11', '13', '16', '71', '124', '162', '179'],
  Mountains: ['15', '28', '29', '62', '66', '184', '191'],
  Countryside: ['17', '70', '76', '85', '110', '116', '118', '128', '142', '200', '204', '206'],
};
const folderOf = n => Object.keys(FOLDERS).find(f => FOLDERS[f].includes(n)) || 'Countryside';
const items = prod.items.map((p, k) => {
  const n = p.id.replace('demo-', '');
  const [d, t] = [p.date, p.caption.when.split(' · ')[1] || ''];
  return {
    id: p.id, album: folderOf(n), file: `IMG_${4100 + k * 7}.jpg`, video: false,
    thumb: `media/p/${p.id}-720.webp`, big: `media/p/${p.id}-1920.webp`,
    state: k % 9 === 4 ? 'similar' : k % 13 === 6 ? 'off' : 'on', level: 'normal',
    place: p.caption.place, date: d, time: t, camera: '', looks: 4.6 + (k * 53 % 15) / 10,
  };
});
for (const i of items) i.q = Math.round((0.48 + (i.looks - 4.6) / 1.4 * 0.42 + (i.id.length % 5) / 100) * 1000) / 1000;
const status = { state: 'idle', text: 'TV is up to date' };
const lib = {
  folders: Object.keys(FOLDERS).map(name => ({
    name, schedule: null,
    files: items.filter(i => i.album === name).map(({ file, id, video, thumb, big, state, level, place, date }) => ({ file, id, video, thumb, big, state, level, place, date })),
    suggest: [],
  })),
  status, mix: 11,
};
const all = { items, albums: Object.keys(FOLDERS), status, clips_generated: prod.generated, scores_generated: prod.generated };
const rank = album => {
  const ph = items.filter(i => album === '*' || i.album === album).map(i => ({
    id: i.id, file: i.file, album: i.album, date: i.date, camera: i.camera, place: i.place, level: i.level, off: i.state === 'off',
    thumb: i.thumb, scored: true, quality: i.q, weight: Math.round(i.q * 10000) / 100,
    aesthetic: i.looks, aesthetic_norm: Math.min(1, (i.looks - 4) / 2.5),
    sharpness: 0.8 + (i.q * 7 % 0.2), exposure: 0.9 + (i.looks % 0.1), noise: 0.85 + (i.q * 3 % 0.15), luma: 0.45,
    highlights: 0.004, shadows: 0.02, faces: 0, faces_sharp: null, w: 3840, h: 2160, orientation: 'landscape', palette: [],
    cluster_size: i.state === 'similar' ? 2 : 1, is_rep: i.state !== 'similar', also: false, rep_id: i.id, kept: false, rep_file: null,
    reason: i.looks > 5.5 ? 'very sharp, good aesthetic' : 'sharp, plain aesthetic', layout: 'cover',
  })).sort((a, b) => b.weight - a.weight).map((x, k, arr) => ({ ...x, share: Math.round(x.weight / arr.reduce((s, y) => s + y.weight, 0) * 1000) / 10, overall: k + 1, rank: k + 1 }));
  return { album, photos: ph, clips: [], scored_total: ph.length, generated: prod.generated,
    models: { clip: 'ViT-L-14 openai (open_clip)', aesthetic: 'LAION improved aesthetic predictor', faces: 'OpenCV YuNet 2023mar', saliency: 'spectral residual (Hou and Zhang)' } };
};
const progress = { state: 'done', percent: 100, started: 0, elapsed: null, steps: [], gpu: null, gpu_done: [] };
const now = { now: { role: 'show', id: items[5].id, kind: 'full', members: [items[5].id] }, age: 12 };

const cors = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS' };
const json = (r, o) => r.respond({ status: 200, contentType: 'application/json', headers: cors, body: JSON.stringify(o) });

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const b = await puppeteer.launch({ executablePath: process.env.CHROME || '/usr/bin/google-chrome', headless: 'new', args: ['--no-sandbox', '--hide-scrollbars'] });
  const p = await b.newPage();
  const errs = []; p.on('pageerror', e => errs.push(e.message));
  await p.setRequestInterception(true);
  p.on('request', r => {
    const u = new URL(r.url());
    if (u.hostname !== HOST) return u.protocol === 'data:' ? r.continue() : r.abort();
    if (r.method() === 'OPTIONS') return r.respond({ status: 204, headers: cors });
    if (u.port === '8153') {
      if (u.pathname === '/lib') return json(r, lib);
      if (u.pathname === '/all') return json(r, all);
      if (u.pathname === '/rank') return json(r, rank(u.searchParams.get('album') || '*'));
      if (u.pathname === '/progress') return json(r, progress);
      return json(r, { ok: true, status });
    }
    if (u.port === '8154') return u.pathname === '/now' ? json(r, now) : json(r, { ok: true });
    const m = u.pathname.match(/^\/media\/p\/(.+)-(720|1920)\.webp$/);
    if (m) {
      const f = m[2] === '720' ? path.join(DEMO, 'thumbs', `${m[1]}-720.webp`) : path.join(DEMO, 'frames', `${m[1]}.jpg`);
      return fs.existsSync(f) ? r.respond({ status: 200, contentType: m[2] === '720' ? 'image/webp' : 'image/jpeg', body: fs.readFileSync(f) }) : r.respond({ status: 404 });
    }
    if (u.pathname === '/' || u.pathname === '/index.html') return r.respond({ status: 200, contentType: 'text/html', body: fs.readFileSync(SITE) });
    return r.respond({ status: 404 });
  });
  const base = `http://${HOST}:8150/`;
  const go = async (hash) => { await p.goto(base + hash, { waitUntil: 'networkidle0' }); await p.reload({ waitUntil: 'networkidle0' }); };
  const shot = async (n) => { await new Promise(r => setTimeout(r, 1200)); await p.screenshot({ path: path.join(OUT, `${n}.png`) }); };
  for (const dark of [false, true]) {
    await p.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: dark ? 'dark' : 'light' }]);
    const sfx = dark ? '-dark' : '';
    await p.setViewport({ width: 1440, height: 960, deviceScaleFactor: 2 });
    await go(''); await shot('manager-home' + sfx);
    await go('#/f/Countryside'); await shot('manager-folder' + sfx);
    await go('#/all/rank'); await shot('manager-rank' + sfx);
    await p.setViewport({ width: 390, height: 844, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
    await go(''); await shot('phone-home' + sfx);
    await go('#/f/Coast'); await shot('phone-folder' + sfx);
  }
  console.log('errors:', JSON.stringify(errs));
  await b.close();
})();
