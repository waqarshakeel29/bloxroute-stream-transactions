import streamlit as st
import pandas as pd
import sqlite3
import os
import asyncio
import websockets
import json
import threading
from datetime import datetime
import re
import dotenv
from streamlit_autorefresh import st_autorefresh


# Load environment variables
dotenv.load_dotenv()

# Streamlit configuration
st.set_page_config(layout="wide")

# Define the database path relative to the project directory
DB_PATH = os.path.join(os.path.dirname(__file__), "db.sqlite3")

# Initialize database and tables
def init_db():
    if not os.path.exists(DB_PATH):
        print("Initializing database...")
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS wallets (id INTEGER PRIMARY KEY, address TEXT UNIQUE, added_time TEXT)")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY, 
                hash TEXT, 
                from_address TEXT, 
                to_address TEXT, 
                value TEXT, 
                gas TEXT, 
                gasPrice TEXT, 
                nonce TEXT, 
                input TEXT,
                type TEXT,
                v TEXT,
                r TEXT,
                s TEXT
            )
        """)
        conn.commit()
        conn.close()
        print("Database initialized.")
    else:
        print("Database already exists.")

# Connect to the SQLite database at the specified path
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# Add wallet address to database
def add_wallet_to_db(wallet_address, added_time):
    conn = get_db_connection()
    conn.execute("INSERT OR IGNORE INTO wallets (address, added_time) VALUES (?, ?)", (wallet_address, added_time))
    conn.commit()
    conn.close()

# Fetch all monitored addresses with their addition times
def fetch_monitored_addresses():
    conn = get_db_connection()
    wallets = conn.execute("SELECT address, added_time FROM wallets").fetchall()
    conn.close()
    return wallets

# Delete a wallet from the database
def delete_wallet_from_db(wallet_address):
    conn = get_db_connection()
    conn.execute("DELETE FROM wallets WHERE address = ?", (wallet_address,))
    conn.commit()
    conn.close()

# Add a transaction to the database
def add_transaction_to_db(transaction_data):
    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO transactions (hash, from_address, to_address, value, gas, gasPrice, nonce, input, type, v, r, s) 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_data["hash"], 
            transaction_data["from"], 
            transaction_data["to"], 
            transaction_data["value"], 
            transaction_data["gas"], 
            transaction_data["gasPrice"],
            transaction_data.get("nonce"),
            transaction_data.get("input"),
            transaction_data.get("type"),
            transaction_data.get("v"),
            transaction_data.get("r"),
            transaction_data.get("s"),
        )
    )
    conn.commit()
    conn.close()

# Fetch all transactions from the database in descending order of ID
def fetch_transactions():
    conn = get_db_connection()
    transactions = conn.execute("SELECT * FROM transactions ORDER BY id DESC").fetchall()
    conn.close()
    return transactions

# Initialize the database on first run
init_db()

# Bloxroute configuration
BLOXROUTE_WS_URL = os.getenv("BLOXROUTE_WS_URL")  # Replace with actual bloxroute URL in .env file
BLOXROUTE_API_KEY = os.getenv("BLOXROUTE_API_KEY")  # Replace with your API key in .env file

# Validate Ethereum address format
def is_valid_eth_address(address):
    return re.match(r"^0x[a-fA-F0-9]{40}$", address) is not None

# WebSocket and Transaction Monitoring with Bloxroute
async def listen_for_transactions(stop_event):
    addresses = [addr["address"] for addr in fetch_monitored_addresses()]
    if not addresses:
        print("No addresses to monitor.")
        return

    filter_expression = (
        f"({{to}} IN {addresses}) OR ({{from}} IN {addresses})"
    )

    async with websockets.connect(
        BLOXROUTE_WS_URL,
        extra_headers={"Authorization": BLOXROUTE_API_KEY},
    ) as websocket:
        await websocket.send(
            json.dumps({
                "id": 1,
                "method": "subscribe",
                "params": [
                    "newTxs", 
                    {
                        "include": ["tx_hash", "tx_contents"],
                        "filters": filter_expression
                    }
                ]
            })
        )

        while not stop_event.is_set():
            try:
                message = await websocket.recv()
                transaction = json.loads(message)
                print(transaction)
                if transaction.get("params") and transaction["params"].get("result"):
                    process_transaction(transaction["params"]["result"]['txContents'])
            except Exception as e:
                print(f"Error in WebSocket loop: {e}")
                break

# Process and add transaction to database
def process_transaction(tx):
    monitored_addresses = [addr for addr, _ in fetch_monitored_addresses()]
    if tx["to"] in monitored_addresses or tx["from"] in monitored_addresses:
        add_transaction_to_db(tx)

# Start monitoring function that runs in the background
def start_monitoring(stop_event):
    asyncio.run(listen_for_transactions(stop_event))

# Function to start or stop the WebSocket monitoring in a separate thread
def toggle_monitoring():
    if "monitoring_thread" not in st.session_state:
        st.session_state["monitoring_thread"] = None
    if "stop_event" not in st.session_state:
        st.session_state["stop_event"] = threading.Event()

    if st.session_state["monitoring_thread"] is None or not st.session_state["monitoring_thread"].is_alive():
        # Start monitoring
        st.session_state["stop_event"].clear()
        st.session_state["monitoring_thread"] = threading.Thread(target=start_monitoring, args=(st.session_state["stop_event"],))
        st.session_state["monitoring_thread"].daemon = True
        st.session_state["monitoring_thread"].start()
        st.session_state["is_monitoring"] = True
    else:
        # Stop monitoring
        st.session_state["stop_event"].set()
        st.session_state["is_monitoring"] = False
    st.rerun()

# Display Transactions Function
def display_transactions():
    transactions = fetch_transactions()
    if transactions:
        # Convert the fetched transactions to a DataFrame and set column names
        df_transactions = pd.DataFrame(transactions)
        df_transactions.columns = [
            "Transaction ID", "Transaction Hash", "From Address", "To Address", 
            "Value (ETH)", "Gas", "Gas Price", "Nonce", "Input", "Type", "V", "R", "S"
        ]
        st.write(df_transactions)
    else:
        st.write("No transactions found.")

# Streamlit UI
st.subheader("Transaction Monitoring")

# Toggle monitoring button with dynamic label
if "is_monitoring" not in st.session_state:
    st.session_state["is_monitoring"] = False

if st.button("Stop Monitoring" if st.session_state["is_monitoring"] else "Start Monitoring"):
    toggle_monitoring()

# Create "Refresh Transactions" button
# if st.button("Refresh Transactions"):
#     display_transactions()
# else:
display_transactions()

# Wallet Management Section
st.subheader("Ethereum Wallet Management")
wallet_address = st.text_input("Enter Ethereum Wallet Address")
if st.button("Add Wallet Address"):
    if wallet_address and is_valid_eth_address(wallet_address):
        add_wallet_to_db(wallet_address, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        st.success(f"Wallet Address '{wallet_address}' added for monitoring.")
        st.rerun()  # Refresh the UI to show the updated list
    else:
        st.error("Please enter a valid Ethereum wallet address.")

# Display List of Monitored Wallets in Table Format with Delete Icon
monitored_addresses = fetch_monitored_addresses()
if monitored_addresses:
    st.subheader("Monitored Wallet Addresses")
    df_wallets = pd.DataFrame(monitored_addresses, columns=["Wallet Address", "Added Time"])

    # Adding a delete button for each wallet address
    for i, row in df_wallets.iterrows():
        col1, col2, col3 = st.columns([3, 3, 1])
        col1.write(row["Wallet Address"])
        col2.write(row["Added Time"])
        if col3.button("Delete", key=row["Wallet Address"]):
            delete_wallet_from_db(row["Wallet Address"])
            st.success(f"Wallet Address '{row['Wallet Address']}' has been removed.")
            st.rerun()  # Refresh the UI after deletion
else:
    st.write("No wallet addresses are currently being monitored.")

count = st_autorefresh(interval=5000)