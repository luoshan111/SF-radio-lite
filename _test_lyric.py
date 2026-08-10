import requests, json

url = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
params = {"songmid": "002UuFi00E9JCb", "format": "json", "nobase64": 1}
headers = {"Referer": "https://y.qq.com", "User-Agent": "Mozilla/5.0"}

resp = requests.get(url, params=params, headers=headers, timeout=8)
data = resp.json()
lyric = data.get("lyric", "")
if lyric:
    lines = lyric.split("\n")[:5]
    for l in lines:
        print(l)
    print(f"... total {len(lyric)} chars")
else:
    print("No lyrics")
    print(json.dumps(data, ensure_ascii=False)[:300])
