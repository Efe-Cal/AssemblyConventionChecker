// Rasterize the original vector mark with Node's standard library.
import {writeFileSync} from 'node:fs';
import {deflateSync} from 'node:zlib';
const size = 256;
const segments = [[82,54,39,128],[39,128,82,202],[174,54,217,128],[217,128,174,202],[95,129,122,156],[122,156,170,103]];
const distance = (x,y,[a,b,c,d]) => {
  const t = Math.max(0, Math.min(1, ((x-a)*(c-a)+(y-b)*(d-b))/((c-a)**2+(d-b)**2)));
  return Math.hypot(x-a-t*(c-a), y-b-t*(d-b));
};
const raw = Buffer.alloc(size * (size*4+1));
for (let y=0;y<size;y++) for (let x=0;x<size;x++) {
  const dx = Math.max(50-x,0,x-205), dy = Math.max(50-y,0,y-205);
  const background = Math.hypot(dx,dy) <= 34;
  let rgb = [17+Math.round((255-y)*.025),24+Math.round((255-y)*.025),35+Math.round((255-y)*.035)];
  let ink = 0;
  for (let i=0;i<segments.length;i++) {
    const alpha = Math.max(0,Math.min(1,5.5-distance(x+.5,y+.5,segments[i])));
    if (alpha > ink) {
      const color = i<4 ? [124,183,239] : [199,223,206];
      rgb = rgb.map((c,j)=>Math.round(c*(1-alpha)+color[j]*alpha)); ink = alpha;
    }
  }
  const offset = y*(size*4+1)+1+x*4;
  raw.set([...rgb,background?255:0],offset);
}
const crc = data => {let value=0xffffffff;for(const byte of data){value^=byte;for(let i=0;i<8;i++)value=(value>>>1)^((value&1)?0xedb88320:0);}return (value^0xffffffff)>>>0;};
const chunk = (name,data) => {const type=Buffer.from(name);const length=Buffer.alloc(4);length.writeUInt32BE(data.length);const checksum=Buffer.alloc(4);checksum.writeUInt32BE(crc(Buffer.concat([type,data])));return Buffer.concat([length,type,data,checksum]);};
const header = Buffer.alloc(13);header.writeUInt32BE(size,0);header.writeUInt32BE(size,4);header[8]=8;header[9]=6;
writeFileSync(new URL('../media/icon.png',import.meta.url),Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',header),chunk('IDAT',deflateSync(raw)),chunk('IEND',Buffer.alloc(0))]));
