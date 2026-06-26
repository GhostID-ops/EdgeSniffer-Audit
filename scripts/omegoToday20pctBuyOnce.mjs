import fs from "node:fs";
const IN = process.argv[2] || "data/path-wallet-audit-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq.json";
const COPY = Number(process.argv[3] ?? 20);          // % of Omego's buy
const data = JSON.parse(fs.readFileSync(IN, "utf8"));
const rows = (data.rows || []).filter(r => Number.isFinite(r.sourceBuySol) && r.sourceBuySol > 0);
const pct = (a,b)=>((b-a)/a)*100;
const avg = xs=>{const f=xs.filter(Number.isFinite);return f.reduce((s,x)=>s+x,0)/Math.max(1,f.length);};
const med = xs=>{const f=xs.filter(Number.isFinite).sort((a,b)=>a-b);return f.length?(f.length%2?f[(f.length-1)/2]:(f[f.length/2-1]+f[f.length/2])/2):null;};

// independent own-exit on the candle path: SL + trailing steps + TP, else horizon close
function ownExit(row, {sl=null, steps=[], tp=null}) {
  const c = Array.isArray(row.candles)?row.candles:[];
  const entry=c[0]?.open;
  if(!entry||!c.length) return Number.isFinite(row.sourceExitPct)?row.sourceExitPct:0;
  let best=0, floor=null;
  for(const k of c){
    const lo=pct(entry,k.low), hi=pct(entry,k.high);
    best=Math.max(best,hi);
    for(const s of steps) if(best>=s.peak) floor=floor==null?s.floor:Math.max(floor,s.floor);
    if(sl!=null && lo<=-sl) return -sl;
    if(floor!=null && lo<=floor) return floor;
    if(tp!=null && hi>=tp) return tp;
  }
  return pct(entry, c.at(-1).close);
}
const steps = "20:5,35:15,50:25,75:45,100:70,150:110,250:180".split(",").map(s=>{const[p,f]=s.split(":").map(Number);return{peak:p,floor:f};});

function summary(label, perToken /* fn(row)->exitPct */, caps=null) {
  let sorted = rows.filter(r=>Number.isFinite(r.entryTs)).sort((a,b)=>a.entryTs-b.entryTs);
  const open=[]; const trades=[]; let maxExpo=0, deployed=0;
  for(const r of sorted){
    for(let i=open.length-1;i>=0;i--) if(open[i].exitTs<=r.entryTs) open.splice(i,1);
    const size = r.sourceBuySol*(COPY/100);
    if(caps){
      const expo=open.reduce((s,t)=>s+t.size,0);
      if(caps.maxOpen!=null && open.length>=caps.maxOpen) continue;
      if(caps.maxExpo!=null && expo+size>caps.maxExpo) continue;
    }
    const exitPct = perToken(r);
    const pnl = size*(exitPct/100);
    const exitTs = (r.candles?.at(-1)?.ts) ?? r.entryTs;
    const t={size,exitPct,pnl,exitTs}; trades.push(t); open.push(t); deployed+=size;
    maxExpo=Math.max(maxExpo, open.reduce((s,x)=>s+x.size,0));
  }
  const pnls=trades.map(t=>t.pnl), rois=trades.map(t=>t.exitPct);
  const wins=trades.filter(t=>t.pnl>0).length;
  console.log(`${label.padEnd(46)} tokens=${String(trades.length).padStart(3)} deployed=${deployed.toFixed(3)} PnL=${(pnls.reduce((s,x)=>s+x,0)>=0?"+":"")}${pnls.reduce((s,x)=>s+x,0).toFixed(3)} avgROI=${avg(rois).toFixed(1)}% medROI=${(med(rois)??0).toFixed(1)}% win=${(100*wins/Math.max(1,trades.length)).toFixed(0)}% maxExpo=${maxExpo.toFixed(2)}`);
}

console.log(`Input: ${IN}`);
console.log(`Omego buy-once candidate mints today (has buy + data): ${rows.length}`);
console.log(`Copy size: ${COPY}% of his buy\n`);
console.log("== Buy-once, 20% copy, NO caps (participate in every mint he entered) ==");
summary("A) Mirror Omego's exit", r=>Number.isFinite(r.sourceExitPct)?r.sourceExitPct:0);
summary("B) Own exit (SL25 + trailing, ignore his sell)", r=>ownExit(r,{sl:25,steps,tp:null}));
// #4 runner: core mirrors his exit; runner targets TP80 w/ SL55 protection
for(const runner of [0.10,0.20,0.25]){
  summary(`C) Runner ${(runner*100)|0}% (core mirror + runner TP80/SL55)`, r=>{
    const core=Number.isFinite(r.sourceExitPct)?r.sourceExitPct:0;
    const run=ownExit(r,{sl:55,steps:[],tp:80});
    return (1-runner)*core + runner*run;
  });
}
console.log("\n== Same, but capped (maxOpen=5, maxExposure=5 SOL) for realism ==");
summary("A) Mirror exit (capped)", r=>Number.isFinite(r.sourceExitPct)?r.sourceExitPct:0, {maxOpen:5,maxExpo:5});
summary("C) Runner 20% (capped)", r=>{const core=Number.isFinite(r.sourceExitPct)?r.sourceExitPct:0;return 0.8*core+0.2*ownExit(r,{sl:55,steps:[],tp:80});}, {maxOpen:5,maxExpo:5});
