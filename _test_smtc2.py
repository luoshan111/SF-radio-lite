import asyncio
from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager

async def check():
    manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
    session = manager.get_current_session()
    if session:
        props = await session.try_get_media_properties_async()
        timeline = session.get_timeline_properties()
        playback = session.get_playback_info()
        print(f"Title: {props.title}")
        print(f"Artist: {props.artist}")
        print(f"Position: {timeline.position.total_seconds():.1f}s")
        print(f"Duration: {timeline.end_time.total_seconds():.1f}s")
        print(f"Status: {playback.playback_status}")  # 4 = Playing
    else:
        print("No session")

asyncio.run(check())
