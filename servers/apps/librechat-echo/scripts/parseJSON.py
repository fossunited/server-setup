import json
from datetime import datetime


def formatDate(isodate):
    date_obj = datetime.fromisoformat(isodate.replace('Z', '+00:00'))
    return date_obj.strftime('%d-%b-%Y')


# Read the JSON files
with open('libreChat.users.json', 'r') as userFile:
    users = json.load(userFile)

with open('libreChat.transactions.json', 'r') as transactionFile:
    transactions = json.load(transactionFile)

# Create a dictionary of user IDs and usernames
user_dict = {user['_id']['$oid']: user['name'] for user in users}

# Process transactions and add usernames
new_transactions = []
for transaction in transactions:
    new_transaction = {
        "_id": transaction['_id'],
        "user": {
            "$oid": transaction['user']['$oid'],
            "name": user_dict.get(transaction['user']['$oid'], "Unknown")
        },
        "tokenType": transaction['tokenType'],
        "model": transaction['model'],
        "date": formatDate(transaction['createdAt']['$date']),
        "rawAmount": transaction['rawAmount'],
        "tokenValue": transaction['tokenValue'],
        "rate": transaction['rate']
    }
    new_transactions.append(new_transaction)

with open('libreChat_usageRecord.json', 'w') as f:
    json.dump(new_transactions, f, indent=2)

print("New transactions file created: new_transactions.json")
print(f"Number of processed transactions: {len(new_transactions)}")
