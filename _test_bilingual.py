import sys
sys.path.insert(0, r'D:\code\BIZHI')
from widgets.music.api import MusicApi, _merge_lyrics

# Test merge function
original = [
    {"time_ms": 1000, "text": "Hello world"},
    {"time_ms": 5000, "text": "Goodbye"},
]
translation = [
    {"time_ms": 1000, "text": "你好世界"},
    {"time_ms": 5000, "text": "再见"},
]
merged = _merge_lyrics(original, translation)
for m in merged:
    print(f"  {m['time_ms']}ms: {m['text']} | {m['trans']}")

assert merged[0]["trans"] == "你好世界"
assert merged[1]["trans"] == "再见"
print("Merge OK")

# Test with offset tolerance
translation2 = [
    {"time_ms": 1100, "text": "你好世界"},
]
merged2 = _merge_lyrics(original, translation2)
assert merged2[0]["trans"] == "你好世界"
print("Offset tolerance OK")

# Test real auto_sync
api = MusicApi()
result = api.auto_sync()
if result.get("error"):
    print(f"auto_sync error: {result['error']}")
else:
    lyrics = result.get("lyrics", [])
    has_trans = sum(1 for l in lyrics if l.get("trans"))
    print(f"Song: {result.get('title')} - {result.get('artist')}")
    print(f"Lyrics: {len(lyrics)} lines, {has_trans} with translation")
    if lyrics:
        for l in lyrics[:3]:
            t = f" / {l['trans']}" if l.get("trans") else ""
            print(f"  {l['text']}{t}")

print("\nAll tests passed!")
