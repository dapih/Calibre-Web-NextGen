# Minimal M0 containment proof. Runs only disposable copies of this helper.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$compiler = Join-Path $cache 'mingw64\bin\gcc.exe'
$archive = Join-Path $cache 'x86_64-16.2.0-release-posix-seh-ucrt-rt_v14-rev1.7z'
if (-not (Test-Path -LiteralPath $compiler) -or -not (Test-Path -LiteralPath $archive) -or (Get-FileHash -LiteralPath $archive).Hash -ne '26788998D615856A915E1EEACB0854FCE87BC608FA9A0C16F96BCBFDCF3415C0') { throw 'Verified private compiler prerequisite is missing.' }
$work = Join-Path $cache ('job-proof-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$exe = Join-Path $work 'job-proof.exe'
& $compiler -std=c11 -municode -static -O2 -Wall -Wextra -Werror -o $exe (Join-Path $PSScriptRoot 'windows-m0-job-proof.c')
if ($LASTEXITCODE -ne 0) { throw 'Job proof compilation failed.' }
$parent = Start-Process -FilePath $exe -ArgumentList @('parent', ('"' + $work + '"')) -WindowStyle Hidden -PassThru
try {
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    $workerMarker = Join-Path $work 'worker.txt'
    $childMarker = Join-Path $work 'grandchild.txt'
    $verifiedMarker = Join-Path $work 'verified.txt'
    while (-not (Test-Path -LiteralPath $verifiedMarker) -and [DateTime]::UtcNow -lt $deadline) {
        if ($parent.HasExited) { throw "Proof parent exited early: $($parent.ExitCode)" }
        Start-Sleep -Milliseconds 100
        $parent.Refresh()
    }
    if (-not (Test-Path -LiteralPath $verifiedMarker)) { throw 'Timed out waiting for verified job-member markers.' }
    $workerId = [int](Get-Content -LiteralPath $workerMarker -Encoding Unicode -Raw).Trim()
    $childId = [int](Get-Content -LiteralPath $childMarker -Encoding Unicode -Raw).Trim()
    $worker = Get-Process -Id $workerId -ErrorAction Stop
    $child = Get-Process -Id $childId -ErrorAction Stop
    $parent.Kill()
    $parent.WaitForExit()
    if (-not $worker.WaitForExit(10000) -or -not $child.WaitForExit(10000)) { throw 'An owned descendant did not terminate after parent death.' }
    Write-Output 'The parent verified its worker and grandchild in the created job; both exited after the parent was terminated.'
    Write-Output "Disposable proof evidence: $work"
} finally {
    $parent.Refresh()
    if (-not $parent.HasExited) { $parent.Kill(); $parent.WaitForExit() }
    $parent.Dispose()
}
