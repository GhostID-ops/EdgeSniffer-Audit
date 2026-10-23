export const EXIT_VALUATION_VERSION = "stale-safe-v2";
export function evaluateShadowExit(p, now, c) {
  const holdMs=Math.max(0,now-p.openedAtMs), markAgeMs=Math.max(0,now-p.lastMarkAtMs);
  const priced=Number.isFinite(p.lastRoiPct)&&Number.isFinite(p.lastMarketCapUsd)&&markAgeMs<=c.maxExitMarkAgeMs;
  if(priced&&p.lastRoiPct<=c.stopLossPct)return{reason:"stop_loss",priced,holdMs,markAgeMs};
  if(priced&&p.lastRoiPct>=c.takeProfitPct)return{reason:"take_profit",priced,holdMs,markAgeMs};
  if(priced&&p.bestRoiPct>=c.trailingStartPct&&p.lastRoiPct<=p.bestRoiPct-c.trailingGivebackPct)return{reason:"trailing_stop",priced,holdMs,markAgeMs};
  if(markAgeMs>=c.noUpdateExitMs)return{reason:"unpriced_no_update",priced:false,holdMs,markAgeMs};
  if(holdMs>=c.maxHoldMs)return{reason:priced?"time_exit":"unpriced_stale_exit",priced,holdMs,markAgeMs};
}
export function settleShadowExit(p,d,c){
  if(!d.priced)return{grossPnlSol:null,executionCostSol:null,netPnlSol:null};
  const grossPnlSol=c.paperBetSol*p.lastRoiPct/100;
  const executionCostSol=c.paperBetSol*c.entryCostBps/10000+Math.max(0,c.paperBetSol+grossPnlSol)*c.exitCostBps/10000+c.fixedRoundTripCostSol;
  return{grossPnlSol,executionCostSol,netPnlSol:grossPnlSol-executionCostSol};
}
