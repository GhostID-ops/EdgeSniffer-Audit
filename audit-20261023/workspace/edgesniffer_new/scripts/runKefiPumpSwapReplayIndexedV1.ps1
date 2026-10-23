param(
  [string]$WslRoot = '/home/franky/edgesniffer-kefi-full-meta-v1',
  [int]$MaxWorkers = 2
)

$ErrorActionPreference = 'Stop'
$out = "$WslRoot/operations/kefi-prospective-discovery-v1"
$bin = "$WslRoot/bin/kefi_prospective_discovery_v2"
$parts = '03','04','05','06','07','08','09','10','11','12'

foreach ($part in $parts) {
  $summary = "$out/discovery-v2-$part-summary.json"
  if (-not (wsl.exe -u root --exec test -s $summary)) {
    $args = @('-u','root','--exec',$bin,'--discover',$WslRoot,
      "$out/discovery-v2-$part.jsonl",$summary,"2026/10/05/$part")
    Start-Process -WindowStyle Hidden -FilePath 'wsl.exe' -ArgumentList $args
    while ((wsl.exe -u root --exec pgrep -f 'kefi_prospective_discovery_v2' | Measure-Object).Count -ge $MaxWorkers) {
      Start-Sleep -Seconds 5
    }
  }
}

do {
  $missing = @($parts | Where-Object { -not (wsl.exe -u root --exec test -s "$out/discovery-v2-$_-summary.json") })
  if ($missing.Count -gt 0) { Start-Sleep -Seconds 10 }
} while ($missing.Count -gt 0)

wsl.exe -u root --exec python3 -c "import json,glob; out='$out'; rows=[]; [rows.extend(json.load(open(p)) if False else []) for p in []]"
Write-Output 'PARTITION_INDEX_READY'
