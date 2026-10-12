# Install the pinned official SAM 2 source in the existing Python environment.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$samRepo = Join-Path $projectRoot 'vendor\sam2'
$samRevision = '2b90b9f5ceec907a1c18123530e92e794ad901a4'
if (!(Test-Path -LiteralPath $pythonExe)) { throw 'Create the project Python environment first.' }
if (!(Test-Path -LiteralPath $samRepo)) {
    git clone --depth 1 https://github.com/facebookresearch/sam2.git $samRepo
    if ($LASTEXITCODE -ne 0) { throw 'SAM 2 clone failed.' }
    git -C $samRepo fetch --depth 1 origin $samRevision
    if ($LASTEXITCODE -ne 0) { throw 'Pinned SAM 2 revision fetch failed.' }
    git -C $samRepo checkout --detach $samRevision
    if ($LASTEXITCODE -ne 0) { throw 'Pinned SAM 2 checkout failed.' }
}
$currentRevision = git -C $samRepo rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $currentRevision -ne $samRevision) {
    throw "Existing vendor/sam2 checkout must match $samRevision. Preserve local changes before updating it."
}
$previousBuildSetting = $env:SAM2_BUILD_CUDA
try {
    $env:SAM2_BUILD_CUDA = '0'
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv pip install --python $pythonExe --no-build-isolation -e $samRepo
    } else {
        & $pythonExe -m pip install --no-build-isolation -e $samRepo
    }
    if ($LASTEXITCODE -ne 0) { throw 'SAM 2 installation failed. Requires CUDA PyTorch and setuptools.' }
} finally {
    if ($null -eq $previousBuildSetting) { Remove-Item Env:SAM2_BUILD_CUDA -ErrorAction SilentlyContinue }
    else { $env:SAM2_BUILD_CUDA = $previousBuildSetting }
}
Write-Output 'SAM 2 installed. Official small checkpoint downloads on the first segmentation run.'
