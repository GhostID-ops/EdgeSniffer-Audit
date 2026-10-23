import "dotenv/config";

const key=process.env.HELIUS_API_KEY??process.env.HELIUS_API_KEY_1??"";
if(!key)throw new Error("missing Helius API key");
const wallets=process.argv.slice(2);if(!wallets.length)throw new Error("usage: node scripts/auditWalletRecency.mjs WALLET...");
const now=Math.floor(Date.now()/1000),days=d=>now-d*86400;
async function get(u,wallet){for(let attempt=0;attempt<6;attempt++){const res=await fetch(u);if(res.ok)return res.json();if(res.status!==429)throw new Error(`${wallet.slice(0,6)} status ${res.status}`);await new Promise(r=>setTimeout(r,1000*2**attempt));}throw new Error(`${wallet.slice(0,6)} status 429 after retries`);}
async function audit(wallet){let before,txs=[];for(let page=0;page<10;page++){const u=new URL(`https://api.helius.xyz/v0/addresses/${wallet}/transactions`);u.searchParams.set("api-key",key);u.searchParams.set("limit","100");if(before)u.searchParams.set("before",before);const batch=await get(u,wallet);if(!batch.length)break;txs.push(...batch);before=batch.at(-1)?.signature;if((batch.at(-1)?.timestamp??now)<days(30))break;await new Promise(r=>setTimeout(r,300));}const swaps=txs.filter(x=>x.type==="SWAP");const counts=d=>swaps.filter(x=>(x.timestamp??0)>=days(d)).length;return{wallet,latestTransactionAt:txs[0]?.timestamp?new Date(txs[0].timestamp*1000).toISOString():null,latestSwapAt:swaps[0]?.timestamp?new Date(swaps[0].timestamp*1000).toISOString():null,transactionsFetched:txs.length,swaps24h:counts(1),swaps7d:counts(7),swaps30d:counts(30),active7d:counts(7)>0};}
const out=[];for(const w of wallets){try{out.push(await audit(w));}catch(e){out.push({wallet:w,error:e instanceof Error?e.message:String(e)});}}
console.log(JSON.stringify({generatedAt:new Date().toISOString(),wallets:out},null,2));
