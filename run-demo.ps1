param([switch]$OpenReport, [int]$MaxSteps = 24)
$ErrorActionPreference = 'Stop'

# Prefer an existing Python. The bundled fallback makes this machine usable
# even when its system python command is an unconfigured version-manager shim.
$candidates = @()
foreach ($commandName in @('python', 'python3')) {
    $command = Get-Command $commandName -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
}
$candidates += Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$demoPython = $null
foreach ($candidate in ($candidates | Select-Object -Unique)) {
    if (Test-Path -LiteralPath $candidate) {
        try {
            & $candidate -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { $demoPython = $candidate; break }
        } catch { }
    }
}
if (-not $demoPython) { throw 'Python 3.10+ is required. Install it separately, then run: python mvp/run_demo.py --open' }
$demoArguments = @((Join-Path $PSScriptRoot 'mvp\run_demo.py'), '--max-steps', $MaxSteps)
if ($OpenReport) { $demoArguments += '--open' }
& $demoPython @demoArguments
exit $LASTEXITCODE
