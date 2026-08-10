import sys
sys.path.insert(0, r'D:\code\BIZHI')
from widgets.music.qq_music import detect_qq_music_song, search_song, get_lyrics, parse_lrc

keyword = detect_qq_music_song()
print(f'1. Detected: {keyword}')

if keyword:
    results = search_song(keyword, limit=1)
    if results:
        song = results[0]
        mid = song["songmid"]
        name = song["songname"]
        singer = song["singer"]
        print(f'2. Found: {name} - {singer} (mid={mid})')
        lyrics_data = get_lyrics(mid)
        if lyrics_data and lyrics_data.get("lrc"):
            parsed = parse_lrc(lyrics_data["lrc"])
            print(f'3. Lyrics: {len(parsed)} lines')
            if parsed:
                print(f'   First: {parsed[0]["text"]}')
                print(f'   Last:  {parsed[-1]["text"]}')
        else:
            print('3. No lyrics found')
    else:
        print('2. Search returned no results')
else:
    print('1. No song detected')
