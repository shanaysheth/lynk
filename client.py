# client.py
# This is the Lynk client application.
# It connects to the Lynk server, listens for incoming messages
# in a background thread, and lets the user send messages via
# a simple terminal interface.

import socket      # For TCP connection to server and UDP discovery listening
import threading   # For running the receive loop in the background
import json        # For encoding/decoding messages
import time        # For a small startup delay
import os          # For checking file paths and sizes
import base64      # For encoding file bytes into text so they fit in JSON

# ─── Configuration ────────────────────────────────────────

SERVER_PORT = 9000   # TCP port the server listens on
UDP_PORT    = 55000  # UDP port used for discovery beacons
BUFFER_SIZE = 4096   # How many bytes we read at a time from the socket

# ─── Session State ────────────────────────────────────────

device_id    = None
current_room = "general"

# ─── Discovery ────────────────────────────────────────────

def discover_server(timeout=3):
    """
    Listen for a UDP broadcast beacon from the server.
    The server sends its IP and port every 2 seconds.
    If we receive one within the timeout, we return those details.
    If not, we return None so the user can enter the IP manually.
    """
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    udp_sock.settimeout(timeout)
    udp_sock.bind(("", UDP_PORT))

    print("[Lynk] Searching for server on local network...")

    try:
        data, _ = udp_sock.recvfrom(1024)
        msg = json.loads(data.decode())

        if msg.get("type") == "DISCOVER":
            ip   = msg["payload"]["ip"]
            port = msg["payload"]["port"]
            print(f"[Lynk] Found server at {ip}:{port}")
            return ip, port

    except socket.timeout:
        pass

    finally:
        udp_sock.close()

    return None, None

# ─── Sending Messages ─────────────────────────────────────

def send_msg(sock, msg: dict):
    """
    Convert a Python dictionary to a JSON string and send it
    over the TCP socket. We append '\n' so the server knows
    where the message ends (newline-delimited protocol).
    """
    try:
        data = json.dumps(msg) + "\n"
        sock.sendall(data.encode())
    except Exception:
        pass

def send_room_msg(sock, text):
    """Send a text message to everyone in our current room."""
    send_msg(sock, {
        "type":    "ROOM",
        "sender":  device_id,
        "room":    current_room,
        "target":  None,
        "payload": text
    })

def send_broadcast_msg(sock, text):
    """Send a text message to every connected device."""
    send_msg(sock, {
        "type":    "BROADCAST",
        "sender":  device_id,
        "room":    None,
        "target":  None,
        "payload": text
    })

def send_direct_msg(sock, target_id, text):
    """Send a text message to one specific device by its ID."""
    send_msg(sock, {
        "type":    "DIRECT",
        "sender":  device_id,
        "room":    None,
        "target":  target_id,
        "payload": text
    })

def join_room(sock, room_name):
    """Tell the server we want to join a room, and update our local state."""
    global current_room
    current_room = room_name
    send_msg(sock, {
        "type":    "JOIN",
        "sender":  device_id,
        "room":    room_name,
        "target":  None,
        "payload": None
    })
    print(f"[Lynk] Joined room: {room_name}")

def leave_room(sock):
    """Tell the server we're leaving our current room."""
    send_msg(sock, {
        "type":    "LEAVE",
        "sender":  device_id,
        "room":    current_room,
        "target":  None,
        "payload": None
    })
    print(f"[Lynk] Left room: {current_room}")

def send_file(sock, target_id, filepath):
    """
    Send a file to a specific device.
    We encode the file as base64 so it can travel safely inside
    our JSON protocol without breaking the message framing.
    Base64 converts binary bytes into plain ASCII text characters,
    which means the whole file fits neatly inside our JSON payload.
    """
    if not os.path.exists(filepath):
        print(f"[!] File not found: {filepath}")
        return

    filename = os.path.basename(filepath)   # Just the filename, not full path
    filesize = os.path.getsize(filepath)    # Size in bytes

    # Read the entire file and encode it as base64 text
    with open(filepath, "rb") as f:
        raw_bytes = f.read()
        encoded = base64.b64encode(raw_bytes).decode("utf-8")

    print(f"[FILE] Sending {filename} ({filesize} bytes) → {target_id}...")

    # Send everything in one JSON message — no raw byte streaming needed
    send_msg(sock, {
        "type":    "FILE_HEADER",
        "sender":  device_id,
        "room":    None,
        "target":  target_id,
        "payload": {
            "filename": filename,
            "size":     filesize,
            "data":     encoded   # base64 encoded file content
        }
    })

    print(f"[FILE] Done. Sent {filename} to {target_id}")

# ─── Receiving Messages ───────────────────────────────────

def receive_loop(sock):
    """
    This function runs in a background thread.
    It continuously reads data from the server socket and
    displays incoming messages to the user.
    We use a buffer because TCP can deliver partial messages —
    we wait until we have a complete line before parsing.
    """
    buffer = ""

    while True:
        try:
            data = sock.recv(BUFFER_SIZE).decode()

            if not data:
                print("\n[!] Disconnected from server.")
                break

            buffer += data

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)

                if not line.strip():
                    continue

                try:
                    msg = json.loads(line)
                    display_message(msg)
                except json.JSONDecodeError:
                    pass

        except Exception:
            break

def display_message(msg):
    """
    Print an incoming message to the terminal in a readable format.
    The format depends on the message type.
    """
    global device_id

    msg_type = msg.get("type")
    sender   = msg.get("sender", "unknown")
    payload  = msg.get("payload", "")

    if msg_type == "ACK" and sender == "server" and isinstance(payload, dict):
        # This is the welcome message — it contains our assigned device_id
        device_id = payload.get("device_id", device_id)
        print(f"\n[Lynk] Connected! Your device ID: {device_id}")
        print(f"[Lynk] Default room: {current_room}")
        print_help()

    elif msg_type in ("BROADCAST", "ROOM", "DIRECT"):
        # A regular text message
        print(f"\n[{msg_type}] {sender}: {payload}")

    elif msg_type == "FILE_HEADER":
        # Someone is sending us a file
        filename = payload.get("filename", "received_file")
        size     = payload.get("size", 0)
        data     = payload.get("data")   # base64 encoded file content

        print(f"\n[FILE] Incoming from {sender}: {filename} ({size} bytes)")

        if data:
            # Decode base64 back to raw bytes and save to files_received folder
            os.makedirs("files_received", exist_ok=True)
            save_path = os.path.join("files_received", filename)

            with open(save_path, "wb") as f:
                f.write(base64.b64decode(data))

            print(f"[FILE] Saved to: {save_path}")

    elif msg_type == "ERROR":
        print(f"\n[ERROR] {payload}")

# ─── Terminal UI ──────────────────────────────────────────

def print_help():
    """Print the list of available commands to the terminal."""
    print("""
─────────────────────────────────────────
Commands:
  /join <room>            Join a room
  /leave                  Leave current room
  /room <message>         Send to current room
  /broadcast <message>    Send to all devices
  /direct <id> <message>  Send to one device
  /sendfile <id> <path>   Send a file to a device
  /quit                   Exit Lynk
  (just type anything)    Send to current room
─────────────────────────────────────────""")

# ─── Main ─────────────────────────────────────────────────

def main():
    # Step 1: Try to find the server automatically via UDP
    server_ip, server_port = discover_server(timeout=3)

    if not server_ip:
        server_ip   = input("Could not find server. Enter IP manually: ").strip()
        server_port = SERVER_PORT

    # Step 2: Connect to the server via TCP
    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_sock.connect((server_ip, server_port))

    # Step 3: Start the background thread to receive messages
    receiver = threading.Thread(target=receive_loop, args=(tcp_sock,), daemon=True)
    receiver.start()

    # Step 4: Wait briefly for the welcome ACK to arrive before we send JOIN
    time.sleep(0.5)

    # Step 5: Join the default room
    join_room(tcp_sock, current_room)

    # Step 6: Main input loop — read commands from the user
    while True:
        try:
            user_input = input()

            if not user_input.strip():
                continue

            if user_input.startswith("/join "):
                room_name = user_input[6:].strip()
                join_room(tcp_sock, room_name)

            elif user_input.startswith("/leave"):
                leave_room(tcp_sock)

            elif user_input.startswith("/broadcast "):
                message = user_input[11:].strip()
                send_broadcast_msg(tcp_sock, message)

            elif user_input.startswith("/direct "):
                parts = user_input[8:].split(" ", 1)
                if len(parts) == 2:
                    send_direct_msg(tcp_sock, parts[0], parts[1])
                else:
                    print("[!] Usage: /direct <device_id> <message>")

            elif user_input.startswith("/room "):
                message = user_input[6:].strip()
                send_room_msg(tcp_sock, message)

            elif user_input.startswith("/sendfile "):
                parts = user_input[10:].split(" ", 1)
                if len(parts) == 2:
                    send_file(tcp_sock, parts[0], parts[1])
                else:
                    print("[!] Usage: /sendfile <device_id> <filepath>")

            elif user_input == "/quit":
                break

            else:
                send_room_msg(tcp_sock, user_input)

        except (KeyboardInterrupt, EOFError):
            break

    tcp_sock.close()
    print("[Lynk] Disconnected. Goodbye!")

if __name__ == "__main__":
    main()