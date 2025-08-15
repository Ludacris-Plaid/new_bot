import requests, os
token = os.environ.get("TELEGRAM_TOKEN")
print(requests.get(f"https://api.telegram.org/bot{token}/getMe").json())
