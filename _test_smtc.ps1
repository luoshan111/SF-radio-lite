[Windows.Media.Control.GlobalSystemMediaTransportControlsSession, Windows.Media.Control, ContentType = WindowsRuntime] | Out-Null
$manager = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync().GetAwaiter().GetResult()
$session = $manager.GetCurrentSession()
if ($session) {
    $props = $session.TryGetMediaPropertiesAsync().GetAwaiter().GetResult()
    Write-Output "$($props.Title)|$($props.Artist)|$($session.SourceAppUserModelId)"
} else {
    Write-Output 'NO_SESSION'
}
