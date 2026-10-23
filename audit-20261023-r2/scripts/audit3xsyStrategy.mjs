import fs from "node:fs";

const input = process.argv[2];
const out = process.argv[3] ?? "data/3xsy-strategy-audit-20260721";
if (!input) throw new Error("usage: node scripts/audit3xsyStrategy.mjs INPUT_JSON [OUTPUT_BASE]");
const data = JSON.parse(fs.readFileSync(input, "utf8"));
const rows = data.rows.filter((r) => Number.isFinite(r.sourceExitRoiPct));

const avg = (a) => a.reduce((s, x) => s + x, 0) / Math.max(1, a.length);
const median = (a) => { const x = [...a].sort((p,q)=>p-q); return x.length ? (x.length%2 ? x[(x.length-1)/2] : (x[x.length/2-1]+x[x.length/2])/2) : null; };
const quantile = (a,p) => { const x=[...a].sort((u,v)=>u-v); return x.length ? x[Math.min(x.length-1,Math.floor((x.length-1)*p))] : null; };
function stats(label, xs) {
  const wins=xs.filter(r=>r.realizedPnlSol>0), losses=xs.filter(r=>r.realizedPnlSol<0);
  const gp=wins.reduce((s,r)=>s+r.realizedPnlSol,0), gl=-losses.reduce((s,r)=>s+r.realizedPnlSol,0);
  const rois=xs.map(r=>r.sourceExitRoiPct), pnl=xs.reduce((s,r)=>s+r.realizedPnlSol,0);
  const trimLo=quantile(rois,.01), trimHi=quantile(rois,.99);
  const trimmed=rois.filter(x=>x>=trimLo&&x<=trimHi);
  return {label,n:xs.length,pnlSol:pnl,winPct:100*wins.length/Math.max(1,xs.length),avgRoiPct:avg(rois),medianRoiPct:median(rois),pf:gl?gp/gl:null,trimmedAvgRoiPct:avg(trimmed),medianHoldSec:60*median(xs.map(r=>r.holdMinutes)),medianBuySol:median(xs.map(r=>r.firstBuySol))};
}
function buckets(defs){return defs.map(([label,test])=>stats(label,rows.filter(test)));}
const mcDefs=[
  ["<10k",r=>r.entryMarketCapUsd<1e4],["10-15k",r=>r.entryMarketCapUsd>=1e4&&r.entryMarketCapUsd<1.5e4],
  ["15-20k",r=>r.entryMarketCapUsd>=1.5e4&&r.entryMarketCapUsd<2e4],["20-30k",r=>r.entryMarketCapUsd>=2e4&&r.entryMarketCapUsd<3e4],
  ["30-40k",r=>r.entryMarketCapUsd>=3e4&&r.entryMarketCapUsd<4e4],["40-50k",r=>r.entryMarketCapUsd>=4e4&&r.entryMarketCapUsd<5e4],
  ["50-75k",r=>r.entryMarketCapUsd>=5e4&&r.entryMarketCapUsd<7.5e4],["75-100k",r=>r.entryMarketCapUsd>=7.5e4&&r.entryMarketCapUsd<1e5],
  ["100-150k",r=>r.entryMarketCapUsd>=1e5&&r.entryMarketCapUsd<1.5e5],["150k+",r=>r.entryMarketCapUsd>=1.5e5]
];
const holdDefs=[["<10s",r=>r.holdMinutes<1/6],["10-20s",r=>r.holdMinutes>=1/6&&r.holdMinutes<1/3],["20-40s",r=>r.holdMinutes>=1/3&&r.holdMinutes<2/3],["40-60s",r=>r.holdMinutes>=2/3&&r.holdMinutes<1],["1-3m",r=>r.holdMinutes>=1&&r.holdMinutes<3],["3m+",r=>r.holdMinutes>=3]];
const sizeDefs=[["<0.5",r=>r.firstBuySol<.5],["0.5-0.8",r=>r.firstBuySol>=.5&&r.firstBuySol<.8],["0.8-1.0",r=>r.firstBuySol>=.8&&r.firstBuySol<1],["1.0-1.25",r=>r.firstBuySol>=1&&r.firstBuySol<1.25],["1.25+",r=>r.firstBuySol>=1.25]];
const days=[...new Set(rows.map(r=>r.entryIso.slice(0,10)))].sort();
const daily=days.map(d=>stats(d,rows.filter(r=>r.entryIso.startsWith(d))));
const hours=Array.from({length:24},(_,h)=>stats(`${String(h).padStart(2,"0")}:00`,rows.filter(r=>new Date(r.entryIso).getUTCHours()===h)));
const thresholds=[];
for(const lo of [0,10000,15000,20000,30000,40000,50000,75000,100000]) for(const hi of [20000,30000,40000,50000,75000,100000,150000,Infinity]) if(hi>lo){
  const s=stats(`${lo/1000}k-${hi===Infinity?"inf":hi/1000+"k"}`,rows.filter(r=>r.entryMarketCapUsd>=lo&&r.entryMarketCapUsd<hi));
  if(s.n>=200) thresholds.push(s);
}
thresholds.sort((a,b)=>(b.trimmedAvgRoiPct-a.trimmedAvgRoiPct)||(b.n-a.n));
const output={coverage:data.coverage,all:stats("all",rows),pump:stats("pump",rows.filter(r=>r.route==="pump")),other:stats("other",rows.filter(r=>r.route!=="pump")),marketCap:buckets(mcDefs),hold:buckets(holdDefs),size:buckets(sizeDefs),daily,hours,topThresholdRanges:thresholds.slice(0,12)};
const f=x=>Number.isFinite(x)?x.toFixed(2):"-";
const table=(title,xs)=>[`## ${title}`,"","| Bucket | n | PnL SOL | Win | Avg ROI | Median ROI | PF | 1-99% avg ROI | Med hold | Med buy |","|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",...xs.map(x=>`| ${x.label} | ${x.n} | ${x.pnlSol>=0?"+":""}${f(x.pnlSol)} | ${f(x.winPct)}% | ${x.avgRoiPct>=0?"+":""}${f(x.avgRoiPct)}% | ${x.medianRoiPct>=0?"+":""}${f(x.medianRoiPct)}% | ${f(x.pf)} | ${x.trimmedAvgRoiPct>=0?"+":""}${f(x.trimmedAvgRoiPct)}% | ${f(x.medianHoldSec)}s | ${f(x.medianBuySol)} |`),""] .join("\n");
const md=["# 3XSY Strategy Audit","",`Generated: ${new Date().toISOString()}`,`Input: \`${input}\``,"",table("Overview",[output.all,output.pump,output.other]),table("Market-cap buckets",output.marketCap),table("Hold-time buckets",output.hold),table("First-buy size buckets",output.size),table("Daily stability",output.daily),table("UTC hour",output.hours),table("Best MC ranges by 1-99% trimmed mean",output.topThresholdRanges),"## Notes","","- Entry market cap is first-fill price × assumed 1B supply.","- ROI/PnL represent the source wallet's realized exits, before follower latency and follower-specific execution drag.","- The 1-99% trimmed mean removes the most extreme 1% of winners and losers within each bucket.",""] .join("\n");
fs.writeFileSync(`${out}.json`,JSON.stringify(output,null,2));
fs.writeFileSync(`${out}.md`,md);
console.log(JSON.stringify({out:`${out}.md`,rows:rows.length,best:output.topThresholdRanges[0]},null,2));
