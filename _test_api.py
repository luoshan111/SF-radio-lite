import sys
sys.path.insert(0, r'D:\code\BIZHI')

# Test compilation
from widgets.music.qq_music import search_song, get_lyrics, parse_lrc, detect_qq_music_song
from widgets.music.api import MusicApi

print("=== Imports OK ===")

# Test parse_lrc with offset
test_lrc = "[offset:+500]\n[00:01.00]Line one\n[00:05.00]Line two"
offset, lines = parse_lrc(test_lrc)
print(f"parse_lrc: offset={offset}, lines={len(lines)}")
assert offset == 500
assert len(lines) == 2
assert lines[0]["time_ms"] == 1000
print("parse_lrc OK")

# Test MusicApi auto_sync
api = MusicApi()
result = api.auto_sync()
print(f"auto_sync keys: {list(result.keys())}")
if result.get("error"):
    print(f"auto_sync error: {result['error']}")
else:
    print(f"  title: {result.get('title')}")
    print(f"  artist: {result.get('artist')}")
    print(f"  position_ms: {result.get('position_ms')}")
    print(f"  duration_ms: {result.get('duration_ms')}")
    print(f"  is_playing: {result.get('is_playing')}")
    print(f"  lyrics count: {len(result.get('lyrics', []))}")
    print(f"  lrc_offset: {result.get('lrc_offset')}")
    print(f"  user_offset: {result.get('user_offset')}")

# Test set_user_offset
r = api.set_user_offset(-500)
print(f"set_user_offset: {r}")

print("\n=== All tests passed ===")
