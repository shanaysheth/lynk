# server.py
# This is the central relay server for Lynk.
# It accepts multiple client connections using threads,
# routes messages based on their TYPE, and broadcasts
# its presence on the local network using UDP.

import socket      # For creating TCP and UDP network connections
import threading   # For handling multiple clients at the same time
import json        # For encoding/decoding messages as JSON
import hashlib     # For generating unique device IDs
import time        # For timestamps and sleep

# ─── Configuration ────────────────────────────────────────

HOST = "0.0.0.0"   # Listen on all available network interfaces
PORT = 9000         # TCP port clients connect to
UDP_PORT = 55000    # UDP port used for LAN discovery broadcasts

# ─── Shared State ─────────────────────────────────────────
# These three dictionaries store the live state of the server.
# They are shared across all threads, so we use a lock
# to prevent two threads from modifying them at the same time.

devices = {}   # Maps device_id -> socket (every connected client)
rooms   = {}   # Maps room_name -> [device_id, ...] (who is in each room)
names   = {}   # Maps device_id -> display name (human readable label)

# A lock ensures only one thread modifies shared data at a time
lock = threading.Lock()

# ─── Utility Functions ────────────────────────────────────

def generate_id(addr):
    """
    Create a short unique ID for a client based on their
    IP address, port, and the current time.
    We use MD5 just as a hashing tool (not for security).
    We take only the first 8 characters to keep it short.
    """
    raw = f"{addr[0]}:{addr[1]}:{time.time()}"
    return hashlib.md5(raw.encode()).hexdigest()[:8]


def send_msg(sock, msg: dict):
    """
    Serialize a Python dictionary to JSON and send it over a socket.
    We add a newline character at the end so the receiver knows
    where one message ends and the next begins (newline-delimited JSON).
    We wrap in try/except so a broken connection doesn't crash the server.
    """
    try:
        data = json.dumps(msg) + "\n"   # Convert dict to JSON string
        sock.sendall(data.encode())      # Send all bytes over the socket
    except Exception:
        pass  # Silently ignore send errors (client may have disconnected)


def get_local_ip():
    """
    Find the server's local IP address on the network.
    We do this by briefly connecting to an external address (Google DNS).
    We never actually send data — we just use the socket to ask
    the OS which local IP it would use for that route.
    """
    temp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        temp_sock.connect(("8.8.8.8", 80))
        return temp_sock.getsockname()[0]
    finally:
        temp_sock.close()

# ─── Message Routing ──────────────────────────────────────

def route(msg, sender_id):
    """
    Read the TYPE field of a message and deliver it correctly:
      - BROADCAST: send to every connected device
      - ROOM:      send to everyone in the specified room
      - DIRECT:    send to one specific device
      - JOIN:      add the sender to a room
      - LEAVE:     remove the sender from a room
      - FILE_HEADER: forward a file transfer announcement
    """

    message_type = msg.get("type")

    if message_type == "BROADCAST":
        # Send to every device except the sender
        with lock:
            # Build a list of sockets for everyone except the sender
            target_sockets = [
                sock for did, sock in devices.items()
                if did != sender_id
            ]
        for sock in target_sockets:
            send_msg(sock, msg)

    elif message_type == "ROOM":
        room_name = msg.get("room")
        with lock:
            # Get the list of device IDs in this room
            members = rooms.get(room_name, [])
            # Build list of sockets, excluding the sender
            target_sockets = [
                devices[did] for did in members
                if did != sender_id and did in devices
            ]
        for sock in target_sockets:
            send_msg(sock, msg)

    elif message_type == "DIRECT":
        target_id = msg.get("target")
        with lock:
            target_sock = devices.get(target_id)  # Look up the target's socket

        if target_sock:
            send_msg(target_sock, msg)
        else:
            # Target not found — send an error back to the sender
            with lock:
                sender_sock = devices.get(sender_id)
            if sender_sock:
                send_msg(sender_sock, {
                    "type": "ERROR",
                    "sender": "server",
                    "room": None,
                    "target": sender_id,
                    "payload": f"Device '{target_id}' not found."
                })

    elif message_type == "JOIN":
        room_name = msg.get("room")
        with lock:
            if room_name not in rooms:
                rooms[room_name] = []   # Create the room if it doesn't exist
            if sender_id not in rooms[room_name]:
                rooms[room_name].append(sender_id)  # Add client to the room

        print(f"[+] {names.get(sender_id)} joined room '{room_name}'")

        # Notify existing members that someone joined
        join_notice = {
            "type": "ROOM",
            "sender": "server",
            "room": room_name,
            "target": None,
            "payload": f"{names.get(sender_id)} joined the room."
        }
        with lock:
            members = rooms.get(room_name, [])
            target_sockets = [
                devices[did] for did in members
                if did != sender_id and did in devices
            ]
        for sock in target_sockets:
            send_msg(sock, join_notice)

    elif message_type == "LEAVE":
        room_name = msg.get("room")
        with lock:
            if room_name in rooms and sender_id in rooms[room_name]:
                rooms[room_name].remove(sender_id)  # Remove client from room
        print(f"[-] {names.get(sender_id)} left room '{room_name}'")

    elif message_type == "FILE_HEADER":
        # Forward the file announcement to the target device or room
        target_id = msg.get("target")
        room_name = msg.get("room")

        if target_id:
            with lock:
                target_sock = devices.get(target_id)
            if target_sock:
                send_msg(target_sock, msg)
        elif room_name:
            with lock:
                members = rooms.get(room_name, [])
                target_sockets = [
                    devices[did] for did in members
                    if did != sender_id and did in devices
                ]
            for sock in target_sockets:
                send_msg(sock, msg)

# ─── Client Handler ───────────────────────────────────────

def handle_client(conn, addr):
    """
    This function runs in its own thread for each connected client.
    It assigns the client a device_id, sends them a welcome message,
    then loops forever reading incoming messages and routing them.
    """

    # Generate a unique ID for this client
    device_id = generate_id(addr)

    # Register client in shared state
    with lock:
        devices[device_id] = conn
        names[device_id] = f"device-{device_id}"

    print(f"[+] {names[device_id]} connected from {addr}")

    # Send the client their assigned device_id
    send_msg(conn, {
        "type": "ACK",
        "sender": "server",
        "room": None,
        "target": device_id,
        "payload": {
            "device_id": device_id,
            "message": "Welcome to Lynk"
        }
    })

    # Buffer stores partial messages until a full line arrives
    buffer = ""

    try:
        while True:
            # Receive raw bytes from the client and decode to string
            data = conn.recv(4096).decode()

            if not data:
                # Empty data means the client disconnected
                break

            # Append new data to our buffer
            buffer += data

            # Process all complete messages (separated by newlines)
            while "\n" in buffer:
                # Split off the first complete line
                line, buffer = buffer.split("\n", 1)

                if not line.strip():
                    continue  # Skip empty lines

                try:
                    msg = json.loads(line)  # Parse JSON string into dict
                    print(f"  [{names[device_id]}] {msg.get('type')} → {str(msg.get('payload', ''))[:60]}")
                    route(msg, device_id)   # Route the message
                except json.JSONDecodeError:
                    print(f"  [!] Bad JSON from {device_id}: {line}")

    except ConnectionResetError:
        pass  # Client disconnected abruptly — that's okay

    finally:
        # Always clean up when a client disconnects
        with lock:
            for member_list in rooms.values():
                if device_id in member_list:
                    member_list.remove(device_id)
            devices.pop(device_id, None)
            names.pop(device_id, None)

        print(f"[-] {device_id} disconnected")
        conn.close()

# ─── UDP Discovery Beacon ─────────────────────────────────

def udp_beacon():
    """
    Broadcasts the server's IP and port over UDP every 2 seconds.
    Clients listen for this beacon on startup so they can
    auto-connect without the user needing to type an IP address.
    This uses UDP broadcast — one packet reaches all devices on the LAN.
    """
    # Create a UDP socket (not TCP — UDP is fire-and-forget, perfect for beacons)
    beacon_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # SO_BROADCAST allows us to send to the broadcast address
    beacon_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    local_ip = get_local_ip()

    # Build the beacon message once — we'll reuse it every 2 seconds
    beacon_msg = json.dumps({
        "type": "DISCOVER",
        "sender": "server",
        "room": None,
        "target": None,
        "payload": {"ip": local_ip, "port": PORT}
    }).encode()

    print(f"[Lynk] UDP beacon active — broadcasting {local_ip}:{PORT}")

    while True:
        # Send to 255.255.255.255 — this reaches all devices on the LAN
        beacon_sock.sendto(beacon_msg, ("<broadcast>", UDP_PORT))
        time.sleep(2)  # Wait 2 seconds before broadcasting again

# ─── Entry Point ──────────────────────────────────────────

def start_server():
    # Start the UDP beacon in a background daemon thread
    # (daemon=True means it stops automatically when the main program exits)
    beacon_thread = threading.Thread(target=udp_beacon, daemon=True)
    beacon_thread.start()

    # Create the main TCP server socket
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # SO_REUSEADDR lets us restart the server quickly without
    # waiting for the OS to release the port
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    server_sock.bind((HOST, PORT))   # Bind to our host and port
    server_sock.listen()             # Start listening for connections
    print(f"[Lynk] Server listening on {HOST}:{PORT}")

    while True:
        # Block here until a new client connects
        conn, addr = server_sock.accept()

        # Spawn a new thread for this client so we don't block other connections
        client_thread = threading.Thread(
            target=handle_client,
            args=(conn, addr),
            daemon=True
        )
        client_thread.start()

if __name__ == "__main__":
    start_server()