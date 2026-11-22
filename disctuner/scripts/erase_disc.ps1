<#
Quick-erase a rewritable disc (CD-RW, DVD-RW, DVD+RW) through Windows' IMAPI2.

  powershell -ExecutionPolicy Bypass -File scripts\erase_disc.ps1 -Drive E:

Nothing is touched unless Windows says it can erase the disc that is in the drive.
#>
param([Parameter(Mandatory = $true)][string]$Drive)
$ErrorActionPreference = "Stop"

$master = New-Object -ComObject IMAPI2.MsftDiscMaster2
$recorder = $null
foreach ($id in $master) {
    $r = New-Object -ComObject IMAPI2.MsftDiscRecorder2
    $r.InitializeDiscRecorder($id)
    if ($r.VolumePathNames -contains ($Drive.TrimEnd('\') + '\')) { $recorder = $r }
}
if (-not $recorder) { throw "no IMAPI2 recorder at $Drive" }

$erase = New-Object -ComObject IMAPI2.MsftDiscFormat2Erase
$erase.Recorder = $recorder
$erase.ClientName = "DiscTuner"
if (-not $erase.IsRecorderSupported($recorder)) { throw "Windows cannot erase with this drive" }
if (-not $erase.IsCurrentMediaSupported($recorder)) { throw "Windows cannot erase this kind of disc" }

$erase.FullErase = $false
"Erasing ..."
$erase.EraseMedia()
"Erased"
