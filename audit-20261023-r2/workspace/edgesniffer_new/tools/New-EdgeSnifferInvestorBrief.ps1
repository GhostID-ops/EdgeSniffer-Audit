param(
    [string]$OutputPath = (Join-Path (Get-Location) 'EdgeSniffer_Technical_Investor_Brief.docx')
)

$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Escape-Xml([string]$Value) {
    return [System.Security.SecurityElement]::Escape($Value)
}

function Paragraph([string]$Text, [string]$Style = 'Body') {
    $escaped = Escape-Xml $Text
    switch ($Style) {
        'Title' { return "<w:p><w:pPr><w:pStyle w:val=`"Title`"/></w:pPr><w:r><w:t>$escaped</w:t></w:r></w:p>" }
        'Subtitle' { return "<w:p><w:pPr><w:pStyle w:val=`"Subtitle`"/></w:pPr><w:r><w:t>$escaped</w:t></w:r></w:p>" }
        'H1' { return "<w:p><w:pPr><w:pStyle w:val=`"Heading1`"/></w:pPr><w:r><w:t>$escaped</w:t></w:r></w:p>" }
        'H2' { return "<w:p><w:pPr><w:pStyle w:val=`"Heading2`"/></w:pPr><w:r><w:t>$escaped</w:t></w:r></w:p>" }
        'Bullet' { return "<w:p><w:pPr><w:ind w:left=`"720`" w:hanging=`"360`"/></w:pPr><w:r><w:t>• $escaped</w:t></w:r></w:p>" }
        'Note' { return "<w:p><w:pPr><w:pStyle w:val=`"Note`"/></w:pPr><w:r><w:t>$escaped</w:t></w:r></w:p>" }
        'PageBreak' { return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>' }
        default { return "<w:p><w:r><w:t xml:space=`"preserve`">$escaped</w:t></w:r></w:p>" }
    }
}

function Table([object[]]$Rows) {
    $xml = '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders><w:top w:val="single" w:sz="6" w:color="9EADBA"/><w:left w:val="single" w:sz="6" w:color="9EADBA"/><w:bottom w:val="single" w:sz="6" w:color="9EADBA"/><w:right w:val="single" w:sz="6" w:color="9EADBA"/><w:insideH w:val="single" w:sz="4" w:color="D9E2F3"/><w:insideV w:val="single" w:sz="4" w:color="D9E2F3"/></w:tblBorders></w:tblPr>'
    foreach ($row in $Rows) {
        $xml += '<w:tr>'
        foreach ($cell in $row) {
            $escaped = Escape-Xml ([string]$cell)
            $xml += "<w:tc><w:tcPr><w:tcW w:w=`"2600`" w:type=`"dxa`"/></w:tcPr><w:p><w:r><w:t xml:space=`"preserve`">$escaped</w:t></w:r></w:p></w:tc>"
        }
        $xml += '</w:tr>'
    }
    return $xml + '</w:tbl>'
}

$blocks = @(
    @{ s='Title'; t='EdgeSniffer' },
    @{ s='Subtitle'; t='Technical Diligence Memorandum | Solana Market Intelligence and Execution Research' },
    @{ s='Note'; t='Confidential | Prepared for technical investor review | October 2026' },
    @{ s='Body'; t='Purpose: provide a rigorous technical account of what EdgeSniffer has built, what our experiments have established, and which claims remain intentionally unmade.' },
    @{ s='PageBreak'; t='' },

    @{ s='H1'; t='1. Investment Thesis' },
    @{ s='Body'; t='EdgeSniffer is building an evidence-first market-intelligence and execution system for the fastest, least structured segment of Solana trading: newly created Pump.fun and PumpSwap pools. The commercial thesis is that the durable advantage is not a single indicator or a copy-trading alert. It is the ability to convert raw public chain activity into a causally correct, low-latency and execution-aware decision process before the opportunity becomes obvious to the broader market.' },
    @{ s='Body'; t='The platform was built to answer the questions a serious quantitative trading system has to answer: What was publicly observable before the decision? Was the candidate universe complete enough to make a comparison meaningful? Was transaction order real rather than provider arrival order? Could the modeled trade actually have been filled after fees, slippage, priority costs and latency? And does the edge survive outside the research sample?' },
    @{ s='Body'; t='Our answer is a production-oriented stack with strict scientific gates. The engineering layer is designed to run continuously; strategy promotion is deliberately slower. That distinction is an asset, not a caveat: it prevents us from presenting wallet similarity, a favorable backtest, or a lucky fill as a deployable autonomous strategy.' },
    @{ s='H2'; t='What has already been demonstrated' },
    @{ s='Table'; rows=@(
        @('Question', 'Current answer', 'What it does not yet prove'),
        @('Can the system reconstruct a sophisticated wallet’s public decision footprint?', 'Yes. kEFi research produced and froze a 13-feature strict pre-entry fingerprint baseline, with materially higher match frequency on the observed target lane than broad market controls.', 'That every component is public, causal, or independently tradable.'),
        @('Can we collect and verify high-speed Solana market evidence?', 'Yes. Canonical State reconciles streamed Pump/PumpSwap identities against finalized block data and attaches immutable identity, path, creation and continuity receipts.', 'Complete historical market coverage or an investable autonomous cohort.'),
        @('Can the platform execute venue-native paths?', 'Yes at the infrastructure layer: Rust-first local execution, local signing, pool-layout-aware handling, bounded risk controls and no aggregator dependency in the hot path.', 'Positive live economics at scale.'),
        @('Can we claim a deployable autonomous strategy today?', 'Not yet. The autonomous model is in prospective evidence collection and evaluation-gate mode.', 'Any return, Sharpe, capacity, or live-PnL claim.')
    ) },

    @{ s='H1'; t='2. The kEFi Reverse-Engineering Result' },
    @{ s='Body'; t='The initial research question was deliberately narrow: can an external observer distinguish moments when a sophisticated wallet buys from contemporaneous moments when it does not, using only public data available before the wallet’s action? The answer so far is yes, with an important qualification: a “fingerprint” is not automatically the wallet’s full strategy and is not automatically a tradable signal.' },
    @{ s='Body'; t='We identified and froze a 13-feature strict pre-entry fingerprint baseline for the kEFi lane. The exact feature weights and thresholds are proprietary. At a high level, the feature families include short-horizon buy and sell flow, net flow asymmetry, transaction-burst structure, pool/liquidity regime, market-cap regime, pool-age and activity timing, reserve dynamics, and feature-validity state. The freezing step matters: it prevents us from repeatedly changing the rule after seeing later outcomes.' },
    @{ s='Body'; t='In the first fresh target evaluation, the frozen fingerprint matched 99 of 150 observed kEFi buy decisions, or 66%. In a broad contemporaneous control universe, 83 of 1,977 opportunities matched, or 4.20%. Read correctly, this does not mean we “bought the same token only 4.2% of the time.” It means the frozen public-state pattern was rare in the broad market while appearing frequently before kEFi entries. That is the selection signal we wanted to establish.' },
    @{ s='Body'; t='The result is encouraging because it demonstrates discrimination: the system is not merely finding common market noise. It is not yet an economic result. A pattern may be predictive of a wallet’s action while being reactive to that wallet, dependent on private wallet state, too late for an outsider, or unprofitable after costs. That is why EdgeSniffer separates selection proof, actionability proof, and economic proof.' },
    @{ s='H2'; t='What we believe the 13-feature baseline is detecting' },
    @{ s='Bullet'; t='A narrow public microstructure regime rather than generic “high volume.”' },
    @{ s='Bullet'; t='A particular combination of recent directional flow, liquidity/reserve state, and pool maturity.' },
    @{ s='Bullet'; t='Timing structure: not simply that a token is active, but that activity is occurring in a specific early regime.' },
    @{ s='Bullet'; t='A distinction between first-observed selections and later adds/re-entries, which must not be conflated.' },
    @{ s='Bullet'; t='A mix of public pre-action information and possible wallet-private position-management behavior; the latter is explicitly being separated rather than assumed copyable.' },

    @{ s='H1'; t='3. Why the Architecture Is Different from a Typical Bot' },
    @{ s='Body'; t='Most retail systems are notification pipelines with a transaction sender attached: listen to an API, apply a few filters, and buy. EdgeSniffer is built as an evidence system with an execution plane. The architecture is intentionally more demanding because the main risk in this market is false certainty: a feed may be delayed, a transaction may be reordered, a pool may be misidentified, or a favorable simulated exit may be impossible to realize.' },
    @{ s='H2'; t='Data plane' },
    @{ s='Body'; t='Yellowstone gRPC and related Solana stream sources provide low-latency observations. The hot receive loop only receives, validates, sequences and enqueues. Heavy work such as pool resolution, materialization, replay, evaluation and reporting is separated and resource-limited so it cannot backpressure the source invisibly.' },
    @{ s='H2'; t='Canonical evidence plane' },
    @{ s='Body'; t='Canonical State V1 records compact filtered transaction identities before any wallet or candidate selection. Those identities are reconciled to finalized getBlock data. Relevant transactions carry finalized signature/index proof, authoritative outer/inner instruction paths, pool/mint identity, source/continuity provenance, and hash-bound receipts. A source delivery order is never treated as canonical Solana ordering.' },
    @{ s='H2'; t='State and decision plane' },
    @{ s='Body'; t='Pool state, candidate eligibility, and F01-F13-style pre-decision features are built from the same canonical sequence. Every feature has a timestamp and a validity/missingness mask. Unknown history stays unknown. The decision system includes an explicit no-trade option and complete denominator accounting rather than selecting a token simply because the system is running.' },
    @{ s='H2'; t='Execution plane' },
    @{ s='Body'; t='The intended live posture is Rust-only buy and sell execution, local signing, local market-cap/filter inference, ShredStream/gRPC telemetry and canonical venue layouts. Jupiter fallback is disabled. We avoid introducing HTTP calls into the hot path. Execution feasibility is a first-class model input: pool transition semantics, fees, slippage, priority costs, no-fills, failed transactions and exit uncertainty are represented explicitly.' },

    @{ s='H1'; t='4. The Reliability and Integrity Model' },
    @{ s='Body'; t='Continuous market capture is valuable only if it is scientifically honest. A feed reconnect, provider replay, disk threshold, process restart or hypervisor shutdown must never leave a plausible-looking but bridged cohort. EdgeSniffer therefore fails closed.' },
    @{ s='Table'; rows=@(
        @('Failure mode', 'System response', 'Why it matters'),
        @('Yellowstone reconnect', 'Require a verified 32-slot replay overlap before accepting new progress.', 'Prevents a quiet source gap from contaminating a cohort.'),
        @('Finalized identity mismatch', 'Quarantine the slot; do not release canonical state.', 'A matching count is not enough; signatures and finalized indexes must reconcile.'),
        @('Ambiguous instruction path / unsupported layout', 'Reject semantic emission and preserve diagnostic evidence.', 'Avoids inventing an event order or pool identity.'),
        @('Process TERM or host shutdown', 'Write a durable abort receipt; mark the forward session VOIDED; prohibit bridging.', 'Protects scientific continuity even when availability is interrupted.'),
        @('Resource pressure', 'Hard RAM, disk, RPC and queue bounds; fail closed before protected storage is consumed.', 'Prevents evidence loss from a system that “keeps running” past safe capacity.')
    ) },
    @{ s='Body'; t='Recent operations validated the distinction in practice. A provider-stream reconnect replayed successfully and was accepted only after overlap verification. A hypervisor-initiated VPS shutdown was recognized as external, not incorrectly blamed on the data pipeline. The running collector now records shutdown interruption as an explicit invalidation rather than attempting to resume the same scientific session.' },

    @{ s='H1'; t='5. From Fingerprint to Autonomous Opportunity Model' },
    @{ s='Body'; t='Teacher-wallet work is a discovery tool, not the endpoint. We classify explanatory variables into four buckets: predictive-before-wallet public information; reactive-following information visible only after the wallet acts; wallet-private/internal state reconstructed from wallet history; and permanently unobservable private information. Only the first category can support an autonomous pre-entry model. The second can support a separate follower strategy if latency permits. The remaining categories cannot honestly be sold as copyable public alpha.' },
    @{ s='Body'; t='The Autonomous Opportunity Model (AOM) is the next product-level research layer. It will scan the market-wide eligible universe independently of kEFi or 2CQ timing, compute public analogues of the frozen state features, rank opportunities transparently, and evaluate fixed 30/60/120-second horizons using a bounded execution simulator. The simulation includes fees, price impact, entry delay, priority cost, failed sends, no-fill behavior and unknown exits. It is intentionally separate from the wallet-selection result.' },
    @{ s='H2'; t='What must happen before capital is scaled' },
    @{ s='Bullet'; t='Finish a bounded forward-only canonical capture with an intact evidence chain.' },
    @{ s='Bullet'; t='Admit only partitions with verified coverage, canonical order, state validity and universe proof appropriate to the cohort.' },
    @{ s='Bullet'; t='Freeze one autonomous entry/risk/exit contract before outcomes are inspected.' },
    @{ s='Bullet'; t='Demonstrate economics versus contemporaneous, context-matched controls after costs and cluster-aware uncertainty.' },
    @{ s='Bullet'; t='Demonstrate paper-to-live execution parity in a separate dust-size calibration wallet with strict session and loss limits.' },

    @{ s='H1'; t='6. Existing Results and How to Read Them' },
    @{ s='Body'; t='Internal 2CQ paper research provides a second, independent source of encouraging evidence. The current core paper profile has recorded positive paper checkpoints under a fixed runner-oriented low-market-cap entry definition. The latest clean internal checkpoint referenced approximately 500 closes, +17.20 SOL modeled PnL and profit factor 1.59. These figures are research telemetry, not audited performance, not live fills, and not a solicitation. They remain vulnerable to selection drift, implementation assumptions, and live execution friction until the full promotion sequence is complete.' },
    @{ s='Body'; t='This is exactly why EdgeSniffer’s process is valuable: it does not promote a profitable-looking paper profile directly into a production-sized bot. Each profile remains named, isolated and falsifiable. Dead-hour filtering, for example, was tested and rejected when it removed profitable activity from the current 2CQ sample. That is the behavior we want from the research loop: preserve good ideas, discard attractive but unsupported ones, and keep the evidence trail.' },

    @{ s='H1'; t='7. Technical Questions an Investor Should Ask' },
    @{ s='H2'; t='“Do you have complete market coverage?”' },
    @{ s='Body'; t='We state coverage precisely. A finalized reconciled slot proves a bounded transaction identity result; it does not by itself prove a complete historical eligible-pool universe. Our forward-only cohort makes a narrower, defensible claim: pools created after a declared boundary with continuous verified observation. We will not silently generalize that into full historical completeness.' },
    @{ s='H2'; t='“Could the kEFi fingerprint simply include kEFi’s own impact?”' },
    @{ s='Body'; t='That is one of the central risks, and it is why timing is explicit. The research distinguishes state before kEFi’s transaction from state only visible after it. Features contaminated by the wallet’s own update cannot support a predictive-before-wallet claim.' },
    @{ s='H2'; t='“What prevents overfitting?”' },
    @{ s='Body'; t='Feature specifications and thresholds are frozen before fresh evaluation. Controls are contemporaneous and matched on pre-entry context. The system separates development data, prospective paper cohorts and future holdouts; it does not use PnL to tune a selection fingerprint retroactively.' },
    @{ s='H2'; t='“What is defensible about the execution stack?”' },
    @{ s='Body'; t='Execution is not delegated to a generic aggregator path. The platform uses local Rust components, canonical venue state where available, bounded order logic and explicit lifecycle telemetry. It is built to answer whether a modeled entry and exit could actually happen, not merely whether the chart moved favorably.' },

    @{ s='H1'; t='8. Milestones and Investor-Relevant De-Risking' },
    @{ s='Table'; rows=@(
        @('Milestone', 'Decision unlocked', 'Status'),
        @('Wallet decision reconstruction and 13-feature baseline', 'Public selection discrimination can be measured.', 'Completed / frozen baseline.'),
        @('Canonical finalized reconciliation and authoritative path evidence', 'Trustworthy forward market-state evidence can be captured.', 'Operational; bounded forward capture in progress.'),
        @('Forward-only new-pool cohort', 'Independent autonomous replay inputs become admissible.', 'Current critical path.'),
        @('AOM-V0 offline replay', 'Transparent market-wide selection and cost-aware simulation.', 'Next after cohort seal.'),
        @('Prospective paper economics', 'Decision on whether a strategy survives market controls and costs.', 'Blocked until fresh admissible inputs.'),
        @('Dust-size execution calibration', 'Real-world fill/latency parity and operational safety.', 'Only after positive paper economics.')
    ) },

    @{ s='H1'; t='9. Bottom Line' },
    @{ s='Body'; t='EdgeSniffer is not a black-box promise of effortless Solana alpha. It is a serious attempt to solve the actual hard problem: turn public, fast-moving blockchain microstructure into an auditable and executable decision system, then refuse to scale until the evidence supports it. The kEFi work has already shown that a sophisticated wallet’s public selection footprint can be detected with a frozen 13-feature baseline. The platform now has the canonical data and execution infrastructure needed to ask the more valuable question: which parts of that footprint can become an independent, profitable market-wide opportunity model?' },
    @{ s='Body'; t='That is the investable direction. The system’s value compounds through verified market coverage, reusable canonical state, hardened execution telemetry, and a research process that distinguishes signal discovery from investable evidence.' }
)

$body = ($blocks | ForEach-Object { if ($_.s -eq 'Table') { Table $_.rows } else { Paragraph $_.t $_.s } }) -join "`n"
$document = @"
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>$body<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080"/></w:sectPr></w:body></w:document>
"@

$styles = @'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Aptos" w:hAnsi="Aptos"/><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:rPr><w:b/><w:sz w:val="42"/><w:color w:val="17365D"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:rPr><w:sz w:val="26"/><w:color w:val="4F81BD"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:rPr><w:b/><w:sz w:val="30"/><w:color w:val="17365D"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:rPr><w:b/><w:sz w:val="24"/><w:color w:val="305496"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Note"><w:name w:val="Note"/><w:rPr><w:i/><w:color w:val="666666"/></w:rPr></w:style>
</w:styles>
'@

$contentTypes = @'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
'@
$relationships = @'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>
'@
$documentRelationships = @'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>
'@
$timestamp = [DateTime]::UtcNow.ToString('s') + 'Z'
$core = "<?xml version=`"1.0`" encoding=`"UTF-8`" standalone=`"yes`"?><cp:coreProperties xmlns:cp=`"http://schemas.openxmlformats.org/package/2006/metadata/core-properties`" xmlns:dc=`"http://purl.org/dc/elements/1.1/`" xmlns:dcterms=`"http://purl.org/dc/terms/`" xmlns:xsi=`"http://www.w3.org/2001/XMLSchema-instance`"><dc:title>EdgeSniffer Technical Architecture, Research Positioning, and Production Roadmap</dc:title><dc:creator>EdgeSniffer</dc:creator><dcterms:created xsi:type=`"dcterms:W3CDTF`">$timestamp</dcterms:created></cp:coreProperties>"
$app = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>EdgeSniffer</Application></Properties>'

if (Test-Path $OutputPath) { Remove-Item -LiteralPath $OutputPath -Force }
$stream = [System.IO.File]::Open($OutputPath, [System.IO.FileMode]::CreateNew)
try {
    $zip = [System.IO.Compression.ZipArchive]::new($stream, [System.IO.Compression.ZipArchiveMode]::Create, $false)
    $parts = @{
        '[Content_Types].xml' = $contentTypes
        '_rels/.rels' = $relationships
        'word/document.xml' = $document
        'word/styles.xml' = $styles
        'word/_rels/document.xml.rels' = $documentRelationships
        'docProps/core.xml' = $core
        'docProps/app.xml' = $app
    }
    foreach ($part in $parts.GetEnumerator()) {
        $entry = $zip.CreateEntry($part.Key)
        $writer = [System.IO.StreamWriter]::new($entry.Open(), [System.Text.UTF8Encoding]::new($false))
        try { $writer.Write($part.Value) } finally { $writer.Dispose() }
    }
} finally {
    if ($zip) { $zip.Dispose() }
    $stream.Dispose()
}

Write-Output $OutputPath
