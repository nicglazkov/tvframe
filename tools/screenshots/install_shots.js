// Screenshots of the photo manager from a real, running install (a test one, filled with stock photos).
// The page loads as http://tvframe.demo:8150/, so no address shows; every request goes to the install, whose
// ports can be shifted (OFFSET=1000 reaches a test install on 9150-9154 next to a real one).
//   OFFSET=1000 node install_shots.js <out-dir>      (needs puppeteer-core and Chrome; CHROME=/path/to/chrome)
const fs = require('fs'), path = require('path');
const puppeteer = require('puppeteer-core');
const OUT = path.resolve(process.argv[2] || '.');
const OFFSET = +(process.env.OFFSET || 0), HOST = 'tvframe.demo';
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const b = await puppeteer.launch({ executablePath: process.env.CHROME || '/usr/bin/google-chrome', headless: 'new',
    args: ['--no-sandbox', '--hide-scrollbars', '--autoplay-policy=no-user-gesture-required',
      // headless Chrome reports no mouse, which turns off the hover previews
      '--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4'] });
  const p = await b.newPage();
  const errs = []; p.on('pageerror', e => errs.push(e.message));
  await p.setRequestInterception(true);
  p.on('request', async r => {
    const u = new URL(r.url());
    if (u.hostname !== HOST) return u.protocol === 'data:' || u.protocol === 'blob:' ? r.continue() : r.abort();
    const to = `http://127.0.0.1:${+u.port + OFFSET}${u.pathname}${u.search}`;
    try {
      const h = { ...r.headers() }; delete h.host;
      const res = await fetch(to, { method: r.method(), headers: h, body: r.postData() });
      const headers = {}; res.headers.forEach((v, k) => { if (k !== 'content-encoding' && k !== 'transfer-encoding') headers[k] = v; });
      await r.respond({ status: res.status, headers, body: Buffer.from(await res.arrayBuffer()) });
    } catch { r.abort(); }
  });
  const go = async hash => { await p.goto(`http://${HOST}:8150/${hash}`, { waitUntil: 'networkidle0' }); await sleep(1500); };
  const shot = async n => { await sleep(900); await p.screenshot({ path: path.join(OUT, `${n}.png`) }); console.log(n); };
  const click = async sel => { const e = await p.$(sel); if (!e) throw new Error('missing ' + sel); await e.click(); await sleep(700); };
  const esc = async () => { await p.keyboard.press('Escape'); await sleep(500); };

  for (const dark of [false, true]) {
    await p.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: dark ? 'dark' : 'light' }]);
    const sfx = dark ? '-dark' : '';
    await p.setViewport({ width: 1440, height: 960, deviceScaleFactor: 2 });
    await go(''); await shot('manager-home' + sfx);
    await go('#/f/Coast'); await shot('manager-folder' + sfx);

    await go('#/all/rank');
    const boxes = await p.$$('.rk tr.row .rcb');
    for (const k of [0, 2, 3]) if (boxes[k]) { await boxes[k].click(); await sleep(250); }
    await shot('manager-rank' + sfx);
    await click('[data-pb="frame"]'); await sleep(1500); await shot('manager-frame' + sfx); await esc(); await esc();

    await go('#/all/clips');
    const th = await p.$('.rk-clips tr[data-cid] td.th');
    if (th) { await th.hover(); await sleep(3500); await shot('manager-clips' + sfx); await p.mouse.move(5, 5); }
    await click('.rk-clips button.btn[data-trim]'); await sleep(4000); await shot('manager-trim' + sfx); await esc();

    await p.setViewport({ width: 390, height: 844, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
    await go(''); await shot('phone' + sfx);
  }
  console.log('errors:', JSON.stringify(errs));
  await b.close();
})();
