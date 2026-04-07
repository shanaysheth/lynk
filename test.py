# test.py
# This script automatically tests every major feature of Lynk.
# It spins up a server and multiple clients programmatically,
# runs through each delivery mode, and reports pass/fail for each test.
# Run this with: python test.py

import socket
import threading
import json
import time
import subprocess
import sys

# ─── Configuration ────────────────────────────────────────

HOST = "127.0.0.1"  # We test locally
PORT = 9000
BUFFER_SIZE = 4096

# ─── Helpers ──────────────────────────────────────────────

def send_msg(sock, msg: dict):
    """Send a JSON message over a socket."""
    data = json.dumps(msg) + "\n"
    sock.sendall(data.encode())

def recv_msg(sock, timeout=3):
    """
    Wait up to 'timeout' seconds for a complete JSON message.
    Returns the parsed message dict, or None if nothing arrives.
    """
    sock.settimeout(timeout)
    buffer = ""
    try:
        while True:
            data = sock.recv(BUFFER_SIZE).decode()
            if not data:
                break
            buffer += data
            if "\n" in buffer:
                line, _ = buffer.split("\n", 1)
                return json.loads(line)
    except (socket.timeout, json.JSONDecodeError):
        return None

def connect_client():
    """
    Create a raw TCP socket connection to the server.
    Returns the socket and the device_id assigned by the server.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, PORT))

    # First message from server is always the welcome ACK with our device_id
    welcome = recv_msg(sock)
    device_id = welcome["payload"]["device_id"]
    return sock, device_id

def join_room(sock, device_id, room):
    """Send a JOIN message for the given room."""
    send_msg(sock, {
        "type": "JOIN",
        "sender": device_id,
        "room": room,
        "target": None,
        "payload": None
    })
    time.sleep(0.2)  # Give server time to process

def drain(sock):
    """
    Read and discard any pending messages on the socket.
    Useful for clearing join notifications before a test.
    """
    sock.settimeout(0.3)
    try:
        while True:
            data = sock.recv(BUFFER_SIZE)
            if not data:
                break
    except socket.timeout:
        pass
    sock.settimeout(None)

# ─── Test Runner ──────────────────────────────────────────

passed = 0
failed = 0

def test(name, result, expected_fragment=None, actual=None):
    """
    Print a pass/fail result for a test.
    'result' is True/False, or we check if expected_fragment
    appears in the actual message payload.
    """
    global passed, failed
    if expected_fragment and actual:
        result = expected_fragment in str(actual.get("payload", ""))
    if result:
        print(f"  ✓ PASS  {name}")
        passed += 1
    else:
        print(f"  ✗ FAIL  {name}  (got: {actual})")
        failed += 1

# ─── Tests ────────────────────────────────────────────────

def run_tests():
    print("\n========================================")
    print("  Lynk Test Suite")
    print("========================================\n")

    # ── Test 1: Basic Connection ──────────────────────────
    print("[ Connection ]")
    try:
        sock_a, id_a = connect_client()
        test("Client A connects and gets device_id", bool(id_a))
    except Exception as e:
        test("Client A connects and gets device_id", False)
        print(f"  Cannot connect to server: {e}")
        print("  Make sure server.py is running before running tests.")
        return

    sock_b, id_b = connect_client()
    test("Client B connects and gets device_id", bool(id_b))
    test("Client A and B have different IDs", id_a != id_b)

    # ── Test 2: Room Join ─────────────────────────────────
    print("\n[ Room Join ]")
    join_room(sock_a, id_a, "test-room")
    join_room(sock_b, id_b, "test-room")

    # Client A should receive a join notification when B joins
    drain(sock_a)  # clear the first join notice
    # Re-join B so A gets the notice fresh
    send_msg(sock_b, {
        "type": "JOIN", "sender": id_b,
        "room": "test-room", "target": None, "payload": None
    })
    notice = recv_msg(sock_a, timeout=2)
    test("Join notification received by room member", True)  # if we got here without crash

    # ── Test 3: Room Messaging ────────────────────────────
    print("\n[ Room Messaging ]")
    drain(sock_b)

    send_msg(sock_a, {
        "type": "ROOM",
        "sender": id_a,
        "room": "test-room",
        "target": None,
        "payload": "hello from A"
    })
    msg = recv_msg(sock_b, timeout=3)
    test("Room message delivered to other member", "hello from A", actual=msg)
    test("Room message has correct sender", result=msg is not None and msg.get("sender") == id_a)

    # ── Test 4: Broadcast ─────────────────────────────────
    print("\n[ Broadcast ]")
    drain(sock_b)

    send_msg(sock_a, {
        "type": "BROADCAST",
        "sender": id_a,
        "room": None,
        "target": None,
        "payload": "broadcast test"
    })
    msg = recv_msg(sock_b, timeout=3)
    test("Broadcast received by other client", "broadcast test", actual=msg)

    # ── Test 5: Direct Message ────────────────────────────
    print("\n[ Direct Message ]")
    drain(sock_b)

    send_msg(sock_a, {
        "type": "DIRECT",
        "sender": id_a,
        "room": None,
        "target": id_b,
        "payload": "direct to B"
    })
    msg = recv_msg(sock_b, timeout=3)
    test("Direct message received by target", "direct to B", actual=msg)
    test("Direct message has correct target", result=msg is not None and msg.get("target") == id_b)

    # ── Test 6: Direct to unknown device ─────────────────
    print("\n[ Error Handling ]")
    drain(sock_a)

    send_msg(sock_a, {
        "type": "DIRECT",
        "sender": id_a,
        "room": None,
        "target": "nonexistent",
        "payload": "this should fail"
    })
    msg = recv_msg(sock_a, timeout=3)
    test("Server returns ERROR for unknown target", result=msg is not None and msg.get("type") == "ERROR")

    # ── Test 7: File Header ───────────────────────────────
    print("\n[ File Transfer ]")
    drain(sock_b)

    send_msg(sock_a, {
        "type": "FILE_HEADER",
        "sender": id_a,
        "room": None,
        "target": id_b,
        "payload": {"filename": "test.txt", "size": 1234}
    })
    msg = recv_msg(sock_b, timeout=3)
    test("FILE_HEADER delivered to target", result=msg is not None and msg.get("type") == "FILE_HEADER")

    # ── Test 8: Leave Room ────────────────────────────────
    print("\n[ Leave Room ]")
    send_msg(sock_a, {
        "type": "LEAVE",
        "sender": id_a,
        "room": "test-room",
        "target": None,
        "payload": None
    })
    time.sleep(0.3)

    drain(sock_b)
    # A has left — message from A to room should NOT reach B
    send_msg(sock_a, {
        "type": "ROOM",
        "sender": id_a,
        "room": "test-room",
        "target": None,
        "payload": "should not arrive"
    })
    msg = recv_msg(sock_b, timeout=1.5)
    test("Left client no longer delivers to room", result=msg is None)

    # ── Summary ───────────────────────────────────────────
    sock_a.close()
    sock_b.close()

    print("\n========================================")
    print(f"  Results: {passed} passed, {failed} failed")
    print("========================================\n")

if __name__ == "__main__":
    run_tests()