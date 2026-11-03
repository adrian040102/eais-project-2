#requires -Version 5.1
<#
.SYNOPSIS
Runs the Project 2 evaluator in a disposable Python 3.10 Docker container.

.DESCRIPTION
Setup builds the runtime image and missing train store. Evaluate prepares those
prerequisites automatically. Model calls are disabled unless -Model is supplied.
The project directory is mounted at /workspace, where reports and data persist.

.EXAMPLE
.\run.ps1 -Action Setup

.EXAMPLE
.\run.ps1 -Action Test

.EXAMPLE
.\run.ps1 -Split dev -System team/system.py

.EXAMPLE
.\run.ps1 -Split dev -Model -MaxCostUsd 0.25
#>
[CmdletBinding()]
param(
    [ValidateSet('Setup', 'Status', 'Test', 'Evaluate', 'Search')]
    [string]$Action = 'Evaluate',

    [ValidateSet('dev', 'holdout', 'all')]
    [string]$Split = 'dev',

    [string]$System = 'team/system.py',

    [string]$Query,

    [string]$Company,

    [switch]$Model,

    [double]$MaxCostUsd = 0.25
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$ProjectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$Image = 'eais-p2:py310'
$Dockerfile = Join-Path $ProjectRoot 'runtime/Dockerfile'
$TrainStore = Join-Path $ProjectRoot 'eais-p2-kit/data/trains.sqlite'
$DockerExecutable = $null

function Invoke-DockerChecked {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & $script:DockerExecutable @Arguments
    $nativeExitCode = $LASTEXITCODE
    if ($nativeExitCode -ne 0) {
        $failure = [System.InvalidOperationException]::new("Docker command failed (exit code $nativeExitCode).")
        $failure.Data['ExitCode'] = $nativeExitCode
        throw $failure
    }
}

function Get-EngineState {
    try {
        $engineLines = @(& $script:DockerExecutable info --format '{{.OSType}}|{{.ServerVersion}}' 2>$null)
        $engineExitCode = $LASTEXITCODE
    }
    catch {
        throw 'Docker engine is unavailable. Start Docker Desktop, wait until it is running, then rerun this command.'
    }
    if ($engineExitCode -ne 0 -or $engineLines.Count -eq 0) {
        throw 'Docker engine is unavailable. Start Docker Desktop, wait until it is running, then rerun this command.'
    }
    $engine = ($engineLines -join '').Trim().Split('|')
    if ($engine.Count -lt 2 -or [string]::IsNullOrWhiteSpace($engine[0]) -or [string]::IsNullOrWhiteSpace($engine[1])) {
        throw 'Docker engine is unavailable. Start Docker Desktop, wait until it is running, then rerun this command.'
    }
    if ($engine[0] -ne 'linux') {
        throw 'This runtime needs Linux containers. Switch Docker Desktop to Linux containers, then rerun this command.'
    }
    return $engine
}

function Test-RuntimeImage {
    try {
        & $script:DockerExecutable image inspect --format '{{.Id}}' $script:Image 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Ensure-RuntimeImage {
    param([switch]$Rebuild)
    if ($Rebuild -or -not (Test-RuntimeImage)) {
        if (-not (Test-Path -LiteralPath $script:Dockerfile -PathType Leaf)) {
            throw "Dockerfile not found: $script:Dockerfile"
        }
        Write-Host "Building $script:Image ..."
        Invoke-DockerChecked -Arguments @('build', '--tag', $script:Image, '--file', $script:Dockerfile, $script:ProjectRoot)
    }
}

function Invoke-Runtime {
    param(
        [Parameter(Mandatory = $true)][string[]]$Command,
        [switch]$PassModelEnvironment
    )
    $dockerArguments = @(
        'run', '--rm',
        '--mount', "type=bind,source=$script:ProjectRoot,target=/workspace",
        '--workdir', '/workspace',
        '--env', 'PYTHONUTF8=1',
        '--env', 'PYTHONIOENCODING=utf-8',
        '--env', 'PYTHONDONTWRITEBYTECODE=1'
    )
    if ($PassModelEnvironment) {
        # Docker reads these values from the host environment; tokens never enter argv.
        $dockerArguments += @('--env', 'P2_TOKEN', '--env', 'P2_BASE_URL')
    }
    $dockerArguments += @($script:Image)
    $dockerArguments += $Command
    Invoke-DockerChecked -Arguments $dockerArguments
}

function Ensure-TrainStore {
    if (-not (Test-Path -LiteralPath $script:TrainStore -PathType Leaf)) {
        Write-Host 'Building the train store (once, approximately 0.7 GB) ...'
        Invoke-Runtime -Command @('python', '-I', 'eais-p2-kit/make_store.py')
        if (-not (Test-Path -LiteralPath $script:TrainStore -PathType Leaf)) {
            throw "The train-store build finished without creating $script:TrainStore."
        }
    }
}

function Resolve-ContainerSystem {
    if ([string]::IsNullOrWhiteSpace($script:System)) {
        throw '-System must name a Python file inside this project.'
    }
    $candidate = if ([System.IO.Path]::IsPathRooted($script:System)) {
        $script:System
    }
    else {
        Join-Path $script:ProjectRoot $script:System
    }
    $candidate = [System.IO.Path]::GetFullPath($candidate)
    $boundary = $script:ProjectRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $candidate.StartsWith($boundary, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw '-System must resolve to a Python file inside this project.'
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "System file not found: $candidate"
    }
    $file = Get-Item -LiteralPath $candidate
    if ($file.Extension -ine '.py') {
        throw '-System must name a .py file.'
    }
    # Reparse points could expose a host path not available through this bind mount.
    $cursor = $file
    while ($cursor.FullName.TrimEnd([System.IO.Path]::DirectorySeparatorChar) -ine $script:ProjectRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar)) {
        if (($cursor.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw '-System and its containing project directories must not be symbolic links or junctions.'
        }
        $cursor = Get-Item -LiteralPath ([System.IO.Path]::GetDirectoryName($cursor.FullName))
    }
    $relative = $candidate.Substring($boundary.Length).Replace('\', '/')
    return "/workspace/$relative"
}

try {
    if ($Action -eq 'Search' -and [string]::IsNullOrWhiteSpace($Query)) {
        throw '-Action Search requires -Query with search words.'
    }
    # Resolve evaluation inputs before a missing runtime can trigger a build.
    $containerSystem = $null
    if ($Action -eq 'Evaluate') {
        $containerSystem = Resolve-ContainerSystem
        if ([double]::IsNaN($MaxCostUsd) -or [double]::IsInfinity($MaxCostUsd) -or $MaxCostUsd -le 0) {
            throw '-MaxCostUsd must be a finite positive number.'
        }
        if ($Model) {
            foreach ($environmentName in @('P2_TOKEN', 'P2_BASE_URL')) {
                if ([string]::IsNullOrWhiteSpace([System.Environment]::GetEnvironmentVariable($environmentName))) {
                    throw "-Model requires $environmentName in the host environment."
                }
            }
        }
    }
    $dockerCommand = Get-Command -Name docker -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $dockerCommand) {
        throw 'Docker CLI was not found. Install Docker Desktop and start it, then rerun this command.'
    }
    $DockerExecutable = $dockerCommand.Source
    $engine = @(Get-EngineState)
    if ($Action -eq 'Status') {
        Write-Host "Docker engine: Linux (server $($engine[1]))"
        if (Test-RuntimeImage) {
            Write-Host "Runtime image: $Image is ready"
        }
        else {
            Write-Host "Runtime image: $Image is missing; run .\run.ps1 -Action Setup"
        }
        if (Test-Path -LiteralPath $TrainStore -PathType Leaf) {
            Write-Host 'Train store: ready'
        }
        else {
            Write-Host 'Train store: missing; run .\run.ps1 -Action Setup'
        }
        exit 0
    }
    Ensure-RuntimeImage -Rebuild:($Action -eq 'Setup')
    if ($Action -eq 'Setup' -or $Action -eq 'Evaluate') {
        Ensure-TrainStore
    }
    if ($Action -eq 'Setup') {
        Write-Host 'Project 2 runtime and train store are ready.'
        exit 0
    }
    if ($Action -eq 'Test') {
        Invoke-Runtime -Command @('python', '-B', '-X', 'utf8', '-I', '-m', 'unittest', 'discover', '-s', 'evaluation/tests', '-p', 'test_*.py', '-v')
        exit 0
    }
    if ($Action -eq 'Search') {
        $searchCommand = @('python', '-B', '-I', 'evaluation/search_rules.py', '--query', $Query)
        if (-not [string]::IsNullOrWhiteSpace($Company)) {
            $searchCommand += @('--company', $Company)
        }
        Invoke-Runtime -Command $searchCommand
        exit 0
    }
    $evaluationCommand = @(
        'python', '-I', 'evaluation/evaluate.py', 'run',
        '--system', $containerSystem,
        '--split', $Split,
        '--max-cost-usd', $MaxCostUsd.ToString([System.Globalization.CultureInfo]::InvariantCulture)
    )
    if ($Model) {
        $evaluationCommand += @('--model')
    }
    Invoke-Runtime -Command $evaluationCommand -PassModelEnvironment:$Model
    exit 0
}
catch {
    [Console]::Error.WriteLine("Project 2: $($_.Exception.Message)")
    if ($_.Exception.Data.Contains('ExitCode')) {
        exit [int]$_.Exception.Data['ExitCode']
    }
    exit 1
}
