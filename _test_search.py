import requests, json

url = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
params = {"w": "expectations guccihighwaters", "format": "json", "p": 1, "n": 2, "cr": 1, "new_json": 1}
headers = {"Referer": "https://y.qq.com", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

resp = requests.get(url, params=params, headers=headers, timeout=8)
data = resp.json()
songs = data.get("data", {}).get("song", {}).get("list", [])
if songs:
    s = songs[0]
    print(json.dumps(s, ensure_ascii=False, indent=2))
else:
    print("No results")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:500])
