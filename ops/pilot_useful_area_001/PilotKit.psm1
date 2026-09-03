<#
    ops/pilot_useful_area_001/PilotKit.psm1
    DRONE-USEFUL-AREA-PILOT-001 -- guards shared by every script of the kit.

    Every refusal of this macro-stage lives HERE and nowhere else. A kit that
    repeats "is this production?" in six scripts will one day disagree with
    itself in one of the six -- and that will be the one holding the
    production path.

    WINDOWS POWERSHELL 5.1 IS A SUPPORTED TARGET.
      The server runs Windows and the operator pastes into whatever console
      is open, which on Windows Server is powershell.exe 5.1, not pwsh 7.
      Two things about 5.1 broke the first edition of this kit and are fixed
      here:

        * `Set-Content -Encoding utf8NoBOM` does not exist in 5.1, and plain
          `-Encoding UTF8` writes a BOM. A BOM in front of a JSON document
          makes json.load() in the probes fail with a message about column 1;
        * native redirection `>` in 5.1 writes UTF-16LE. A probe's ASCII JSON
          captured that way came back as a file that looks right in an editor
          and is not readable by anything expecting UTF-8.

      So: every evidence file is written by PYTHON, which controls its own
      encoding, and PowerShell only reads it back through .NET with an
      explicit encoding. Nothing in this kit redirects a JSON document with
      `>`.

    Output is ASCII only: it is read in a PowerShell console and in an NSSM
    log, where the code page is not our guarantee.
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

# The kit lives in its OWN checkout, so that checking out or rolling back a
# target repository cannot take the running scripts away with it.
$script:KitRoot = 'C:\vehicle-soft-pilot-kit'

$script:ServerRunsRoot    = 'D:\transport-report-backups\pilot\DRONE-USEFUL-AREA-001\runs'
$script:CollectorRunsRoot = 'C:\vehicle-soft-pilot-runs'

$script:CollectorRepo = 'C:\VehicleSoft_DJI_StageB_Pilot'
$script:CollectorPython = 'C:\VehicleSoft_DJI_StageB_Pilot\drone_collector\.venv\Scripts\python.exe'
$script:CollectorSession = 'C:\VehicleSoft_DJI_StageB_Pilot\drone_collector\data\storage_state.json'

$script:SystemPython = 'C:\Program Files\Python314\python.exe'

# The verified revision of the PRODUCT. The revision of the KIT is measured,
# never declared: it cannot be known before the commit that creates it.
$script:ProductSha = 'c3e6a12ab95117710eeea5e05133f5cd548b698e'
$script:TargetDay   = '2026-06-05'
$script:MigrationId = 'DRONES_USEFUL_AREA_001'
$script:StagingHost = 'srv-yoqsh'
$script:CollectorHost = 'BAK-TEX11'

$script:SmokePath = '/login'
$script:SmokeAllowedStatus = @(200)
# [REASON]: 200 сам по себе не значит «приложение поднялось». Страница
# обслуживания, заглушка обратного прокси и чужой сервер на том же порту
# отвечают двумястами так же охотно. Признак -- класс формы входа из
# templates/login.html.
$script:SmokePageMarker = 'vs-login-form'

# Точный набор полей run.json. Объявлен здесь, а не подразумевается по коду:
# «почти проверено» у файла, из которого берутся ПУТИ для записи и решения об
# остановке службы, значит «не проверено».
$script:RunManifestRequiredFields = @(
    'kit', 'kit_version', 'run_id', 'approved_kit_sha', 'measured_kit_sha',
    'product_sha', 'target_day', 'created_utc', 'machine', 'kit_checkout',
    'run_root', 'steps')

function Get-PilotConstants {
    [CmdletBinding()]
    param()
    return [ordered]@{
        ProductionRoot     = $script:ProductionRoot
        ProductionDb       = $script:ProductionDb
        ProductionUrl      = $script:ProductionUrl
        ProductionService  = $script:ProductionService
        StagingRoot        = $script:StagingRoot
        StagingDb          = $script:StagingDb
        StagingUrl         = $script:StagingUrl
        KitRoot            = $script:KitRoot
        ServerRunsRoot     = $script:ServerRunsRoot
        CollectorRunsRoot  = $script:CollectorRunsRoot
        CollectorRepo      = $script:CollectorRepo
        CollectorPython    = $script:CollectorPython
        CollectorSession   = $script:CollectorSession
        SystemPython       = $script:SystemPython
        ProductSha         = $script:ProductSha
        TargetDay          = $script:TargetDay
        MigrationId        = $script:MigrationId
        StagingHost        = $script:StagingHost
        CollectorHost      = $script:CollectorHost
        SmokePath          = $script:SmokePath
        SmokeAllowedStatus = $script:SmokeAllowedStatus
        SmokePageMarker    = $script:SmokePageMarker
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

function Assert-PilotOutsideCheckouts {
    <#
        A working directory must live outside EVERY checkout.

        [REASON]: the first edition put the collector's run directory inside
        C:\VehicleSoft_DJI_StageB_Pilot. Its own artefacts then showed up in
        `git status`, and the clean-worktree check that the run depends on
        would have failed because of the run itself.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Path,
          [string]$What = 'work root')

    Assert-PilotNotProduction -Path $Path -What $What
    foreach ($root in @($script:StagingRoot, $script:CollectorRepo, $script:KitRoot)) {
        if (Test-PilotPathWithin -Path $Path -Root $root) {
            throw "REFUSED: the $What '$Path' is inside the checkout $root. Its files would show up in git status and make the clean-worktree check fail on the run's own artefacts."
        }
    }
}

# --- URLs --------------------------------------------------------------------

function Get-PilotUrlAuthority {
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Url)

    $text = $Url.Trim().TrimEnd('/').ToLowerInvariant()
    if ($text.Contains('://')) { $text = $text.Substring($text.IndexOf('://') + 3) }
    if ($text.Contains('@')) { $text = $text.Substring($text.IndexOf('@') + 1) }
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

function Test-PilotRedirectStaysInAuthority {
    <#
        A redirect may move the page; it may not move the SITE.
        A relative Location stays inside by construction; an absolute one is
        compared by authority. A redirect to production or to a foreign host
        is not "the page moved", it is the wrong site answering.

        The authority is a parameter so the rule can be exercised against a
        local server in CI. The rule is the same either way; only the site it
        is asked about differs.
    #>
    [CmdletBinding()]
    param([AllowEmptyString()][AllowNull()][string]$Location,
          [Parameter(Mandatory)][string]$Authority)

    if ([string]::IsNullOrWhiteSpace($Location)) { return $false }
    $text = $Location.Trim()
    if ($text.StartsWith('/')) { return $true }
    if (-not $text.Contains('://')) { return $true }
    return ((Get-PilotUrlAuthority -Url $text) -eq (Get-PilotUrlAuthority -Url $Authority))
}

function Test-PilotRedirectStaysInStaging {
    [CmdletBinding()]
    param([AllowEmptyString()][AllowNull()][string]$Location)
    return (Test-PilotRedirectStaysInAuthority -Location $Location -Authority $script:StagingUrl)
}

function Test-PilotSmokeStatus {
    <#
        Which statuses count as "the staging application is up".

        [REASON]: the first edition accepted anything under 500. A 404 from a
        wrong path and a 401 from an application that never finished starting
        both passed it. A check a broken service passes is not a check.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][int]$Status)
    return ($script:SmokeAllowedStatus -contains $Status)
}

# --- Machine -----------------------------------------------------------------

function Assert-PilotHost {
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
        against this machine. A name that only appears in a document proves
        nothing; the winner is the service that actually runs from
        C:\transport-report-staging.
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

function Get-PilotKitSha {
    <#
        The kit's own revision, MEASURED at the kit checkout.

        [REASON]: it cannot be a constant in the source. The kit lives in the
        commit that creates it, so a script demanding `HEAD -eq <kit sha>`
        from its own file would be demanding its own absence.

        But MEASURED is not the same as APPROVED, and the first edition
        confused the two: any clean HEAD became trusted simply because its sha
        had been read. Measuring is what this function does; approving is what
        Assert-PilotApprovedKitSha does, with a sha the reviewer names.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$KitCheckout)

    if (-not (Test-Path -LiteralPath (Join-Path $KitCheckout '.git'))) {
        throw "REFUSED: '$KitCheckout' is not a git checkout, so the kit revision cannot be measured."
    }
    Assert-PilotWorktreeClean -Repo $KitCheckout | Out-Null
    $sha = Get-PilotHeadSha -Repo $KitCheckout
    if ($sha -notmatch '^[0-9a-f]{40}$') {
        throw "REFUSED: the kit checkout reported '$sha' as its HEAD."
    }
    return $sha
}

function Assert-PilotApprovedKitSha {
    <#
        The kit checkout must be at the revision an independent reviewer
        APPROVED -- not merely at some clean revision.

        [REASON]: without this, a newer commit, a stray branch or an unrelated
        clean checkout was "trusted" the moment its sha was measured. The whole
        chain of evidence then said nothing: it recorded which bytes ran, and
        nobody had said those bytes were the ones to run.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$KitCheckout,
          [Parameter(Mandatory)][string]$ApprovedKitSha)

    if ($ApprovedKitSha -notmatch '^[0-9a-f]{40}$') {
        throw "REFUSED: '$ApprovedKitSha' is not a full 40-character revision. The approved kit revision is named by the reviewer, in full."
    }
    $measured = Get-PilotKitSha -KitCheckout $KitCheckout
    if ($measured -ne $ApprovedKitSha) {
        throw "REFUSED: the kit checkout $KitCheckout is at $measured, which is not the APPROVED kit revision $ApprovedKitSha. Measuring a revision is not the same as it having been approved."
    }
    # [REASON]: returns the sha and prints nothing. A function that both
    # writes to the output stream and returns a value hands its caller two
    # objects, and the caller then has to guess which one is the sha.
    return $measured
}

function Assert-PilotProductSha {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Repo)

    $head = Get-PilotHeadSha -Repo $Repo
    if ($head -ne $script:ProductSha) {
        throw "REFUSED: $Repo is at $head, not at the verified product revision $($script:ProductSha)."
    }
    Write-Output "PRODUCT_HEAD=$head"
}

# --- Files, JSON and evidence -------------------------------------------------

function Get-PilotFileSha256 {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Write-PilotJson {
    <#
        UTF-8 WITHOUT a BOM, through .NET, on both 5.1 and 7.

        [REASON]: `Set-Content -Encoding utf8NoBOM` is PowerShell 6+ only and
        is a parameter-binding error on 5.1; plain `-Encoding UTF8` on 5.1
        writes a BOM, and a BOM in front of a JSON document makes json.load()
        in the probes fail. UTF8Encoding($false) is the same on every version.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path,
          [Parameter(Mandatory)]$Value)

    $directory = Split-Path -Parent $Path
    if ($directory -and -not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    $json = $Value | ConvertTo-Json -Depth 12
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json, $encoding)
    return (Resolve-Path -LiteralPath $Path).Path
}

function Read-PilotJson {
    <#
        Read JSON through .NET with an explicit encoding.

        [REASON]: `Get-Content -Encoding UTF8` on 5.1 and on 7 disagree about
        a BOM-less file, and the kit reads files written by Python. .NET's
        ReadAllText detects a BOM when there is one and decodes UTF-8 when
        there is not, identically on both.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "REFUSED: evidence file not found: $Path"
    }
    $full = (Resolve-Path -LiteralPath $Path).Path
    $text = [System.IO.File]::ReadAllText($full, [System.Text.Encoding]::UTF8)
    return ($text | ConvertFrom-Json)
}

function Invoke-PilotPython {
    <#
        Run a python tool and check its exit code.

        NOTHING is redirected with `>`. When a tool must produce a JSON file
        it is given its own --out and writes the file itself; PowerShell reads
        it back with Read-PilotJson. On 5.1 `>` writes UTF-16LE, so a JSON
        document captured that way is unreadable by every reader that expects
        UTF-8 -- including the next step of this very kit.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Python,
          [Parameter(Mandatory)][string[]]$Arguments,
          [int[]]$AllowedExitCodes = @(0),
          [switch]$PassThruExitCode)

    & $Python @Arguments | Out-Null
    $code = $LASTEXITCODE
    if ($PassThruExitCode) { return $code }
    if ($AllowedExitCodes -notcontains $code) {
        throw "REFUSED: $Python $($Arguments -join ' ') exited $code (allowed: $($AllowedExitCodes -join ', '))."
    }
    return $code
}

function Invoke-PilotProbe {
    <#
        Run one of the kit's probes with the run identity attached, and return
        the evidence it wrote. The probe writes the file; this never captures
        stdout into a file.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Python,
          [Parameter(Mandatory)][string]$Script,
          [Parameter(Mandatory)][string[]]$Arguments,
          [Parameter(Mandatory)][string]$RunId,
          [Parameter(Mandatory)][string]$KitSha,
          [Parameter(Mandatory)][string]$OutFile,
          [int[]]$AllowedExitCodes = @(0))

    $full = @($Script) + $Arguments + @('--run-id', $RunId, '--kit-sha', $KitSha,
                                        '--out', $OutFile)
    $code = Invoke-PilotPython -Python $Python -Arguments $full -PassThruExitCode
    if ($AllowedExitCodes -notcontains $code) {
        throw "REFUSED: $(Split-Path -Leaf $Script) exited $code (allowed: $($AllowedExitCodes -join ', ')). Evidence: $OutFile"
    }
    return [ordered]@{ ExitCode = $code; Evidence = (Read-PilotJson -Path $OutFile) }
}

# --- The one run manifest -----------------------------------------------------

function Get-PilotRunDirectory {
    <#
        The one place that turns a runs root and a run id into a directory.

        [REASON]: NOT Join-Path. Join-Path resolves against PowerShell drives,
        and on a non-Windows host `Join-Path 'D:\runs' $id` fails with "cannot
        find drive D" and yields $null. Two nulls then compare equal, so a
        validator built on Join-Path would have passed a manifest whose
        run_root was anything at all -- and it did, silently, wherever the path
        could not be resolved. Plain concatenation says what is meant and is
        the same string on every host.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$RunsRoot,
          [Parameter(Mandatory)][string]$RunId)

    return ($RunsRoot.TrimEnd('\', '/') + '\' + $RunId)
}

function Test-PilotRunManifest {
    <#
        A run manifest is not a note to ourselves: deploy, recalculate and
        rollback take PATHS out of it and write files there.

        [REASON]: the first edition checked run_id and nothing else. A
        manifest whose run_root pointed at the staging checkout, at production
        or at a neighbouring run would have been obeyed -- New-Item, evidence
        files and, further down, a service stop, all aimed wherever the file
        said. Every field is checked, and run_root must EQUAL the path this
        run id implies, not merely resemble it.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)]$Manifest,
          [Parameter(Mandatory)][string]$RunsRoot,
          [Parameter(Mandatory)][string]$RunId,
          [string]$ApprovedKitSha,
          [string]$ExpectedMachine)

    $problems = @()
    foreach ($field in $script:RunManifestRequiredFields) {
        if (-not ($Manifest.PSObject.Properties.Name -contains $field)) {
            $problems += "MISSING_FIELD:$field"
        }
    }
    if ($problems.Count -gt 0) { return $problems }

    if ($Manifest.kit -ne 'DRONE-USEFUL-AREA-PILOT-001') { $problems += 'WRONG_KIT' }
    if ([string]$Manifest.kit_version -ne '2') { $problems += 'WRONG_KIT_VERSION' }
    if ($Manifest.run_id -ne $RunId) { $problems += 'RUN_ID_MISMATCH' }
    if ($Manifest.product_sha -ne $script:ProductSha) { $problems += 'PRODUCT_SHA_MISMATCH' }
    if ($Manifest.target_day -ne $script:TargetDay) { $problems += 'TARGET_DAY_MISMATCH' }
    if ([string]$Manifest.approved_kit_sha -notmatch '^[0-9a-f]{40}$') { $problems += 'MALFORMED_APPROVED_KIT_SHA' }
    if ([string]$Manifest.measured_kit_sha -notmatch '^[0-9a-f]{40}$') { $problems += 'MALFORMED_MEASURED_KIT_SHA' }
    if ($Manifest.approved_kit_sha -ne $Manifest.measured_kit_sha) { $problems += 'APPROVED_AND_MEASURED_KIT_SHA_DIFFER' }
    if ($ApprovedKitSha -and $Manifest.approved_kit_sha -ne $ApprovedKitSha) { $problems += 'APPROVED_KIT_SHA_MISMATCH' }
    if ([string]::IsNullOrWhiteSpace([string]$Manifest.machine)) {
        $problems += 'MACHINE_ABSENT'
    } else {
        # [REASON]: не «непустая строка», а ЭТА машина. Манифест, написанный на
        # другом сервере, называет чужие пути и чужую службу, и принять его --
        # значит выполнить их.
        $expected = if ($ExpectedMachine) { $ExpectedMachine } else { $env:COMPUTERNAME }
        if ($expected -and
            ([string]$Manifest.machine).Trim().ToLowerInvariant() -ne $expected.Trim().ToLowerInvariant()) {
            $problems += 'MACHINE_MISMATCH'
        }
    }
    # created_utc пишется при создании запуска, поэтому и требуется, и
    # проверяется по форме: поле, которое никто не читает, однажды окажется
    # чем угодно.
    if ([string]$Manifest.created_utc -notmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$') {
        $problems += 'MALFORMED_CREATED_UTC'
    }
    if (-not (Test-PilotPathEquals -Left ([string]$Manifest.kit_checkout) -Right $script:KitRoot)) {
        $problems += 'KIT_CHECKOUT_IS_NOT_THE_KIT_ROOT'
    }

    # run_root must be EXACTLY the directory this run id implies.
    $expected = Get-PilotRunDirectory -RunsRoot $RunsRoot -RunId $RunId
    if (-not (Test-PilotPathEquals -Left ([string]$Manifest.run_root) -Right $expected)) {
        $problems += 'RUN_ROOT_IS_NOT_THE_RUN_DIRECTORY'
    }
    foreach ($root in @($script:ProductionRoot, $script:StagingRoot,
                        $script:CollectorRepo, $script:KitRoot)) {
        if (Test-PilotPathWithin -Path ([string]$Manifest.run_root) -Root $root) {
            $problems += 'RUN_ROOT_IS_INSIDE_A_CHECKOUT'
        }
    }
    if ($null -eq $Manifest.steps) {
        $problems += 'STEPS_ABSENT'
    } elseif (-not ($Manifest.steps -is [System.Management.Automation.PSCustomObject] -or
                    $Manifest.steps -is [System.Collections.IDictionary])) {
        # Строка или число здесь означают, что манифест писал не этот комплект.
        $problems += 'STEPS_IS_NOT_AN_OBJECT'
    }
    return $problems
}

function Assert-PilotRunManifest {
    [CmdletBinding()]
    param([Parameter(Mandatory)]$Manifest,
          [Parameter(Mandatory)][string]$RunsRoot,
          [Parameter(Mandatory)][string]$RunId,
          [string]$ApprovedKitSha,
          [string]$ExpectedMachine)

    $problems = @(Test-PilotRunManifest -Manifest $Manifest -RunsRoot $RunsRoot `
                                        -RunId $RunId -ApprovedKitSha $ApprovedKitSha `
                                        -ExpectedMachine $ExpectedMachine)
    if ($problems.Count -gt 0) {
        throw "REFUSED: the run manifest of $RunId is not usable: $($problems -join ', '). Nothing was created, written or stopped."
    }
}

function New-PilotRun {
    <#
        Create the run: one identifier, one directory, one manifest.

        [REASON]: without this every step looked for the "newest" file of its
        kind on its own. Two runs on one day and the report would happily pair
        the preflight of one with the recalculation of the other, and every
        line of it would be true.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$RunsRoot,
          [Parameter(Mandatory)][string]$KitCheckout,
          [Parameter(Mandatory)][string]$ApprovedKitSha)

    Assert-PilotOutsideCheckouts -Path $RunsRoot -What 'runs root'
    $kitSha = Assert-PilotApprovedKitSha -KitCheckout $KitCheckout `
                                         -ApprovedKitSha $ApprovedKitSha

    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $tail = -join ((1..8) | ForEach-Object { '{0:x}' -f (Get-Random -Minimum 0 -Maximum 16) })
    $runId = "$stamp-$tail"

    $directory = Get-PilotRunDirectory -RunsRoot $RunsRoot -RunId $runId
    # [REASON]: the run directory is created ATOMICALLY and a collision is a
    # refusal. `-Force` would have quietly adopted an existing directory --
    # another run's, with another run's evidence already in it.
    if (Test-Path -LiteralPath $directory) {
        throw "REFUSED: the run directory $directory already exists. This kit never adopts a directory it did not create."
    }
    [System.IO.Directory]::CreateDirectory($directory) | Out-Null
    foreach ($leaf in @('evidence', 'log', 'copy', 'sandbox', 'report', 'backup')) {
        [System.IO.Directory]::CreateDirectory((Join-Path $directory $leaf)) | Out-Null
    }

    $manifest = [ordered]@{
        kit              = 'DRONE-USEFUL-AREA-PILOT-001'
        kit_version      = '2'
        run_id           = $runId
        approved_kit_sha = $ApprovedKitSha
        measured_kit_sha = $kitSha
        product_sha      = $script:ProductSha
        target_day       = $script:TargetDay
        created_utc      = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        machine          = $env:COMPUTERNAME
        kit_checkout     = $KitCheckout
        run_root         = $directory
        steps            = [ordered]@{}
    }
    Assert-PilotRunManifest -Manifest ([pscustomobject]$manifest) -RunsRoot $RunsRoot `
                            -RunId $runId -ApprovedKitSha $ApprovedKitSha `
                            -ExpectedMachine $env:COMPUTERNAME
    Write-PilotJson -Path (Join-Path $directory 'run.json') -Value $manifest | Out-Null
    return $manifest
}

function Get-PilotRun {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$RunsRoot,
          [Parameter(Mandatory)][string]$RunId,
          [string]$ApprovedKitSha)

    if ($RunId -notmatch '^\d{8}T\d{6}Z-[0-9a-f]{8}$') {
        throw "REFUSED: '$RunId' is not a run identifier of this kit."
    }
    $directory = Get-PilotRunDirectory -RunsRoot $RunsRoot -RunId $RunId
    $manifestPath = Join-Path $directory 'run.json'
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        throw "REFUSED: no run manifest at $manifestPath. Start with PREFLIGHT_AND_COPY_TEST.ps1, which creates the run."
    }
    $manifest = Read-PilotJson -Path $manifestPath
    # [REASON]: и машина тоже. Манифест, написанный на другом сервере, называет
    # чужие пути и чужую службу; принять его -- значит выполнить их здесь.
    Assert-PilotRunManifest -Manifest $manifest -RunsRoot $RunsRoot `
                            -RunId $RunId -ApprovedKitSha $ApprovedKitSha `
                            -ExpectedMachine $env:COMPUTERNAME
    return $manifest
}

function Set-PilotRunStep {
    <#
        Record one step in the single manifest. The manifest is the only index
        of a run; no step ever searches for the newest anything.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$RunsRoot,
          [Parameter(Mandatory)][string]$RunId,
          [Parameter(Mandatory)][string]$Step,
          [Parameter(Mandatory)]$Value)

    $directory = Get-PilotRunDirectory -RunsRoot $RunsRoot -RunId $RunId
    $manifestPath = Join-Path $directory 'run.json'
    $manifest = Read-PilotJson -Path $manifestPath
    # The same full check before writing: a manifest that redirects writes is
    # dangerous precisely at the moment something is about to be written.
    Assert-PilotRunManifest -Manifest $manifest -RunsRoot $RunsRoot -RunId $RunId `
                            -ExpectedMachine $env:COMPUTERNAME

    $steps = [ordered]@{}
    if ($manifest.PSObject.Properties.Name -contains 'steps' -and $manifest.steps) {
        foreach ($property in $manifest.steps.PSObject.Properties) {
            $steps[$property.Name] = $property.Value
        }
    }
    $steps[$Step] = $Value

    $updated = [ordered]@{}
    foreach ($property in $manifest.PSObject.Properties) {
        if ($property.Name -eq 'steps') { continue }
        $updated[$property.Name] = $property.Value
    }
    $updated['steps'] = $steps
    Write-PilotJson -Path $manifestPath -Value $updated | Out-Null
    return $manifestPath
}

function Get-PilotEvidencePath {
    <#
        Fixed names inside the run directory. There is no search, so there is
        nothing to search wrongly.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$RunsRoot,
          [Parameter(Mandatory)][string]$RunId,
          [Parameter(Mandatory)][ValidateSet('preflight', 'deploy', 'collect',
              'recalc_dry', 'recalc_apply_1', 'recalc_apply_2',
              'staging_snapshot', 'staging_before', 'staging_after',
              'staging_input', 'staging_after_dry', 'repo_staging',
              'repo_kit', 'repo_collector', 'materialize', 'report_json',
              'report_md')][string]$Name)

    $directory = Get-PilotRunDirectory -RunsRoot $RunsRoot -RunId $RunId
    switch ($Name) {
        'report_json' { return (Join-Path $directory 'report\PILOT_REPORT.json') }
        'report_md'   { return (Join-Path $directory 'report\PILOT_REPORT.md') }
        default       { return (Join-Path $directory ("evidence\{0}.json" -f $Name)) }
    }
}

function New-PilotRunBackup {
    <#
        Back the staging database up into a directory that belongs to THIS RUN
        and nothing else, and accept exactly the one file this call produced.

        [REASON]: the first edition listed a shared backup directory before and
        after, then took the newest new file. The scheduled nightly backup
        writes into that same directory; a run that overlapped it would have
        adopted somebody else's file as its own rollback point -- and the
        rollback would have restored a database nobody chose.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Python,
          [Parameter(Mandatory)][string]$BackupTool,
          [Parameter(Mandatory)][string]$SourceDb,
          [Parameter(Mandatory)][string]$RunBackupDir,
          [string]$Suffix = 'pilot_before_useful_area')

    Assert-PilotNotProduction -Path $RunBackupDir -What 'run backup directory'
    if (-not (Test-Path -LiteralPath $RunBackupDir)) {
        [System.IO.Directory]::CreateDirectory($RunBackupDir) | Out-Null
    }
    $existing = @(Get-ChildItem -LiteralPath $RunBackupDir -File -ErrorAction SilentlyContinue)
    if ($existing.Count -ne 0) {
        throw "REFUSED: the run backup directory $RunBackupDir is not empty ($($existing.Count) file(s)). It belongs to this run alone."
    }

    Invoke-PilotPython -Python $Python -Arguments @(
        $BackupTool, '--source', $SourceDb, '--dest-dir', $RunBackupDir,
        '--suffix', $Suffix) | Out-Null

    $produced = @(Get-ChildItem -LiteralPath $RunBackupDir -File -Filter '*.db')
    if ($produced.Count -ne 1) {
        throw "REFUSED: the backup call produced $($produced.Count) file(s) in $RunBackupDir; exactly one was expected."
    }
    return [ordered]@{
        Directory = (Resolve-Path -LiteralPath $RunBackupDir).Path
        Path      = $produced[0].FullName
        Bytes     = $produced[0].Length
        Sha256    = (Get-PilotFileSha256 -Path $produced[0].FullName)
    }
}

# --- Smoke test ---------------------------------------------------------------

function Invoke-PilotSmokeTest {
    <#
        Ask the staging application one question with a known answer.

        GET /login unauthenticated renders the sign-in page: 200 and nothing
        else. A 3xx is followed at most once and ONLY when its Location stays
        inside the staging authority; a redirect to production or elsewhere is
        a failure, not a redirect. 401, 403 and 404 are failures outright.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$BaseUrl,
          [string]$Path = $script:SmokePath,
          [int]$TimeoutSec = 15,
          [int]$Attempts = 10,
          [int]$DelaySeconds = 3)

    # The guard first: this kit asks the staging site and no other. Then the
    # same HTTP rules everyone else gets, aimed at the staging authority.
    Assert-PilotStagingUrl -Url $BaseUrl
    return (Invoke-PilotSmokeEndpoint -BaseUrl $BaseUrl -Path $Path `
                                      -Authority $script:StagingUrl `
                                      -Marker $script:SmokePageMarker `
                                      -TimeoutSec $TimeoutSec `
                                      -Attempts $Attempts `
                                      -DelaySeconds $DelaySeconds)
}

function Invoke-PilotSmokeEndpoint {
    <#
        The HTTP half of the smoke test, with the site it is asked about and
        the page marker as parameters. Separated so that CI can run the REAL
        logic -- status, marker, redirect following, redirect authority --
        against a local server, instead of asserting that the code looks right.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$BaseUrl,
          [Parameter(Mandatory)][string]$Path,
          [Parameter(Mandatory)][string]$Authority,
          [Parameter(Mandatory)][string]$Marker,
          [int]$TimeoutSec = 15,
          [int]$Attempts = 10,
          [int]$DelaySeconds = 3)

    $url = ($BaseUrl.TrimEnd('/')) + $Path

    $status = $null
    $location = $null
    $followed = $false
    # [REASON]: NOT $error -- that is PowerShell's automatic error stack.
    $failureText = ''

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        $result = Get-PilotHttpStatus -Url $url -TimeoutSec $TimeoutSec
        $status = $result.Status
        $location = $result.Location
        $failureText = $result.Error
        if ($null -ne $status) { break }
        if ($attempt -lt $Attempts) { Start-Sleep -Seconds $DelaySeconds }
    }

    if ($null -eq $status) {
        return [ordered]@{ ok = $false; status = $null; path = $Path
                           followed_redirect = $false; marker_seen = $false
                           reason = "NO_ANSWER: $failureText" }
    }

    if (@(301, 302, 303, 307, 308) -contains $status) {
        if (-not (Test-PilotRedirectStaysInAuthority -Location $location -Authority $Authority)) {
            return [ordered]@{ ok = $false; status = $status; path = $Path
                               followed_redirect = $false; marker_seen = $false
                               reason = 'REDIRECT_LEAVES_STAGING' }
        }
        $next = $location
        if ($next.StartsWith('/')) { $next = ($BaseUrl.TrimEnd('/')) + $next }
        $result = Get-PilotHttpStatus -Url $next -TimeoutSec $TimeoutSec
        $status = $result.Status
        $followed = $true
    }

    if ($null -eq $status) {
        return [ordered]@{ ok = $false; status = $null; path = $Path
                           followed_redirect = $followed; marker_seen = $false
                           reason = 'NO_ANSWER_AFTER_REDIRECT' }
    }
    if (-not (Test-PilotSmokeStatus -Status $status)) {
        return [ordered]@{ ok = $false; status = $status; path = $Path
                           followed_redirect = $followed; marker_seen = $false
                           reason = "STATUS_NOT_ALLOWED_$status" }
    }
    # [REASON]: 200 is not "the application is up". A maintenance page, a
    # reverse-proxy placeholder and somebody else's server on the same port
    # all answer 200 just as happily. The body must carry the application's
    # own login form.
    $markerSeen = $false
    if ($null -ne $result -and $result.Body) {
        $markerSeen = ([string]$result.Body).Contains($Marker)
    }
    if (-not $markerSeen) {
        return [ordered]@{ ok = $false; status = $status; path = $Path
                           followed_redirect = $followed; marker_seen = $false
                           reason = 'PAGE_IS_NOT_THE_APPLICATION_LOGIN_FORM' }
    }
    return [ordered]@{ ok = $true; status = $status; path = $Path
                       followed_redirect = $followed; marker_seen = $true
                       reason = 'OK' }
}

function Get-PilotHttpStatus {
    <#
        One request, no redirect following, status + Location + body back.

        [REASON]: NOT Invoke-WebRequest. On Windows PowerShell 5.1,
        `-MaximumRedirection 0` against a 3xx throws an InvalidOperationException
        that carries NO response object, so the status is simply lost and every
        redirect reads as "no answer" -- which is exactly what the Windows CI
        job caught. HttpWebRequest with AllowAutoRedirect disabled RETURNS the
        3xx response instead of throwing, and behaves the same on 5.1 and 7.
        4xx and 5xx still arrive as a WebException, and that one does carry
        its response.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Url,
          [int]$TimeoutSec = 15)

    $response = $null
    try {
        $request = [System.Net.HttpWebRequest]::Create($Url)
        $request.Method = 'GET'
        $request.AllowAutoRedirect = $false
        $request.Timeout = [int]($TimeoutSec * 1000)
        $request.ReadWriteTimeout = [int]($TimeoutSec * 1000)
        try {
            $response = $request.GetResponse()
        } catch [System.Net.WebException] {
            # 4xx and 5xx land here and DO carry the response.
            $response = $_.Exception.Response
            if ($null -eq $response) {
                return [ordered]@{ Status = $null; Location = $null; Body = ''
                                   Error = $_.Exception.Message }
            }
        }

        $status = [int]$response.StatusCode
        $location = $null
        try {
            $header = $response.Headers['Location']
            if ($header) { $location = [string]$header }
        } catch { $location = $null }

        $body = ''
        try {
            $stream = $response.GetResponseStream()
            if ($stream) {
                $reader = New-Object System.IO.StreamReader($stream)
                try { $body = $reader.ReadToEnd() } finally { $reader.Dispose() }
            }
        } catch { $body = '' }

        return [ordered]@{ Status = $status; Location = $location
                           Body = $body; Error = '' }
    } catch {
        return [ordered]@{ Status = $null; Location = $null; Body = ''
                           Error = $_.Exception.Message }
    } finally {
        if ($null -ne $response) {
            try { $response.Close() } catch { }
        }
    }
}

Export-ModuleMember -Function Get-PilotConstants, Get-PilotPathSegments,
    Test-PilotPathEquals, Test-PilotPathWithin, Test-PilotTouchesProduction,
    Assert-PilotNotProduction, Assert-PilotStagingPath,
    Assert-PilotOutsideCheckouts, Get-PilotUrlAuthority,
    Test-PilotUrlIsProduction, Test-PilotUrlIsStaging, Assert-PilotStagingUrl,
    Test-PilotRedirectStaysInStaging, Test-PilotRedirectStaysInAuthority,
    Test-PilotSmokeStatus,
    Assert-PilotHost, Get-PilotServiceImagePath, Select-PilotStagingService,
    Resolve-PilotStagingService, Assert-PilotServiceIsNotProduction,
    Invoke-PilotGit, Assert-PilotWorktreeClean, Get-PilotHeadSha,
    Get-PilotKitSha, Assert-PilotApprovedKitSha, Assert-PilotProductSha,
    Test-PilotRunManifest, Assert-PilotRunManifest, Get-PilotRunDirectory,
    Get-PilotFileSha256,
    Write-PilotJson, Read-PilotJson, Invoke-PilotPython, Invoke-PilotProbe,
    New-PilotRun, Get-PilotRun, Set-PilotRunStep, Get-PilotEvidencePath,
    New-PilotRunBackup,
    Invoke-PilotSmokeTest, Invoke-PilotSmokeEndpoint, Get-PilotHttpStatus
