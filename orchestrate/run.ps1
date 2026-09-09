<#
.SYNOPSIS
  Step-at-a-time orchestrator for the production test-and-fix run. See PLAN.md.

.DESCRIPTION
  Launches one headless Claude Code worker per step and waits for it to EXIT -- process
  exit is the completion signal, which is the entire reason this is a script and not a
  session talking to another session.

  Nothing here makes a judgement call. Gates are: a file exists, ruff is clean, pytest is
  green, a suite that was green is still green. The one judgement in the run is yours, at
  the triage gate between `test` and `fix`.

.EXAMPLE
  .\orchestrate\run.ps1 baseline
  .\orchestrate\run.ps1 inventory
  .\orchestrate\run.ps1 test -CredsFile "$env:TEMP\qa-creds.txt"
  .\orchestrate\run.ps1 merge
  .\orchestrate\run.ps1 fix
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet('baseline', 'inventory', 'test', 'merge', 'fix', 'verify', 'status')]
    [string] $Step,

    # Env file holding the three production QA logins. Never in the repo. See PLAN.md.
    [string] $CredsFile,

    # Re-run a single test slice (1..3) instead of all three.
    [ValidateRange(1, 3)]
    [int] $Slice = 0,

    # Print the resolved worker invocation and exit without launching anything.
    [switch] $DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Root      = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$Here      = Join-Path $Root 'orchestrate'
$Prompts   = Join-Path $Here 'prompts'
$Findings  = Join-Path $Here 'findings'
$State     = Join-Path $Here 'state'
$Logs      = Join-Path $Here 'logs'

foreach ($d in @($Findings, $State, $Logs)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}

# --- slices -------------------------------------------------------------------------
# Disjoint by construction. Each slice gets its own account and its own Playwright
# storage-state file, so three concurrent logins cannot invalidate each other.
$Slices = @(
    [pscustomobject]@{ Id = 1; Name = 'contacts-conversations'
                       Role = 'DISPATCHER'; CredPrefix = 'GHL_QA_DISPATCHER'
                       Surfaces = 'Contacts (list, search, filters, create dialog, edit, tags), Conversations (list, thread view, compose, notes), and the shared Contact Details panel used by both' }
    [pscustomobject]@{ Id = 2; Name = 'opportunities-calendars'
                       Role = 'TECH'; CredPrefix = 'GHL_QA_TECH'
                       Surfaces = 'Opportunities (pipeline board, drag-and-drop between stages, create, edit, win/lose, value), Calendars (month/week views, appointment create/edit, reschedule)' }
    [pscustomobject]@{ Id = 3; Name = 'auth-roles-reporting'
                       Role = 'ADMIN'; CredPrefix = 'GHL_QA_ADMIN'
                       Surfaces = 'Login/logout, session expiry and refresh, role permissions (what DISPATCHER and TECH may not do), Settings, Reporting, Dashboard, Launchpad, the app shell/sidebar present on every view, and cross-cutting behaviour: scroll containers, empty states, responsive layout, direct-URL navigation, browser back/forward' }
)

# --- helpers ------------------------------------------------------------------------

function Write-Head([string] $m) { Write-Host "`n=== $m" -ForegroundColor Cyan }
function Write-Warn([string] $m) { Write-Host "!!  $m" -ForegroundColor Yellow }
function Write-Fail([string] $m) { Write-Host "!!  $m" -ForegroundColor Red }
function Write-Ok  ([string] $m) { Write-Host "ok  $m" -ForegroundColor Green }

function Import-Creds {
    param([string] $Path)
    if (-not $Path) {
        throw "this step needs -CredsFile <path to the QA env file>. It lives outside the repo; see PLAN.md."
    }
    if (-not (Test-Path $Path)) { throw "creds file not found: $Path" }

    $loaded = @()
    foreach ($line in Get-Content -LiteralPath $Path) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#')) { continue }
        $i = $t.IndexOf('=')
        if ($i -lt 1) { continue }
        $k = $t.Substring(0, $i).Trim()
        $v = $t.Substring($i + 1).Trim()
        [Environment]::SetEnvironmentVariable($k, $v, 'Process')
        $loaded += $k
    }
    # Report names only. Never echo a value.
    Write-Ok ("credentials loaded: {0}" -f ($loaded -join ', '))
}

function Resolve-Prompt {
    <# Reads a prompt template and substitutes {{PLACEHOLDER}} tokens.
       Credentials are NEVER substituted -- prompts reference env var NAMES, because
       prompt text becomes transcript text. #>
    param([string] $File, [hashtable] $Vars = @{})

    $path = Join-Path $Prompts $File
    if (-not (Test-Path $path)) { throw "prompt template missing: $path" }
    $text = Get-Content -LiteralPath $path -Raw

    foreach ($k in $Vars.Keys) { $text = $text.Replace("{{$k}}", [string]$Vars[$k]) }

    $left = [regex]::Matches($text, '\{\{([A-Z0-9_]+)\}\}') | ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique
    if ($left) { throw "prompt $File has unsubstituted placeholders: $($left -join ', ')" }
    return $text
}

function Invoke-Worker {
    <# Runs one headless worker to completion. Returns the parsed JSON result.
       Waits on process exit; does not poll, does not guess. #>
    param(
        [Parameter(Mandatory)] [string] $Prompt,
        [Parameter(Mandatory)] [string] $Label,
        [hashtable] $Env = @{}
    )

    $promptFile = Join-Path $State "$Label.prompt.txt"
    $outFile    = Join-Path $Logs  "$Label.json"
    $errFile    = Join-Path $Logs  "$Label.stderr.txt"
    Set-Content -LiteralPath $promptFile -Value $Prompt -Encoding UTF8

    if ($DryRun) {
        Write-Host "would run worker '$Label' with prompt $promptFile"
        return $null
    }

    foreach ($k in $Env.Keys) { [Environment]::SetEnvironmentVariable($k, $Env[$k], 'Process') }

    Write-Host "  -> worker '$Label' running (waiting for exit; this is not quick)..."
    $sw = [Diagnostics.Stopwatch]::StartNew()

    # The prompt arrives on STDIN, not as an argument. Passing it in -ArgumentList means
    # Windows argument splitting mangles a multi-line prompt -- the first run of this
    # script delivered the single word "Produce" and the worker had no idea what was
    # wanted. `claude -p` with no prompt argument reads it from stdin, whole.
    # --add-dir is deliberately omitted: the worker works in the repo and nowhere else.
    # Not named $args: that is an automatic variable inside a function.
    $claudeArgs = @(
        '-p'
        '--output-format', 'json'
        '--dangerously-skip-permissions'
    )
    $p = Start-Process -FilePath 'claude' -ArgumentList $claudeArgs -WorkingDirectory $Root `
                       -NoNewWindow -Wait -PassThru `
                       -RedirectStandardInput $promptFile `
                       -RedirectStandardOutput $outFile -RedirectStandardError $errFile
    $sw.Stop()

    if ($p.ExitCode -ne 0) {
        Write-Fail "worker '$Label' exited $($p.ExitCode) after $([int]$sw.Elapsed.TotalSeconds)s. stderr: $errFile"
    } else {
        Write-Ok "worker '$Label' finished in $([int]$sw.Elapsed.TotalSeconds)s"
    }

    $result = $null
    try {
        $raw = Get-Content -LiteralPath $outFile -Raw
        if ($raw) { $result = $raw | ConvertFrom-Json }
    } catch {
        Write-Warn "could not parse worker output as JSON: $outFile"
    }

    if ($result -and $result.PSObject.Properties.Name -contains 'session_id') {
        # Recorded so you can `claude --resume <id>` and interrogate the step by hand.
        Set-Content -LiteralPath (Join-Path $State "$Label.session") -Value $result.session_id
        Write-Host "     session: $($result.session_id)"
    }
    if ($result -and $result.PSObject.Properties.Name -contains 'is_error' -and $result.is_error) {
        Write-Fail "worker '$Label' reported is_error=true"
    }
    return $result
}

# --- gates --------------------------------------------------------------------------

function Invoke-Native {
    <# Runs an external command, returns @{ Text; Lines; ExitCode }.

       Native commands must go through here. With $ErrorActionPreference = 'Stop', any
       tool that writes to stderr (npm and uv both do, routinely, on success) becomes a
       TERMINATING error the moment its stream is merged with 2>&1 -- and PowerShell 5.1
       reports it as "cannot find property Statement", which points at nothing. A gate
       runner must distinguish "the tool printed to stderr" from "the tool failed", and
       the exit code is the only thing that actually says so. #>
    param(
        [Parameter(Mandatory)] [string] $File,
        [string[]] $Arguments = @(),
        [string] $WorkDir
    )
    # Implemented with Start-Process and redirect files rather than the call operator:
    # under Set-StrictMode -Version Latest, PowerShell 5.1 throws PropertyNotFoundStrict
    # ("cannot find property Statement") on `& npm ... 2>&1`, and neither
    # $ErrorActionPreference nor a try/catch prevents it. A separate process with its
    # streams on disk sidesteps the interaction and gives a reliable exit code.
    if (-not $WorkDir) { $WorkDir = (Get-Location).Path }
    $stdout = [IO.Path]::GetTempFileName()
    $stderr = [IO.Path]::GetTempFileName()
    # cmd /c so .cmd shims (npm) and PATH lookups resolve as they do in a shell.
    $line = (@($File) + $Arguments) -join ' '
    try {
        $p = Start-Process -FilePath $env:ComSpec -ArgumentList @('/c', $line) `
                           -WorkingDirectory $WorkDir -NoNewWindow -Wait -PassThru `
                           -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $o = if (Test-Path $stdout) { Get-Content -LiteralPath $stdout -Raw } else { '' }
        $e = if (Test-Path $stderr) { Get-Content -LiteralPath $stderr -Raw } else { '' }
        $text = (@($o, $e) | Where-Object { $_ }) -join "`n"
        return @{ Text = $text; Lines = ($text -split "`r?`n"); ExitCode = $p.ExitCode }
    } finally {
        Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
    }
}

function Test-Gates {
    <# Runs every mechanical gate and returns a result object. Never throws on a red
       gate -- the caller decides what a red gate means. #>
    $r = [ordered]@{ at = (Get-Date).ToString('s') }

    # Output is captured and written with Set-Content, never with `*> (Join-Path ...)`:
    # PowerShell 5.1 will not accept a parenthesised expression as a redirection target.
    Write-Host '  ruff...'
    $g = Invoke-Native 'uv' @('run', 'ruff', 'check', '.') $Root
    $g.Text | Set-Content -LiteralPath (Join-Path $Logs 'gate-ruff.txt')
    $r.ruff = if ($g.ExitCode -eq 0) { 'clean' } else { 'FAILED' }

    Write-Host '  pytest...'
    $g = Invoke-Native 'uv' @('run', 'pytest', '-q') $Root
    $g.Text | Set-Content -LiteralPath (Join-Path $Logs 'gate-pytest.txt')
    $r.pytest_exit = $g.ExitCode
    $m = [regex]::Match($g.Text, '(\d+)\s+passed')
    $r.pytest_passed = if ($m.Success) { [int]$m.Groups[1].Value } else { $null }
    $m = [regex]::Match($g.Text, '(\d+)\s+failed')
    $r.pytest_failed = if ($m.Success) { [int]$m.Groups[1].Value } else { 0 }

    Write-Host '  frontend build...'
    $g = Invoke-Native 'npm' @('run', 'build') (Join-Path $Root 'frontend')
    $g.Text | Set-Content -LiteralPath (Join-Path $Logs 'gate-frontend.txt')
    $r.frontend_build = if ($g.ExitCode -eq 0) { 'ok' } else { 'FAILED' }

    # capture/diff*.py need references/, which is gitignored and machine-local. Probe
    # rather than assume: a gate that cannot run is recorded as skipped, never as passing.
    foreach ($pair in @(@('diff', 'capture/diff.py'), @('diff_panel', 'capture/diff_panel.py'))) {
        $key = $pair[0]; $script = $pair[1]
        Write-Host "  $script..."
        try {
            $g = Invoke-Native 'uv' @('run', 'python', $script) $Root
            $g.Text | Set-Content -LiteralPath (Join-Path $Logs "gate-$key.txt")
            if ($g.ExitCode -ne 0 -and $g.Text -match 'references|No such file|FileNotFound|Errno 2') {
                $r[$key] = 'skipped (references/ not available on this machine)'
            } else {
                # These scripts report "N properties match, M differ" -- record both
                # numbers, not a bare 'ok'. A gate whose value cannot change cannot
                # detect a regression, which is worse than having no gate at all.
                $m = [regex]::Match($g.Text, '(\d+)\s+properties\s+match,\s+(\d+)\s+differ')
                $r[$key] = if ($m.Success) { "$($m.Groups[1].Value) match / $($m.Groups[2].Value) differ" }
                           elseif ($g.ExitCode -eq 0) { 'ok (unparsed)' } else { 'FAILED' }
            }
        } catch {
            $r[$key] = 'skipped (could not run)'
        }
    }

    return [pscustomobject]$r
}

function Show-Gates($g) {
    foreach ($p in $g.PSObject.Properties) { Write-Host ("    {0,-16} {1}" -f $p.Name, $p.Value) }
}

# --- steps --------------------------------------------------------------------------

function Step-Baseline {
    Write-Head 'baseline — recording what is green BEFORE anything changes'
    $g = Test-Gates
    Show-Gates $g
    $g | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $State 'baseline.json')
    Write-Ok "written: orchestrate/state/baseline.json"
    if ($g.ruff -ne 'clean' -or $g.pytest_exit -ne 0 -or $g.frontend_build -ne 'ok') {
        Write-Warn 'the baseline is NOT fully green. Fix that first, or the hard-stop in `fix` has nothing to compare against.'
    }
}

function Step-Inventory {
    Write-Head 'inventory — one worker, produces the test-surface contract'
    $prompt = Resolve-Prompt 'step1-inventory.md' @{ OUT = 'orchestrate/findings/inventory.md' }
    Invoke-Worker -Prompt $prompt -Label 'inventory' | Out-Null

    $out = Join-Path $Findings 'inventory.md'
    if (-not (Test-Path $out)) {
        Write-Fail 'GATE FAILED: orchestrate/findings/inventory.md was not written. Do not advance to `test`.'
        exit 1
    }
    Write-Ok "gate passed: inventory.md exists ($((Get-Item $out).Length) bytes)"
}

function Step-Test {
    Write-Head 'test — parallel workers against PRODUCTION'
    Import-Creds $CredsFile

    $inv = Join-Path $Findings 'inventory.md'
    if (-not (Test-Path $inv)) { throw 'run `inventory` first: the testers take their surface list from findings/inventory.md' }

    $base = [Environment]::GetEnvironmentVariable('GHL_QA_BASE_URL')
    if (-not $base) { throw 'GHL_QA_BASE_URL is not set in the creds file' }
    Write-Host "  target: $base"

    $todo = if ($Slice -gt 0) { $Slices | Where-Object Id -eq $Slice } else { $Slices }

    $jobs = @()
    foreach ($s in $todo) {
        $emailVar = "$($s.CredPrefix)_EMAIL"
        $passVar  = "$($s.CredPrefix)_PASSWORD"
        if (-not [Environment]::GetEnvironmentVariable($emailVar)) { throw "$emailVar is not set in the creds file" }

        $prompt = Resolve-Prompt 'step2-test-slice.md' @{
            SLICE_ID       = $s.Id
            SLICE_NAME     = $s.Name
            SLICE_SURFACES = $s.Surfaces
            ROLE           = $s.Role
            EMAIL_VAR      = $emailVar
            PASSWORD_VAR   = $passVar
            STATE_FILE     = ".capture-profile/qa_auth_slice$($s.Id).json"
            OUT            = "orchestrate/findings/slice-$($s.Id).md"
        }

        if ($DryRun) {
            $pf = Join-Path $State "slice-$($s.Id).prompt.txt"
            Set-Content -LiteralPath $pf -Value $prompt -Encoding UTF8
            Write-Host "  -- slice $($s.Id) ($($s.Name), as $($s.Role)) would run; prompt: $pf"
            continue
        }

        # Each worker is a separate process, so each gets its own browser, its own
        # storage state and its own login. Launched together, waited on together.
        $jobs += Start-Job -Name "slice$($s.Id)" -ScriptBlock {
            param($root, $here, $prompt, $label, $logs, $state, $envMap)
            foreach ($k in $envMap.Keys) { [Environment]::SetEnvironmentVariable($k, $envMap[$k], 'Process') }
            $pf  = Join-Path $state "$label.prompt.txt"
            Set-Content -LiteralPath $pf -Value $prompt -Encoding UTF8
            $out = Join-Path $logs "$label.json"
            $err = Join-Path $logs "$label.stderr.txt"
            # Prompt on stdin, never as an argument -- see Invoke-Worker.
            $p = Start-Process -FilePath 'claude' `
                    -ArgumentList @('-p', '--output-format', 'json', '--dangerously-skip-permissions') `
                    -WorkingDirectory $root -NoNewWindow -Wait -PassThru `
                    -RedirectStandardInput $pf `
                    -RedirectStandardOutput $out -RedirectStandardError $err
            [pscustomobject]@{ Label = $label; ExitCode = $p.ExitCode; Out = $out }
        } -ArgumentList $Root, $Here, $prompt, "slice-$($s.Id)", $Logs, $State, @{
            GHL_QA_BASE_URL = $base
            "$emailVar"     = [Environment]::GetEnvironmentVariable($emailVar)
            "$passVar"      = [Environment]::GetEnvironmentVariable($passVar)
        }

        Write-Host "  -> slice $($s.Id) ($($s.Name), as $($s.Role)) launched"
    }

    if ($DryRun) { Write-Host "`ndry run: nothing launched. Prompts written to orchestrate/state/."; return }

    Write-Host "`n  waiting for $($jobs.Count) worker(s) to exit..."
    $jobs | Wait-Job | Out-Null
    foreach ($j in $jobs) {
        $res = Receive-Job $j
        if ($res -and $res.ExitCode -eq 0) { Write-Ok "$($res.Label) exited 0" }
        else { Write-Fail "$($j.Name) did not exit cleanly — see orchestrate/logs/" }
    }
    $jobs | Remove-Job -Force

    Write-Head 'gate — every slice must have written its findings file'
    $missing = @()
    foreach ($s in $todo) {
        $f = Join-Path $Findings "slice-$($s.Id).md"
        if (Test-Path $f) { Write-Ok "slice-$($s.Id).md ($((Get-Item $f).Length) bytes)" }
        else { Write-Fail "slice-$($s.Id).md MISSING"; $missing += $s.Id }
    }
    if ($missing) {
        Write-Fail "re-run the missing slice(s) with -Slice N. Do not advance."
        exit 1
    }
    Write-Warn 'CLEANUP: read each findings file for cleanup failures before you do anything else. A failed restore on production is the most urgent thing in the file.'
}

function Step-Merge {
    Write-Head 'merge — collate the slices for your triage'
    $files = Get-ChildItem $Findings -Filter 'slice-*.md' | Sort-Object Name
    if (-not $files) { throw 'no slice findings to merge; run `test` first' }

    $merged = Join-Path $Findings 'merged.md'
    $sb = [Text.StringBuilder]::new()
    [void]$sb.AppendLine('# Merged findings — TRIAGE THIS BEFORE RUNNING `fix`')
    [void]$sb.AppendLine()
    [void]$sb.AppendLine('The fix worker reads ONLY the `bug` section. Move anything that is not a real')
    [void]$sb.AppendLine('bug into the environment section, or delete it. An unattended bypass-mode worker')
    [void]$sb.AppendLine('will otherwise rewrite working code to satisfy an imaginary bug.')
    [void]$sb.AppendLine()
    foreach ($f in $files) {
        [void]$sb.AppendLine("---`n")
        [void]$sb.AppendLine("## from $($f.Name)`n")
        # ReadAllText with an explicit UTF-8 decode. Get-Content -Raw on PowerShell 5.1
        # decodes as the ANSI codepage, so an em dash written by a worker came back as
        # mojibake and was then re-encoded as UTF-8 -- corrupting every finding the fix
        # worker would read.
        [void]$sb.AppendLine([IO.File]::ReadAllText($f.FullName, [Text.Encoding]::UTF8))
    }
    [IO.File]::WriteAllText($merged, $sb.ToString(), (New-Object Text.UTF8Encoding($false)))
    Write-Ok "written: orchestrate/findings/merged.md"

    $raw = Get-Content -LiteralPath $merged -Raw
    foreach ($k in @('bug', 'environment', 'uncertain')) {
        $n = ([regex]::Matches($raw, "(?im)^\s*[-*]?\s*classification:\s*$k\b")).Count
        Write-Host ("    {0,-12} {1}" -f $k, $n)
    }
    Write-Warn 'YOUR GATE: read merged.md now. `fix` acts on what you leave in it.'
}

function Step-Fix {
    Write-Head 'fix — one worker, branch only, no deploy'
    $merged = Join-Path $Findings 'merged.md'
    if (-not (Test-Path $merged)) { throw 'run `merge` first, then triage findings/merged.md' }

    $baselineFile = Join-Path $State 'baseline.json'
    if (-not (Test-Path $baselineFile)) { throw 'no baseline recorded. Run `baseline` first or the hard-stop has nothing to compare against.' }
    $baseline = Get-Content -LiteralPath $baselineFile -Raw | ConvertFrom-Json
    Write-Host '  baseline:'; Show-Gates $baseline

    $branch = "fix/prod-findings-$(Get-Date -Format 'yyyyMMdd')"
    $prompt = Resolve-Prompt 'step3-fix.md' @{
        MERGED = 'orchestrate/findings/merged.md'
        LOG    = 'orchestrate/findings/fix-log.md'
        BRANCH = $branch
    }
    Invoke-Worker -Prompt $prompt -Label 'fix' | Out-Null

    Write-Head 'gate — did the fixes break a green baseline?'
    $after = Test-Gates
    Show-Gates $after
    $after | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $State 'after-fix.json')

    $regressed = @()
    if ($baseline.ruff -eq 'clean'      -and $after.ruff -ne 'clean')          { $regressed += 'ruff' }
    if ($baseline.pytest_exit -eq 0     -and $after.pytest_exit -ne 0)         { $regressed += 'pytest' }
    if ($baseline.frontend_build -eq 'ok' -and $after.frontend_build -ne 'ok') { $regressed += 'frontend build' }
    foreach ($k in @('diff', 'diff_panel')) {
        if ($baseline.$k -notmatch 'skipped' -and $baseline.$k -ne $after.$k) { $regressed += "capture/$k" }
    }

    if ($regressed) {
        Write-Fail "HARD STOP: these were green before the fixes and are not now: $($regressed -join ', ')"
        Write-Fail 'Do not run `verify`, do not deploy. Inspect the branch by hand.'
        exit 1
    }
    Write-Ok 'baseline held. Review the branch, then deploy, then run `verify`.'
    Write-Warn "nothing has been deployed. The worker committed to $branch and cannot push or ssh."
}

function Step-Verify {
    Write-Head 'verify — production re-test AFTER you have deployed the branch'
    Write-Warn 'This only means anything if the fixes are actually live. Have you deployed? (ssh owen-main; ./deploy.sh)'
    $ans = Read-Host '  type DEPLOYED to continue'
    if ($ans -ne 'DEPLOYED') { Write-Host 'aborted.'; return }

    Import-Creds $CredsFile
    $log = Join-Path $Findings 'fix-log.md'
    if (-not (Test-Path $log)) { throw 'no findings/fix-log.md — run `fix` first' }

    $base = [Environment]::GetEnvironmentVariable('GHL_QA_BASE_URL')
    $prompt = Resolve-Prompt 'step4-verify.md' @{
        LOG      = 'orchestrate/findings/fix-log.md'
        OUT      = 'orchestrate/findings/verify-report.md'
        BASE_VAR = 'GHL_QA_BASE_URL'
        EMAIL_VAR = 'GHL_QA_ADMIN_EMAIL'
        PASSWORD_VAR = 'GHL_QA_ADMIN_PASSWORD'
    }
    Invoke-Worker -Prompt $prompt -Label 'verify' -Env @{ GHL_QA_BASE_URL = $base } | Out-Null

    $out = Join-Path $Findings 'verify-report.md'
    if (Test-Path $out) { Write-Ok 'written: orchestrate/findings/verify-report.md' }
    else { Write-Fail 'no verify-report.md was written' }
}

function Step-Status {
    Write-Head 'status'
    foreach ($f in @('inventory.md', 'slice-1.md', 'slice-2.md', 'slice-3.md', 'merged.md', 'fix-log.md', 'verify-report.md')) {
        $p = Join-Path $Findings $f
        if (Test-Path $p) { Write-Ok ("{0,-20} {1,8} bytes  {2}" -f $f, (Get-Item $p).Length, (Get-Item $p).LastWriteTime) }
        else { Write-Host ("--  {0,-20} not present" -f $f) }
    }
    $b = Join-Path $State 'baseline.json'
    if (Test-Path $b) {
        Write-Host "`n  baseline:"; Show-Gates (Get-Content -LiteralPath $b -Raw | ConvertFrom-Json)
    } else { Write-Warn 'no baseline recorded yet — run `baseline`' }

    Get-ChildItem $State -Filter '*.session' -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host ("  session {0,-12} {1}" -f $_.BaseName, (Get-Content -LiteralPath $_.FullName -Raw).Trim())
    }
}

# --- dispatch -----------------------------------------------------------------------

switch ($Step) {
    'baseline'  { Step-Baseline }
    'inventory' { Step-Inventory }
    'test'      { Step-Test }
    'merge'     { Step-Merge }
    'fix'       { Step-Fix }
    'verify'    { Step-Verify }
    'status'    { Step-Status }
}
