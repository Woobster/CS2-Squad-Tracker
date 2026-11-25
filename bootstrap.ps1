# First-run setup: downloads embeddable Python and installs the bundled wheels.
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = 'Tls12'
$ProgressPreference = 'SilentlyContinue'

$root    = $PSScriptRoot
$runtime = Join-Path $root 'runtime'
$url     = 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip'
$zip     = Join-Path $root 'python-embed.zip'
$ready   = Join-Path $runtime '.ready'
# python.org's release manifest, with a SHA-256 for each Windows download
$manifestUrl = 'https://www.python.org/ftp/python/3.12.8/windows-3.12.8.json'

try {
    Write-Host '[1/4] Downloading Python 3.12 (about 25 MB)...'
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing

    # Check the download against the SHA-256 that python.org publishes for it
    $zipName = Split-Path -Leaf $url
    $expected = $null
    try {
        $manifest = Invoke-RestMethod -Uri $manifestUrl -UseBasicParsing
        $entry = $manifest.versions | Where-Object { $_.url -and $_.url.EndsWith('/' + $zipName) } | Select-Object -First 1
        if ($entry -and $entry.hash -and $entry.hash.sha256) { $expected = [string]$entry.hash.sha256 }
    } catch {
        $expected = $null
    }
    if ($expected) {
        $actual = (Get-FileHash -Path $zip -Algorithm SHA256).Hash
        if (-not [string]::Equals($actual, $expected, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Python download is corrupted, please run Start.bat again'
        }
        Write-Host 'Download checked.'
    } else {
        Write-Host "WARNING: could not get python.org's checksum for this download; skipping the check."
    }

    Write-Host '[2/4] Unpacking Python...'
    # An existing runtime without .ready is a half-finished earlier attempt: start over
    if (Test-Path $runtime) { Remove-Item -Recurse -Force $runtime }
    Expand-Archive -Path $zip -DestinationPath $runtime -Force
    Remove-Item -Force $zip

    Write-Host '[3/4] Configuring Python...'
    $pth = @('python312.zip', '.', '..\app', 'import site')
    Set-Content -Path (Join-Path $runtime 'python312._pth') -Value $pth -Encoding ASCII
    New-Item -ItemType Directory -Force -Path (Join-Path $runtime 'Lib\site-packages') | Out-Null

    Write-Host '[4/4] Installing libraries (offline, from the wheels folder)...'
    Push-Location $root
    try {
        & (Join-Path $runtime 'python.exe') 'install_wheels.py' 'wheels' 'runtime\Lib\site-packages'
        if ($LASTEXITCODE -ne 0) { throw "install_wheels.py exited with code $LASTEXITCODE" }
    } finally { Pop-Location }

    # Very last step: marks the runtime as complete
    Set-Content -Path $ready -Value 'ok' -Encoding ASCII
    Write-Host 'Setup finished.'
    exit 0
} catch {
    Write-Host ''
    Write-Host ("Setup failed: " + $_.Exception.Message) -ForegroundColor Red
    # Remove the half-finished runtime so the next start retries from scratch
    if (Test-Path $runtime) { Remove-Item -Recurse -Force $runtime -ErrorAction SilentlyContinue }
    if (Test-Path $zip) { Remove-Item -Force $zip -ErrorAction SilentlyContinue }
    exit 1
}
