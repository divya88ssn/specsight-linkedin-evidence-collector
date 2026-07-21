import os
import requests

query = 'site:linkedin.com/posts continous validation of acceptance criteria'

r = requests.post(
    "https://google.serper.dev/search",
    headers={
        "X-API-KEY": os.environ["SERPER_API_KEY"],
        "Content-Type": "application/json",
    },
    json={
        "q": query,
        "num": 10,
    },
    timeout=(10, 30),
)

print(r.status_code)
print(r.text[:2000])
