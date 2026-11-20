[CmdletBinding()]
param([string]$UserName = 'root', [switch]$PrepareOnly)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Run this script on the green Windows machine.' }
$directory = Join-Path $env:LOCALAPPDATA 'CrossZone\credentials'
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $directory /inheritance:r /grant:r "*${sid}:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict credential directory permissions.' }
$acl = Get-Acl -LiteralPath $directory
$rules = $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])
$fullControl = [System.Security.AccessControl.FileSystemRights]::FullControl
$granted = @($rules | Where-Object {
    $_.IdentityReference.Value -eq $sid -and
    $_.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
    ($_.FileSystemRights -band $fullControl) -eq $fullControl
})
if (-not $acl.AreAccessRulesProtected -or $granted.Count -eq 0) {
    throw 'Credential directory permission verification failed.'
}
if ($PrepareOnly) {
    Write-Output 'credential_directory_ready'
    return
}
$destination = Join-Path $directory '141.61.94.26.clixml'
$temporary = Join-Path $directory ([guid]::NewGuid().ToString() + '.tmp')
$password = Read-Host 'SSH password for root@141.61.94.26 (hidden)' -AsSecureString
if ($password.Length -eq 0) { throw 'Empty password was not saved.' }
try {
    $credential = [System.Management.Automation.PSCredential]::new($UserName, $password)
    $credential | Export-Clixml -LiteralPath $temporary
    $loaded = Import-Clixml -LiteralPath $temporary
    if ($loaded.UserName -ne $UserName -or $loaded.Password.Length -eq 0) { throw 'Credential verification failed.' }
    Move-Item -LiteralPath $temporary -Destination $destination -Force
    Write-Host "Encrypted credential saved: $destination"
    Write-Host 'Readable only by the same Windows account on this machine. No password was printed.'
} finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    $password.Dispose()
}
