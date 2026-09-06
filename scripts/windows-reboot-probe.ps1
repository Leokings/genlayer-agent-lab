$ErrorActionPreference = 'Stop'
$root = 'C:\LabTrial'
$config = Get-Content -LiteralPath "$root\guest.json" -Raw | ConvertFrom-Json
$serial = [string](Get-CimInstance Win32_ComputerSystemProduct).IdentifyingNumber
if ($config.nonce -cnotmatch '^[0-9a-f]{32}$' -or $serial.Trim() -cne ('GLAB-' + $config.nonce)) { throw 'guest_identity_required' }
& "$root\venv\Scripts\python.exe" -I "$root\windows_reboot_guest.py" --root $root --guest-id $config.nonce > "$root\probe-output.json" 2> "$root\probe-private.log"
exit $LASTEXITCODE
