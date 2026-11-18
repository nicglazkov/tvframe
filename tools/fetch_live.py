#!/usr/bin/env python3
"""Fetch the live glance data (no API keys) and write site/data/live.json.
Weather and air quality: Open-Meteo, for LIVE_LAT/LIVE_LON in config.env, only with TVFRAME_WEATHER=on.
Headlines: NPR's public top-stories RSS, or the feeds in TVFRAME_NEWS_FEEDS, only with TVFRAME_HEADLINES=on.
Sun times and golden hour are computed here, with no network. Nothing about the household is sent anywhere.
Cron runs it every 20 minutes; a failed source keeps the last good value."""
import json, math, os, re, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'server'))
import tvconf  # noqa: E402
if not (tvconf.LIVE_LAT and tvconf.LIVE_LON):
    sys.exit('Set LIVE_LAT, LIVE_LON and LIVE_PLACE in ' + tvconf.PATH)
LAT, LON, WHERE = float(tvconf.LIVE_LAT), float(tvconf.LIVE_LON), tvconf.LIVE_PLACE
TZ = ZoneInfo(tvconf.TZ)
OUT = os.path.join(tvconf.ROOT, 'site/data/live.json')
UA = {'User-Agent': 'tvframe/1.0 (home photo frame)'}
def get(u, raw=False):
    r = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20).read()
    return r if raw else json.loads(r)
try: old = json.load(open(OUT))
except Exception: old = {}
out = {'where': WHERE, 'fetched': datetime.now(TZ).isoformat(timespec='seconds'), 'sources': {}}
WMO = {0: 'Clear', 1: 'Mostly clear', 2: 'Partly cloudy', 3: 'Cloudy', 45: 'Fog', 48: 'Fog', 51: 'Light drizzle', 53: 'Drizzle', 55: 'Heavy drizzle',
       61: 'Light rain', 63: 'Rain', 65: 'Heavy rain', 71: 'Light snow', 73: 'Snow', 75: 'Heavy snow', 80: 'Showers', 81: 'Showers', 82: 'Heavy showers',
       95: 'Thunderstorms', 96: 'Thunderstorms', 99: 'Thunderstorms'}
if tvconf.WEATHER:
    try:
        w = get(f'https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&current=temperature_2m,apparent_temperature,weather_code,is_day'
                f'&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset&hourly=temperature_2m,weather_code'
                f'&temperature_unit=fahrenheit&timezone={urllib.parse.quote(tvconf.TZ, safe="")}&forecast_days=3')
        c = w['current']; d = w['daily']
        out['weather'] = {'temp': round(c['temperature_2m']), 'feels': round(c['apparent_temperature']), 'code': c['weather_code'], 'is_day': c['is_day'],
                          'text': WMO.get(c['weather_code'], ''), 'days': [{'date': d['time'][i], 'hi': round(d['temperature_2m_max'][i]), 'lo': round(d['temperature_2m_min'][i]),
                          'rain': d['precipitation_probability_max'][i], 'text': WMO.get(d['weather_code'][i], ''), 'sunrise': d['sunrise'][i], 'sunset': d['sunset'][i]} for i in range(len(d['time']))],
                          'hourly': [{'t': t, 'temp': round(v), 'code': k} for t, v, k in zip(w['hourly']['time'], w['hourly']['temperature_2m'], w['hourly']['weather_code'])][:72]}
        out['sources']['weather'] = 'Open-Meteo'
    except Exception as e:
        print('weather failed', e); out['weather'] = old.get('weather')
    try:
        a = get(f'https://air-quality-api.open-meteo.com/v1/air-quality?latitude={LAT}&longitude={LON}&current=us_aqi,pm2_5,ozone&hourly=us_aqi&timezone={urllib.parse.quote(tvconf.TZ, safe="")}&forecast_days=2')
        out['air'] = {'aqi': round(a['current']['us_aqi']), 'pm25': round(a['current']['pm2_5'], 1),
                      'hourly': [{'t': t, 'aqi': v} for t, v in zip(a['hourly']['time'], a['hourly']['us_aqi'])][:48]}
        out['sources']['air'] = 'Open-Meteo air quality (CAMS)'
    except Exception as e:
        print('air failed', e); out['air'] = old.get('air')
# Headlines: one feed per level (TVFRAME_NEWS_FEEDS, "Label=feed; Label=feed"), shown in turn, or NPR's top stories.
# A feed is an RSS address, or google:US, google:WORLD or google:<place> for Google News. Advice pieces, lists,
# questions and opinion are left out. Long titles are kept: the TV sets them smaller so they show whole.
GOOGLE = 'https://news.google.com/rss/headlines/section/{}?hl=en-US&gl=US&ceid=US:en'
SKIP = re.compile(r"\?|^(op-?ed|opinion|commentary|editorial|analysis|letters?|list|watch|video|photos|quiz|podcast)\b|"
                  r"\b\d+ (ways|things|tips|reasons|signs|questions|best|places)\b|\bhow to\b|\bwhat (to|you need to) know\b|"
                  r"\bhere'?s\b|\byou(r|'re|'ll)?\b", re.I)
SPANISH = {'de', 'la', 'que', 'los', 'las', 'el', 'del', 'por', 'para', 'con', 'una', 'en'}


def feed_url(f):
    if not f.startswith('google:'):
        return f
    f = f[7:].strip()
    return GOOGLE.format({'US': 'topic/NATION', 'WORLD': 'topic/WORLD'}.get(f.upper(), 'geo/' + urllib.parse.quote(f)))


def headlines(label, f, n=3):
    x = ET.fromstring(get(feed_url(f), raw=True))
    # the feed's name for the label: "BBC News", "CalMatters", "NBC Bay Area" from "Local – NBC Bay Area" (Google News
    # names each story's source instead)
    name = max(re.split(r'\s+[-–—|:]\s+', ' '.join((x.findtext('channel/title') or '').split())), key=len)
    name = ' '.join(name.split()[:4])
    got = []
    for i in x.iter('item'):
        t = ' '.join((i.findtext('title') or '').split())
        src = (i.findtext('source') or '').strip()
        if src and t.endswith(' - ' + src):
            t = t[:-len(src) - 3]
        if not t or len(t) > 160 or SKIP.search(t) or len(SPANISH & set(t.lower().split())) >= 2:
            continue
        got.append({'title': t, 'date': i.findtext('pubDate'), 'level': label, 'source': src or name})
        if len(got) == n:
            break
    return got


if tvconf.HEADLINES:
    levels = [tuple(p.strip() for p in x.split('=', 1)) for x in tvconf.NEWS_FEEDS.split(';') if '=' in x]
    try:
        if levels:
            per, seen = [], set()
            for label, f in levels:
                try:
                    per.append([h for h in headlines(label, f) if h['title'].lower() not in seen])
                    seen.update(h['title'].lower() for h in per[-1])
                except Exception as e:
                    print('news failed', label, e)
                    per.append([h for h in (old.get('news') or {}).get('items', []) if h.get('level') == label])
            items = [lv[k] for k in range(3) for lv in per if k < len(lv)]  # first story at each level, then the second...
            if not items:
                raise ValueError('no headlines from any level')
            out['news'] = {'source': 'News', 'items': items}
            out['sources']['news'] = ', '.join(label for label, _ in levels) + ' (TVFRAME_NEWS_FEEDS)'
        else:
            out['news'] = {'source': 'NPR', 'items': [{k: v for k, v in h.items() if k in ('title', 'date')}
                                                      for h in headlines('NPR', 'https://feeds.npr.org/1001/rss.xml', 6)]}
            out['sources']['news'] = 'NPR top stories RSS'
    except Exception as e:
        print('news failed', e); out['news'] = old.get('news')

# Sun: NOAA solar position, scanned by minute. Golden hour = sun between 6 degrees and the horizon.
def elev(t):
    u = t.astimezone(ZoneInfo('UTC')); n = (u - datetime(2000, 1, 1, 12, tzinfo=ZoneInfo('UTC'))).total_seconds() / 86400
    L = math.radians((280.46 + 0.9856474 * n) % 360); g = math.radians((357.528 + 0.9856003 * n) % 360)
    lam = L + math.radians(1.915) * math.sin(g) + math.radians(0.02) * math.sin(2 * g); eps = math.radians(23.439 - 4e-7 * n)
    dec = math.asin(math.sin(eps) * math.sin(lam)); ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    gmst = (18.697374558 + 24.06570982441908 * n) % 24; ha = math.radians(gmst * 15 + LON) - ra
    la = math.radians(LAT)
    return math.degrees(math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha)))
def sun_day(dy):
    t0 = datetime(dy.year, dy.month, dy.day, tzinfo=TZ); prev = elev(t0); ev = {}
    for m in range(1, 24 * 60):
        t = t0 + timedelta(minutes=m); e = elev(t)
        for thr, up, dn in ((-0.833, 'sunrise', 'sunset'), (6, 'golden_end', 'golden_start')):
            if prev < thr <= e: ev[up] = t.strftime('%H:%M')
            if prev >= thr > e: ev[dn] = t.strftime('%H:%M')
        prev = e
    return ev
today = datetime.now(TZ).date()
out['sun'] = {'today': {'date': today.isoformat(), **sun_day(today)}, 'tomorrow': {'date': (today + timedelta(days=1)).isoformat(), **sun_day(today + timedelta(days=1))}}
json.dump(out, open(OUT + '.tmp', 'w'), indent=1); os.replace(OUT + '.tmp', OUT)
print(json.dumps({k: (v if k in ('sun',) else bool(v)) for k, v in out.items() if k != 'sources'}))
