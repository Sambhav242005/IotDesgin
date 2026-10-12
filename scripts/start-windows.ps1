[CmdletBinding()]
param(
    [ValidateSet('Auto','High','Balanced','Low','ServerOnly')][string]$Profile = 'Auto',
    [ValidateRange(1024,65535)][int]$Port = 8443,
    [switch]$SetupOnly, [switch]$PlanOnly, [switch]$NoBrowser,
    [switch]$Stop, [switch]$PrivateFirewall
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $root '.venv\Scripts\python.exe'
$dataDir = Join-Path $root 'data'
$profileFile = Join-Path $dataDir 'hardware-profile.json'
function Run-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Executable $($Arguments -join ' ')" }
}
function Install-Source([string]$Name, [string]$Repository, [string]$Commit, [string]$Marker) {
    $destination = [IO.Path]::GetFullPath((Join-Path $root "vendor\$Name"))
    if (Test-Path -LiteralPath (Join-Path $destination $Marker)) { return }
    if (Test-Path -LiteralPath $destination) { throw "Incomplete vendor directory: $destination. Preserve/repair it before retrying." }
    $staging = [IO.Path]::GetFullPath((Join-Path $root "tools\source-$Name-$Commit"))
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    $archive = Join-Path $staging 'source.zip'
    Invoke-WebRequest -UseBasicParsing "https://github.com/$Repository/archive/$Commit.zip" -OutFile $archive
    Expand-Archive -LiteralPath $archive -DestinationPath $staging -Force
    $source = [IO.Path]::GetFullPath((Join-Path $staging "$Name-$Commit"))
    if (!$destination.StartsWith($root + '\') -or !$source.StartsWith($root + '\')) { throw 'Source path escaped project directory' }
    New-Item -ItemType Directory -Force -Path (Join-Path $root 'vendor') | Out-Null
    Move-Item -LiteralPath $source -Destination $destination
}
Push-Location $root
try {
    if (![Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
        throw 'This installer requires Windows x64. GPU drivers must already be installed.'
    }
    if ($Stop) {
        if (!(Test-Path -LiteralPath $python)) { Write-Host 'No installation to stop.'; return }
        Run-Checked $python @('-m','iotdesign.launcher','--data',$dataDir,'--stop'); return
    }
    $ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
    $gpuRows = @()
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($smi) {
        $gpuRows = @(& $smi.Source --query-gpu=index,name,memory.total,compute_cap,driver_version --format=csv,noheader,nounits 2>$null)
        if ($LASTEXITCODE -ne 0) { $gpuRows = @() }
    }
    Write-Host "Windows hardware: $ramGB GB RAM"
    $gpu = $gpuRows | ForEach-Object {
        $parts = $_ -split ','
        if ($parts.Length -eq 5) {
            [pscustomobject]@{Index=$parts[0].Trim(); Name=$parts[1].Trim(); VRAM=[double]$parts[2].Trim(); Capability=[double]$parts[3].Trim(); Driver=[double]$parts[4].Trim()}
        }
    } | Sort-Object VRAM -Descending | Select-Object -First 1
    if ($gpu) { Write-Host "GPU: $($gpu.Name), $($gpu.VRAM) MiB, compute $($gpu.Capability)"; $env:CUDA_VISIBLE_DEVICES = $gpu.Index }
    if ($PlanOnly) {
        Write-Host 'Plan only: no installation, model downloads, firewall edits or server start.'
        Write-Host 'Auto: High 12x336 FP32; Balanced 8x224 FP16; Low 8x168 FP16 + SAM2 Tiny; ServerOnly when CUDA/memory unavailable.'
        if (Test-Path -LiteralPath $python) { Run-Checked $python @('-m','iotdesign.hardware','--profile',$Profile) }
        return
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $root 'tools'),$dataDir | Out-Null
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    $uv = Join-Path $root 'tools\uv.exe'
    if ($uvCommand) { $uv = $uvCommand.Source }
    elseif (!(Test-Path -LiteralPath $uv)) {
        Write-Host 'Downloading official uv Windows x64 bootstrap...'
        $release = Invoke-RestMethod https://api.github.com/repos/astral-sh/uv/releases/latest
        $asset = $release.assets | Where-Object name -eq 'uv-x86_64-pc-windows-msvc.zip'
        $digestAsset = $release.assets | Where-Object name -eq 'uv-x86_64-pc-windows-msvc.zip.sha256'
        if (!$asset -or !$digestAsset) { throw 'Official uv release does not contain the expected Windows archive/checksum.' }
        $zip = Join-Path $root 'tools\uv.zip'
        $digest = Join-Path $root 'tools\uv.zip.sha256'
        Invoke-WebRequest -UseBasicParsing $asset.browser_download_url -OutFile $zip
        Invoke-WebRequest -UseBasicParsing $digestAsset.browser_download_url -OutFile $digest
        $expected = ((Get-Content -LiteralPath $digest -Raw).Trim() -split '\s+')[0]
        if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ne $expected) { throw 'uv SHA256 verification failed.' }
        Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $root 'tools') -Force
    }
    if (!(Test-Path -LiteralPath $python)) {
        Run-Checked $uv @('python','install','3.12')
        Run-Checked $uv @('venv','--python','3.12',(Join-Path $root '.venv'))
    }
    Run-Checked $uv @('pip','install','--python',$python,'-e',$root)
    if ($Profile -ne 'ServerOnly' -and $gpu -and $ramGB -ge 12 -and $gpu.VRAM -ge 3584 -and $gpu.Capability -ge 6) {
        & $python -m iotdesign.hardware --cuda-check
        if ($LASTEXITCODE -ne 0) {
            if ($gpu.Capability -lt 7 -or $gpu.Driver -lt 570) {
                Write-Host 'Installing Pascal-compatible PyTorch CUDA 12.4 wheels...'
                Run-Checked $uv @('pip','install','--python',$python,'--reinstall','torch==2.6.0','torchvision==0.21.0','--index-url','https://download.pytorch.org/whl/cu124')
            } else {
                Run-Checked $uv @('pip','install','--python',$python,'torch==2.11.0','torchvision==0.26.0','--index-url','https://download.pytorch.org/whl/cu128')
            }
        }
        Run-Checked $uv @('pip','install','--python',$python,'-e',($root + '[gpu,semantic]'),'setuptools')
        Install-Source 'vggt' 'facebookresearch/vggt' 'a288dd0f14786c93483e45524328726ab7b1b4ce' 'vggt\models\vggt.py'
        Install-Source 'sam2' 'facebookresearch/sam2' '2b90b9f5ceec907a1c18123530e92e794ad901a4' 'sam2\build_sam.py'
        $previousBuildCuda = $env:SAM2_BUILD_CUDA
        try {
            $env:SAM2_BUILD_CUDA = '0'
            Run-Checked $uv @('pip','install','--python',$python,'--no-build-isolation','-e',(Join-Path $root 'vendor\sam2'))
        } finally { $env:SAM2_BUILD_CUDA = $previousBuildCuda }
    }
    Run-Checked $python @('-m','iotdesign.hardware','--profile',$Profile,'--output',$profileFile)
    Run-Checked $python @('-m','iotdesign.setup_models','--profile-file',$profileFile,'--data',$dataDir)
    if ($PrivateFirewall) {
        $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
        if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run elevated with -PrivateFirewall to add Private-network rules, or omit that option.' }
        foreach ($rule in @(@('IoTDesign HTTPS','TCP',$Port),@('IoTDesign mDNS','UDP',5353))) {
            if (!(Get-NetFirewallRule -DisplayName $rule[0] -ErrorAction SilentlyContinue)) {
                New-NetFirewallRule -DisplayName $rule[0] -Direction Inbound -Protocol $rule[1] -LocalPort $rule[2] -Action Allow -Profile Private | Out-Null
            }
        }
    }
    if ($SetupOnly) { Write-Host 'Setup complete. Run Start-IoTDesign.cmd to launch.'; return }
    if (!$PrivateFirewall) { Write-Host 'For phone LAN access, allow HTTPS and mDNS on a Private network. See README or run elevated with -PrivateFirewall.' }
    $launchArgs = @('-m','iotdesign.launcher','--profile-file',$profileFile,'--data',$dataDir,'--port',"$Port")
    if (!$NoBrowser) { $launchArgs += '--open' }
    Run-Checked $python $launchArgs
} catch {
    Write-Error "Windows setup failed: $($_.Exception.Message)"
    exit 1
} finally { Pop-Location }
