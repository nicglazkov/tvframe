#!/usr/bin/env python3
"""Fetch the live glance data (no API keys) and write ~/tvframe/site/data/live.json.
Weather and air quality: Open-Meteo, for LIVE_LAT/LIVE_LON in config.env. Headlines: NPR's public top-stories RSS.
Sun times and golden hour are computed here. Nothing about the household is sent anywhere.
Cron runs it every 20 minutes; a failed source keeps the last good value."""
import json, math, os, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'server'))
import tvconf  # noqa: E402
if not (tvconf.LIVE_LAT and tvconf.LIVE_LON):
    sys.exit('Set LIVE_LAT, LIVE_LON and LIVE_PLACE in ' + tvconf.PATH)
LAT, LON, WHERE = float(tvconf.LIVE_LAT), float(tvconf.LIVE_LON), tvconf.LIVE_PLACE
TZ = ZoneInfo(tvconf.TZ)
OUT = os.path.expanduser('~/tvframe/site/data/live.json')
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
try:
    x = ET.fromstring(get('https://feeds.npr.org/1001/rss.xml', raw=True))
    out['news'] = {'source': 'NPR', 'items': [{'title': (i.findtext('title') or '').strip(), 'date': i.findtext('pubDate')} for i in x.iter('item')][:6]}
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
