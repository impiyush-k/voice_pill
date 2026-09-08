<#
.SYNOPSIS
    Bypasses the VPN for Groq API traffic by creating persistent Windows routing rules.
.DESCRIPTION
    Resolves api.groq.com to its current IP addresses, detects the local physical (non-VPN) gateway,
    and adds Windows static routes to route Groq traffic directly through the physical network gateway.
.PARAMETER Remove
    Removes the bypass routing rules instead of adding them.
.EXAMPLE
    # Run as Administrator to add the bypass routes:
    .\route_groq_bypass_vpn.ps1
.EXAMPLE
    # Run as Administrator to remove the bypass routes:
    .\route_groq_bypass_vpn.ps1 -Remove
#>

param (
    [switch]$Remove
)

# Ensure script is running as Administrator
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "This script must be run as Administrator! Please open PowerShell as Administrator and run the script again."
    Exit
}

$domain = "api.groq.com"

# 1. Resolve domain IPs
Write-Host "Resolving IPs for $domain..." -ForegroundColor Cyan
try {
    $ips = [System.Net.Dns]::GetHostAddresses($domain) | ForEach-Object { $_.IPAddressToString }
    # Also resolve a backup common Cloudflare IP range just in case
    # Groq uses Cloudflare, so adding Cloudflare IPs is good practice
    $ips += "172.64.149.20"
    $ips += "104.18.38.236"
    $ips = $ips | Select-Object -Unique
}
catch {
    Write-Warning "Could not resolve $domain. Using default Cloudflare/Groq anycast IPs."
    $ips = @("172.64.149.20", "104.18.38.236", "104.18.39.236", "172.64.150.20")
}

Write-Host "Target IPs: $($ips -join ', ')" -ForegroundColor Gray

if ($Remove) {
    Write-Host "Removing bypass routes for $domain..." -ForegroundColor Yellow
    foreach ($ip in $ips) {
        route delete $ip | Out-Null
        Write-Host "Removed route for $ip"
    }
    Write-Host "Done! Groq traffic will now go back through the VPN." -ForegroundColor Green
    Exit
}

# 2. Detect local physical gateway
Write-Host "Detecting physical network gateway..." -ForegroundColor Cyan
# Find default route (0.0.0.0/0) where NextHop is NOT 0.0.0.0 (VPNs typically use 0.0.0.0 next hop for virtual interfaces)
# and filter out virtual/VPN interface aliases
$routes = Get-NetRoute -DestinationPrefix "0.0.0.0/0" | Where-Object { 
    $_.NextHop -ne "0.0.0.0" -and 
    $_.InterfaceAlias -notmatch "VPN|TAP|TUN|WireGuard|Tailscale|Proton|Nord|Cisco|Forti|GlobalProtect|ZeroTier"
}

if (-not $routes) {
    # Fallback: just get the one with lowest Metric or active physical connection
    $routes = Get-NetRoute -DestinationPrefix "0.0.0.0/0" | Sort-Object RouteMetric
}

# Get first match
$physicalRoute = $routes | Select-Object -First 1

if (-not $physicalRoute) {
    Write-Error "Could not detect your physical gateway! Please check your network connection."
    Exit
}

$gateway = $physicalRoute.NextHop
$interfaceAlias = $physicalRoute.InterfaceAlias

Write-Host "Detected Physical Gateway: $gateway on interface '$interfaceAlias'" -ForegroundColor Green

# 3. Add routing rules
Write-Host "Adding persistent routing rules to bypass VPN..." -ForegroundColor Cyan
foreach ($ip in $ips) {
    # Delete existing route to avoid conflicts
    route delete $ip | Out-Null
    # Add persistent route (-p makes it survive restarts)
    $cmd = "route -p add $ip mask 255.255.255.255 $gateway"
    Invoke-Expression $cmd | Out-Null
    Write-Host "Route added: $ip -> $gateway"
}

Write-Host "Done! Test the connection now in Voice Pill by right-clicking and selecting 'Test Connection'." -ForegroundColor Green
