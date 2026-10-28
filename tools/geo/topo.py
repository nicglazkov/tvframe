import json,sys
t=json.load(open(sys.argv[1])); sc,tr=t['transform']['scale'],t['transform']['translate']
arcs=[]
for a in t['arcs']:
    x=y=0; pts=[]
    for dx,dy in a:
        x+=dx;y+=dy; pts.append((x*sc[0]+tr[0], y*sc[1]+tr[1]))
    arcs.append(pts)
def ring(idx):
    out=[]
    for i in idx:
        p=arcs[i] if i>=0 else arcs[~i][::-1]
        out.extend(p if not out else p[1:])
    return out
K=float(sys.argv[2])
d=[]
geom=t['objects']['land']['geometries']
for g in geom:
    polys=g['arcs'] if g['type']=='MultiPolygon' else [g['arcs']]
    for poly in polys:
        for r in poly:
            pts=ring(r)
            if max(lat for _,lat in pts) < -58: continue   # drop Antarctica
            s=[]; last=None
            for lon,lat in pts:
                q=(round((lon+180)*K,1), round((90-lat)*K,1))
                if q!=last: s.append(q); last=q
            if len(s)<3: continue
            # break the ring where it wraps across the antimeridian
            out='M%g %g'%s[0]
            for (x0,y0),(x1,y1) in zip(s,s[1:]):
                out+=('M' if abs(x1-x0)>90*K else 'L')+'%g %g'%(x1,y1)
            d.append(out+'Z')
print(''.join(d))
