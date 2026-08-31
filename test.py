import requests

# -----------------------------------------
# WhatsApp Cloud API Configuration
# -----------------------------------------

ACCESS_TOKEN = "EAA6rtuUkSgIBOw1ZBKc0daGfX8SSbt86QetCckUtCodtMy2ZA44d9e0nrEUhZAsxaroHpX1217ROdLpkDRD1RwKa0VWMzgy5eMfIBv4WN1CYhXnAfXx7psCzgZB2xJkEZABscWDYYsKRwBHXMnfBdT905ZCLklGOnXS8tCaqsDGpoK7s5XlkOxgh4udFz67qw5aQZDZD"
PHONE_NUMBER_ID = "670517682822062"

url = f"https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/messages"

headers = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

# -----------------------------------------
# Payload
# -----------------------------------------

payload = {
    "messaging_product": "whatsapp",
    "to": "918959690512",
    "type": "template",
    "template": {
        "name": "custmer_otp",
        "language": {
            "code": "en_US"
        },
        "components": [
            {
                "type": "body",
                "parameters": [
                    {
                        "type": "text",
                        "text": "632763"
                    }
                ]
            },
            {
                "type": "button",
                "sub_type": "url",
                "index": "0",
                "parameters": [
                    {
                        "type": "text",
                        "text": "632763"
                    }
                ]
            }
        ]
    }
}

# -----------------------------------------
# Send Message
# -----------------------------------------

response = requests.post(
    url,
    headers=headers,
    json=payload
)

print("Status Code:", response.status_code)
print("Response:", response.json())