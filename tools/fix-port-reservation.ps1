$ErrorActionPreference = "Continue"
$log = Join-Path $env:TEMP "dimensional_portal-fix-ports.log"
Start-Transcript -Path $log -Force | Out-Null

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Host ("Admin: {0}" -f $isAdmin)
if (-not $isAdmin) {
    Write-Host "No admin rights, nothing changed."
    Stop-Transcript | Out-Null
    exit 1
}

Write-Host "=== BEFORE ==="
netsh int ipv4 show dynamicport tcp
netsh int ipv4 show excludedportrange protocol=tcp

Write-Host "=== stop winnat ==="
net stop winnat

Write-Host "=== restore default dynamic port range (49152 + 16384) ==="
netsh int ipv4 set dynamicport tcp start=49152 num=16384
netsh int ipv4 set dynamicport udp start=49152 num=16384

Write-Host "=== start winnat ==="
net start winnat
Start-Sleep -Seconds 3

Write-Host "=== AFTER ==="
netsh int ipv4 show dynamicport tcp
netsh int ipv4 show excludedportrange protocol=tcp

Write-Host "=== bind test ==="
foreach ($p in 6099, 6199, 8765, 16100, 26099) {
    try {
        $l = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $p)
        $l.Start(); $l.Stop()
        Write-Host ("  {0} -> OK" -f $p)
    } catch {
        Write-Host ("  {0} -> FAIL {1}" -f $p, $_.Exception.Message)
    }
}
Write-Host "DONE"
Start-Sleep -Seconds 8
Stop-Transcript | Out-Null
