<#
    ops/pilot_useful_area_001/PilotKit.psm1
    DRONE-USEFUL-AREA-PILOT-001 -- guards shared by every script of the kit.

    Every refusal of this macro-stage lives HERE and nowhere else. A kit that
    repeats "is this production?" in six scripts will one day disagree with
    itself in one of the six -- and that will be the one holding the
    production path.

    Output is ASCII only: it is read in a PowerShell console and in an NSSM
    log, where the code page is not our guarantee.

    Nothing in this module contacts Vehicle Soft, production or staging. It
    inspects paths, services, git and files.
#>

Set-StrictMode -Version Latest

# --- Sites. One source of truth, mirrored in pilot_common.py -----------------
$script:ProductionRoot    = 'C:\transport-report'
$script:ProductionDb      = 'C:\transport-report\instance\transport.db'
$script:ProductionUrl     = 'http://10.103.25.14:5050'
$script:ProductionService = 'TransportReport'

$script:StagingRoot = 'C:\transport-report-staging'
$script:StagingDb   = 'C:\transport-report-staging\instance\transport.db'
$script:StagingUrl  = 'http://10.103.25.14:5051'

$script:CollectorRepo = 'C:\VehicleSoft_DJI_StageB_Pilot'
$script:CollectorPython = 'C:\VehicleSoft_DJI_StageB_Pilot\drone_collector\.venv\Scripts\python.exe'
$script:CollectorSession = 'C:\VehicleSoft_DJI_StageB_Pilot\drone_collector\data\storage_state.json'

$script:SystemPython = 'C:\Program Files\Python314\python.exe'

$script:VerifiedSha = 'c3e6a12ab95117710eeea5e05133f5cd548b698e'
$script:TargetDay   = '2026-06-05'
$script:MigrationId = 'DRONES_USEFUL_AREA_001'
$script:StagingHost = 'srv-yoqsh'
$script:CollectorHost = 'BAK-TEX11'

function Get-PilotConstants {
    [CmdletBinding()]
    param()
    return [ordered]@{
        ProductionRoot    = $script:ProductionRoot
        ProductionDb      = $script:ProductionDb
        ProductionUrl     = $script:ProductionUrl
        ProductionService = $script:ProductionService
        StagingRoot       = $script:StagingRoot
        StagingDb         = $script:StagingDb
        StagingUrl        = $script:StagingUrl
        CollectorRepo     = $script:CollectorRepo
        CollectorPython   = $script:CollectorPython
        CollectorSession  = $script:CollectorSession
        SystemPython      = $script:SystemPython
        VerifiedSha       = $script:VerifiedSha
        TargetDay         = $script:TargetDay
        MigrationId       = $script:MigrationId
        StagingHost       = $script:StagingHost
        CollectorHost     = $script:CollectorHost
    }
}

# --- Paths. The single most important guard of the kit -----------------------

function Get-PilotPathSegments {
    <#
        A path as comparable segments.

        [REASON]: comparing paths with -like or .StartsWith() is the same
        defect class as checking a variable name by substring, and here it
        costs more: 'C:\transport-report-staging' STARTS WITH
        'C:\transport-report'. A "never touch production" guard written with
        StartsWith would call staging production -- and, read the other way
        round, would call production staging.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Path)

    $text = $Path.Trim().Trim('"').Replace('/', '\').ToLowerInvariant()
    return @($text -split '\\' | Where-Object { $_ -ne '' })
}

function Test-PilotPathEquals {
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Left,
          [Parameter(Mandatory)][AllowEmptyString()][string]$Right)

    # [REASON]: PowerShell unrolls a ONE-element array on return, so
    # Get-PilotPathSegments('y') comes back as the string 'y' and .Count then
    # throws under Set-StrictMode. @() forces the array back.
    [string[]]$a = @(Get-PilotPathSegments -Path $Left)
    [string[]]$b = @(Get-PilotPathSegments -Path $Right)
    if ($a.Count -ne $b.Count) { return $false }
    for ($i = 0; $i -lt $a.Count; $i++) {
        if ($a[$i] -ne $b[$i]) { return $false }
    }
    return $true
}

function Test-PilotPathWithin {
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Path,
          [Parameter(Mandatory)][AllowEmptyString()][string]$Root)

    # [REASON]: same unrolling trap as in Test-PilotPathEquals. A service
    # ImagePath with no backslash is exactly the single-segment case.
    [string[]]$child  = @(Get-PilotPathSegments -Path $Path)
    [string[]]$parent = @(Get-PilotPathSegments -Path $Root)
    if ($parent.Count -eq 0) { return $false }
    if ($child.Count -lt $parent.Count) { return $false }
    for ($i = 0; $i -lt $parent.Count; $i++) {
        if ($child[$i] -ne $parent[$i]) { return $false }
    }
    return $true
}

function Test-PilotTouchesProduction {
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Path)
    return (Test-PilotPathWithin -Path $Path -Root $script:ProductionRoot)
}

function Assert-PilotNotProduction {
    <#
        Refuse any path that leads into the production checkout.
        Called by every script before it writes a single byte.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Path,
          [string]$What = 'path')

    if (Test-PilotTouchesProduction -Path $Path) {
        throw "REFUSED: the $What '$Path' is inside the production checkout $($script:ProductionRoot). This kit never writes to production."
    }
}

function Assert-PilotStagingPath {
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Path,
          [string]$What = 'path')

    Assert-PilotNotProduction -Path $Path -What $What
    if (-not (Test-PilotPathWithin -Path $Path -Root $script:StagingRoot)) {
        throw "REFUSED: the $What '$Path' is not inside the staging checkout $($script:StagingRoot)."
    }
}

# --- URLs --------------------------------------------------------------------

function Get-PilotUrlAuthority {
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Url)

    $text = $Url.Trim().TrimEnd('/').ToLowerInvariant()
    if ($text.Contains('://')) { $text = $text.Substring($text.IndexOf('://') + 3) }
    $slash = $text.IndexOf('/')
    if ($slash -ge 0) { $text = $text.Substring(0, $slash) }
    return $text
}

function Test-PilotUrlIsProduction {
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Url)
    return ((Get-PilotUrlAuthority -Url $Url) -eq (Get-PilotUrlAuthority -Url $script:ProductionUrl))
}

function Test-PilotUrlIsStaging {
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Url)
    return ((Get-PilotUrlAuthority -Url $Url) -eq (Get-PilotUrlAuthority -Url $script:StagingUrl))
}

function Assert-PilotStagingUrl {
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Url)

    if (Test-PilotUrlIsProduction -Url $Url) {
        throw "REFUSED: '$Url' is the production address. This kit never reaches production."
    }
    if (-not (Test-PilotUrlIsStaging -Url $Url)) {
        throw "REFUSED: '$Url' is not the staging address $($script:StagingUrl)."
    }
}

# --- Machine -----------------------------------------------------------------

function Assert-PilotHost {
    <#
        Refuse to run anywhere but the machine this script is written for.
        The found name is always printed: a refusal that does not say what it
        found sends the operator guessing.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Expected)

    $actual = $env:COMPUTERNAME
    if ([string]::IsNullOrWhiteSpace($actual)) {
        throw "REFUSED: COMPUTERNAME is empty, so the machine cannot be identified. Expected '$Expected'."
    }
    if ($actual.Trim().ToLowerInvariant() -ne $Expected.Trim().ToLowerInvariant()) {
        throw "REFUSED: this script only runs on '$Expected'. This machine is '$actual'."
    }
    Write-Output "HOST=$actual"
}

# --- Services ----------------------------------------------------------------

function Get-PilotServiceImagePath {
    <#
        Where a Windows service actually runs from.

        Two places are read, in this order: the NSSM AppDirectory (the project
        installs its services through NSSM) and the service ImagePath. Either
        one is enough to tell staging from production.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Name)

    $key = "HKLM:\SYSTEM\CurrentControlSet\Services\$Name"
    $imagePath = $null
    $appDirectory = $null
    $appPath = $null

    if (Test-Path -LiteralPath $key) {
        $item = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
        if ($item -and $item.PSObject.Properties.Name -contains 'ImagePath') {
            $imagePath = [string]$item.ImagePath
        }
    }
    $parameters = "$key\Parameters"
    if (Test-Path -LiteralPath $parameters) {
        $item = Get-ItemProperty -LiteralPath $parameters -ErrorAction SilentlyContinue
        if ($item) {
            if ($item.PSObject.Properties.Name -contains 'AppDirectory') {
                $appDirectory = [string]$item.AppDirectory
            }
            if ($item.PSObject.Properties.Name -contains 'Application') {
                $appPath = [string]$item.Application
            }
        }
    }
    return [ordered]@{
        Name         = $Name
        ImagePath    = $imagePath
        AppDirectory = $appDirectory
        Application  = $appPath
    }
}

function Select-PilotStagingService {
    <#
        Decide which of the candidate service names is the STAGING service.

        Pure function on purpose: it takes the candidates already resolved
        against the machine and returns a decision, so the rule can be tested
        without a Windows service.

        The rule: exactly one candidate whose AppDirectory (or ImagePath) is
        inside the staging root and NOT inside the production root, and whose
        name is not the production service name. Zero candidates and two
        candidates are both refusals -- guessing which of two services to stop
        is exactly the guess this kit must never make.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Candidates)

    $resolved = @()
    foreach ($candidate in $Candidates) {
        $name = [string]$candidate.Name
        if ($name.Trim().ToLowerInvariant() -eq $script:ProductionService.ToLowerInvariant()) { continue }

        $where = @()
        foreach ($field in 'AppDirectory', 'Application', 'ImagePath') {
            $value = $null
            if ($candidate.PSObject.Properties.Name -contains $field) {
                $value = [string]$candidate.$field
            }
            if (-not [string]::IsNullOrWhiteSpace($value)) { $where += $value }
        }
        if ($where.Count -eq 0) { continue }

        $insideStaging = $false
        $insideProduction = $false
        foreach ($value in $where) {
            $clean = $value.Trim('"')
            # An ImagePath is a command line: take the part that names a path.
            if ($clean.Contains($script:StagingRoot)) { $insideStaging = $true }
            if (Test-PilotPathWithin -Path $clean -Root $script:StagingRoot) { $insideStaging = $true }
            if (Test-PilotPathWithin -Path $clean -Root $script:ProductionRoot) { $insideProduction = $true }
        }
        # [REASON]: a candidate that names BOTH roots is not a staging service,
        # it is an unknown one. Stopping it on a guess is how a pilot takes
        # production down.
        if ($insideStaging -and -not $insideProduction) { $resolved += $candidate }
    }

    if ($resolved.Count -eq 0) {
        throw "REFUSED: no service could be resolved to the staging checkout $($script:StagingRoot). Nothing was stopped."
    }
    if ($resolved.Count -gt 1) {
        $names = ($resolved | ForEach-Object { $_.Name }) -join ', '
        throw "REFUSED: $($resolved.Count) services resolve to the staging checkout ($names). This kit never guesses which one to stop."
    }
    return $resolved[0]
}

function Resolve-PilotStagingService {
    <#
        The staging service name, taken from the REPOSITORY and then proven
        against this machine.

        Candidate names come from docs/ORG_WINDOWS_SERVER_STAGING_RUNBOOK.md
        (the "Service name" row) plus any service whose name starts with the
        production service name -- that is how the project names them. Each
        candidate is then checked against the registry: the one that actually
        runs from C:\transport-report-staging wins. A name that only appears
        in a document proves nothing.
    #>
    [CmdletBinding()]
    param([string]$RunbookPath)

    $names = New-Object System.Collections.Generic.List[string]
    if ($RunbookPath -and (Test-Path -LiteralPath $RunbookPath)) {
        foreach ($line in Get-Content -LiteralPath $RunbookPath -Encoding UTF8) {
            if ($line -match 'Service name.*`([A-Za-z0-9_]+)`') {
                # $Matches here is PowerShell's automatic capture of -match.
                $captured = $Matches[1]
                if (-not $names.Contains($captured)) { $names.Add($captured) | Out-Null }
            }
        }
    }
    foreach ($service in (Get-Service -ErrorAction SilentlyContinue)) {
        if ($service.Name -like "$($script:ProductionService)*") {
            if (-not $names.Contains($service.Name)) { $names.Add($service.Name) | Out-Null }
        }
    }

    $candidates = @()
    foreach ($name in $names) {
        $existing = Get-Service -Name $name -ErrorAction SilentlyContinue
        if (-not $existing) { continue }
        $candidates += (Get-PilotServiceImagePath -Name $name)
    }

    $chosen = Select-PilotStagingService -Candidates $candidates
    return [string]$chosen.Name
}

function Assert-PilotServiceIsNotProduction {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Name)

    if ($Name.Trim().ToLowerInvariant() -eq $script:ProductionService.ToLowerInvariant()) {
        throw "REFUSED: '$Name' is the PRODUCTION service. This kit never stops, starts or restarts it."
    }
}

# --- Git ---------------------------------------------------------------------

function Invoke-PilotGit {
    <#
        git, with the exit code checked. `git` prints to stderr on perfectly
        normal operations, so stderr alone is not a failure -- the code is.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Repo,
          [Parameter(Mandatory)][string[]]$Arguments,
          [switch]$AllowFailure)

    $output = & git -C $Repo @Arguments 2>&1
    $code = $LASTEXITCODE
    if ($code -ne 0 -and -not $AllowFailure) {
        throw "REFUSED: git $($Arguments -join ' ') failed with exit $code in $Repo`n$output"
    }
    return [ordered]@{ ExitCode = $code; Output = ($output | Out-String).Trim() }
}

function Assert-PilotWorktreeClean {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Repo)

    $status = Invoke-PilotGit -Repo $Repo -Arguments @('status', '--porcelain')
    if ($status.Output -ne '') {
        throw "REFUSED: the working tree in $Repo is not clean:`n$($status.Output)"
    }
    Write-Output "WORKTREE_CLEAN=$Repo"
}

function Get-PilotHeadSha {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Repo)
    return (Invoke-PilotGit -Repo $Repo -Arguments @('rev-parse', 'HEAD')).Output
}

function Assert-PilotHeadIsVerified {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Repo,
          [string]$Expected = $script:VerifiedSha)

    $head = Get-PilotHeadSha -Repo $Repo
    if ($head -ne $Expected) {
        throw "REFUSED: $Repo is at $head, not at the verified commit $Expected."
    }
    Write-Output "HEAD=$head"
}

# --- Files and evidence -------------------------------------------------------

function Get-PilotFileSha256 {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Write-PilotJson {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path,
          [Parameter(Mandatory)]$Value)

    $directory = Split-Path -Parent $Path
    if ($directory -and -not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    $json = $Value | ConvertTo-Json -Depth 12
    Set-Content -LiteralPath $Path -Value $json -Encoding utf8NoBOM
    return (Resolve-Path -LiteralPath $Path).Path
}

function Read-PilotJson {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "REFUSED: evidence file not found: $Path"
    }
    return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Invoke-PilotPython {
    <#
        Run a python script, capture stdout to a file, and check the exit code.
        stdout of the kit's probes is ONE JSON document, so it is captured
        whole rather than piped through a formatter.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Python,
          [Parameter(Mandatory)][string[]]$Arguments,
          [string]$StdoutPath,
          [int[]]$AllowedExitCodes = @(0))

    if ($StdoutPath) {
        $directory = Split-Path -Parent $StdoutPath
        if ($directory -and -not (Test-Path -LiteralPath $directory)) {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
        }
        & $Python @Arguments > $StdoutPath
    } else {
        & $Python @Arguments
    }
    $code = $LASTEXITCODE
    if ($AllowedExitCodes -notcontains $code) {
        throw "REFUSED: $Python $($Arguments -join ' ') exited $code (allowed: $($AllowedExitCodes -join ', '))."
    }
    return $code
}

Export-ModuleMember -Function Get-PilotConstants, Get-PilotPathSegments,
    Test-PilotPathEquals, Test-PilotPathWithin, Test-PilotTouchesProduction,
    Assert-PilotNotProduction, Assert-PilotStagingPath, Get-PilotUrlAuthority,
    Test-PilotUrlIsProduction, Test-PilotUrlIsStaging, Assert-PilotStagingUrl,
    Assert-PilotHost, Get-PilotServiceImagePath, Select-PilotStagingService,
    Resolve-PilotStagingService, Assert-PilotServiceIsNotProduction,
    Invoke-PilotGit, Assert-PilotWorktreeClean, Get-PilotHeadSha,
    Assert-PilotHeadIsVerified, Get-PilotFileSha256, Write-PilotJson,
    Read-PilotJson, Invoke-PilotPython
