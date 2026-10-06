"""Decode what src/lib/qr.ts encodes, with a reader written separately from it.

Checks per text: the format word (BCH) in both copies, the mask, every
Reed-Solomon block's syndromes, and that the payload round-trips; across
versions 1 to 10. Run: `npm run check:qr` (needs node >= 22.6 and python 3).
The encoder has no dependency, so neither does this check.
"""
import json, os, subprocess, random, string, sys
EPC=[-1,10,16,26,18,24,16,18,22,22,26]; NB=[-1,1,1,1,2,2,4,4,4,5,5]
# independent GF(256) with log tables
EXP=[0]*512; LOG=[0]*256; x=1
for i in range(255):
    EXP[i]=x; LOG[x]=i; x<<=1
    if x&0x100: x^=0x11D
for i in range(255,512): EXP[i]=EXP[i-255]
def raw_modules(v):
    r=(16*v+128)*v+64
    if v>=2:
        n=v//7+2; r-=(25*n-10)*n-55
        if v>=7: r-=36
    return r
def align(v):
    if v==1: return []
    n=v//7+2; step=-(-(v*4+4)//(n*2-2))*2
    pos=[6]+[0]*(n-1)
    p=v*4+10
    for i in range(n-1,0,-1): pos[i]=p; p-=step
    return pos
def func_mask(v):
    s=v*4+17; f=[[False]*s for _ in range(s)]
    for i in range(s): f[6][i]=f[i][6]=True
    for (cx,cy) in ((3,3),(s-4,3),(3,s-4)):
        for dy in range(-4,5):
            for dx in range(-4,5):
                if 0<=cx+dx<s and 0<=cy+dy<s: f[cy+dy][cx+dx]=True
    pos=align(v); n=len(pos)
    for i in range(n):
        for j in range(n):
            if (i==0 and j==0) or (i==0 and j==n-1) or (i==n-1 and j==0): continue
            for dy in range(-2,3):
                for dx in range(-2,3): f[pos[j]+dy][pos[i]+dx]=True
    for i in range(9):
        f[8][i]=f[i][8]=True
    for i in range(8): f[8][s-1-i]=True; f[s-1-i][8]=True
    f[s-8][8]=True
    if v>=7:
        for i in range(6):
            for j in range(3): f[i][s-11+j]=True; f[s-11+j][i]=True
    return f
MASKS=[lambda x,y:(x+y)%2==0,lambda x,y:y%2==0,lambda x,y:x%3==0,lambda x,y:(x+y)%3==0,
 lambda x,y:(x//3+y//2)%2==0,lambda x,y:x*y%2+x*y%3==0,lambda x,y:(x*y%2+x*y%3)%2==0,lambda x,y:((x+y)%2+x*y%3)%2==0]
def bch_ok(bits15):
    bits15^=0x5412
    data=bits15>>10; rem=data
    for _ in range(10): rem=(rem<<1)^((rem>>9)*0x537)
    return ((data<<10)|rem)==bits15
def decode(m):
    s=len(m); v=(s-17)//4
    # format info, copy 1 read per spec: bits 0-5 -> (col 8,row i); 6 -> (8,7); 7->(8,8); 8->(7,8); 9..14 -> (14-i, 8)
    g=lambda x,y: 1 if m[y][x] else 0
    bits=sum(g(8,i)<<i for i in range(6))|g(8,7)<<6|g(8,8)<<7|g(7,8)<<8
    for i in range(9,15): bits|=g(14-i,8)<<i
    # copy 2
    b2=sum(g(s-1-i,8)<<i for i in range(8))|sum(g(8,s-15+i)<<i for i in range(8,15))
    assert bits==b2, "format copies differ"
    assert bch_ok(bits), "format BCH"
    assert (bits^0x5412)>>13==0, "EC level not M"   # M = 00
    mask=((bits^0x5412)>>10)&7
    f=func_mask(v)
    assert m[s-8][8], "dark module"
    out=[]; up=True; x=s-1
    while x>0:
        if x==6: x-=1
        ys=range(s-1,-1,-1) if up else range(s)
        for y in ys:
            for dx in (0,1):
                xx=x-dx
                if not f[y][xx]:
                    bit=m[y][xx]^MASKS[mask](xx,y); out.append(1 if bit else 0)
        up=not up; x-=2
    raw=raw_modules(v)//8
    cw=[sum(out[i*8+j]<<(7-j) for j in range(8)) for i in range(raw)]
    nb=NB[v]; ecl=EPC[v]; short=nb-raw%nb; slen=raw//nb
    blocks=[[] for _ in range(nb)]; k=0
    for i in range(slen+1):
        for b in range(nb):
            if i==slen-ecl and b<short: pass_=True
            if i==slen-ecl:
                pass
        # emulate interleave order
    # de-interleave properly
    lens=[slen-ecl+(0 if b<short else 1) for b in range(nb)]
    idx=0
    for i in range(max(lens)):
        for b in range(nb):
            if i<lens[b]: blocks[b].append(cw[idx]); idx+=1
    for i in range(ecl):
        for b in range(nb): blocks[b].append(cw[idx]); idx+=1
    assert idx==raw
    data=[]
    for b in blocks:
        for j in range(ecl):  # syndromes
            sv=0
            for c in b: sv=(EXP[(LOG[sv]+j)%255] if sv else 0)^c
            assert sv==0, f"RS syndrome {j} nonzero"
        data+=b[:len(b)-ecl]
    bitstr=''.join(f'{c:08b}' for c in data)
    assert bitstr[:4]=='0100'
    cc=8 if v<=9 else 16
    n=int(bitstr[4:4+cc],2); body=bitstr[4+cc:4+cc+8*n]
    return v, bytes(int(body[i:i+8],2) for i in range(0,len(body),8)).decode()
random.seed(1); fails=0; seen=set()
texts=["otpauth://totp/StockAI:ana%40example.com?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=StockAI&algorithm=SHA1&digits=6&period=30","a","HELLO WORLD","Ã±andÃº Ã¼ â‚¬"]
for n in (5,14,26,42,62,84,106,122,152,180,213): texts.append(''.join(random.choice(string.ascii_letters+string.digits+'/:?&=%') for _ in range(n)))
for t in texts:
    out=subprocess.run(['node','--experimental-strip-types',os.path.join(os.path.dirname(os.path.abspath(__file__)),'qr-dump.mjs'),t],capture_output=True,text=True)
    d=json.loads(out.stdout); m=[[c=='1' for c in r] for r in d['rows']]
    try:
        v,txt=decode(m); ok=txt==t; seen.add(v)
        print('v',v,'OK' if ok else 'MISMATCH',len(t.encode()))
        fails+= (not ok)
    except AssertionError as e:
        print('FAIL',len(t.encode()),e); fails+=1
print('versions covered',sorted(seen),'fails',fails)
sys.exit(1 if fails else 0)
