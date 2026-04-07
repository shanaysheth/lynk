# Lynk — Reflection & Design Report
**Pranav Shinde & Shanay Sheth**
**Web Systems Principles**

---

## 1. Project Overview

Lynk is a real-time local network clipboard and file sync tool built entirely
using Python's standard library. It allows multiple devices on the same WiFi
network to exchange text and files instantly through a central relay server.

The core idea was simple: instead of emailing yourself files or using a cloud
service, devices on the same network should be able to talk to each other
directly and instantly. Lynk makes this possible using raw TCP sockets for
message delivery and UDP broadcasts for automatic server discovery.

---

## 2. Architecture

The system is split into two components:

**server.py** — A multi-threaded TCP relay server. It accepts connections from
multiple clients simultaneously, maintains a live registry of connected devices
and rooms, and routes messages based on their TYPE field. It also runs a UDP
beacon in a background thread to announce its presence on the LAN every 2
seconds.

**client.py** — A terminal-based client application. It listens for the UDP
beacon on startup to auto-connect, then maintains a persistent TCP connection
to the server. A background thread handles all incoming messages so the user
can type and receive at the same time without blocking.

The message lifecycle for every action is:
1. User types input in the client terminal
2. Client serializes it into a JSON frame and sends it over TCP
3. Server receives the frame, reads the TYPE field, and routes it
4. Target client(s) receive the frame and display it to the user

---

## 3. Design Decisions

### Why TCP for messages?
TCP guarantees that messages arrive in order and without loss. For a clipboard
sync tool this matters — if a message is dropped or arrives out of order, the
user sees corrupted or missing data. UDP does not provide these guarantees, so
we chose TCP for all message delivery.

### Why UDP for discovery?
UDP supports broadcast, meaning one packet reaches every device on the LAN
simultaneously. We use this for the server beacon — the server sends its IP
and port every 2 seconds and any client listening on that port can auto-connect
without the user needing to type an IP address manually. Reliability is not
needed here because if one beacon is missed, the next one arrives 2 seconds later.

### Why newline-delimited JSON?
TCP is a stream protocol — it has no built-in concept of where one message ends
and the next begins. We solve this with newline-delimited JSON: every message
is a single JSON string followed by a '\n' character. The receiver buffers
incoming bytes and only processes a message once it sees a complete line. This
is a simple and readable framing approach that is easy to debug.

### Why threading instead of async?
Python's threading module is part of the standard library and maps naturally
onto the mental model of "one thread per client." Each client handler runs
independently without affecting others. We used a threading.Lock() to protect
the shared state dictionaries (devices, rooms, names) from race conditions
where two threads might try to modify them at the same time.

---

## 4. Protocol Design

All messages follow this JSON structure:
```json
{
  "type":    "<MESSAGE_TYPE>",
  "sender":  "<device_id>",
  "room":    "<room_name or null>",
  "target":  "<device_id or null>",
  "payload": "<string or object>"
}
```

The TYPE field drives all routing logic on the server:

| TYPE | Behaviour |
|------|-----------|
| BROADCAST | Delivered to every connected device |
| ROOM | Delivered to all members of the specified room |
| DIRECT | Delivered to one specific device by ID |
| JOIN | Adds the sender to a room |
| LEAVE | Removes the sender from a room |
| FILE_HEADER | Announces an incoming file transfer |
| DISCOVER | UDP beacon — server announces its IP and port |
| ACK | Server confirms connection and assigns device_id |
| ERROR | Server reports a problem back to the sender |

---

## 5. Challenges & How We Solved Them

### Challenge 1 — Partial TCP messages
TCP does not guarantee that a full message arrives in one recv() call. Early
in testing we saw messages getting cut off mid-JSON, causing parse errors. We
solved this by maintaining a string buffer on both the server and client. We
only parse a message when we see a complete line ending in '\n', and carry
any remaining bytes over to the next recv() call.

### Challenge 2 — Thread safety
Because the server handles each client in a separate thread, multiple threads
can read and write the devices, rooms, and names dictionaries at the same time.
Without protection this causes race conditions. We solved this using a
threading.Lock() — any code that reads or writes shared state acquires the
lock first and releases it when done.

### Challenge 3 — Room sender validation
During testing we discovered a bug where a client that had left a room could
still send messages to that room and they would be delivered to remaining
members. We fixed this by adding a membership check in the ROOM routing block
on the server — if the sender is not in the room's member list, the message is
silently dropped.

### Challenge 4 — UDP discovery on Windows
Windows firewall sometimes blocks UDP broadcast packets. During development we
fell back to manual IP entry while testing on a single machine, then verified
UDP discovery worked correctly when both machines were on the same WiFi network
with firewall rules adjusted.

---

## 6. Testing

We wrote a scripted test harness in test.py that connects two raw TCP clients
to the server and exercises every feature programmatically. All 12 tests pass:

- Client connection and device ID assignment
- Room join and join notifications
- Room message delivery and sender verification
- Broadcast delivery
- Direct message delivery and target verification
- Error response for unknown target device
- FILE_HEADER delivery
- Room isolation after LEAVE (the bug we caught and fixed)

---

## 7. What We Learned

**Raw sockets are lower level than expected.** Before this project we had only
used HTTP or WebSocket libraries where framing is handled automatically. Working
directly with TCP taught us that you have to think carefully about how messages
are delimited, what happens when data arrives in partial chunks, and how to
handle disconnections gracefully.

**Concurrency requires discipline.** Threads sharing mutable state is a common
source of bugs. The threading.Lock() pattern felt verbose at first but it made
us think carefully about which data is shared and when it is safe to read or
modify it.

**Protocol design matters before coding.** Writing protocol.md first meant that
both the server and client were always working toward the same message format.
It also made debugging much easier — when something went wrong we could compare
the actual JSON against the spec immediately.

**UDP and TCP serve different purposes.** We used both in this project and saw
firsthand why: TCP for reliable ordered delivery of messages, UDP for cheap
LAN-wide announcements where the occasional lost packet does not matter.

---

## 8. Known Limitations

- **File transfer is basic** — we send the FILE_HEADER via JSON but stream raw
  bytes immediately after. The recipient currently only sees the header
  notification and does not automatically reassemble the file. A production
  version would need a dedicated file receiver thread.

- **No encryption** — messages are sent as plaintext JSON over the local
  network. Anyone on the same WiFi can intercept them with a packet sniffer.
  TLS would be the right solution for a production version.

- **No persistence** — device IDs are session-only. If a client disconnects
  and reconnects it gets a new ID, which breaks any direct message threads.

- **Single server** — there is no failover. If the machine running server.py
  goes offline, all clients lose connection.

---

## 9. Conclusion

Lynk achieves its core goal: real-time text and file sync across devices on a
local network using nothing but Python's standard library. Building it from raw
sockets gave us a genuine understanding of how networked applications work at
the transport layer — something that using a high-level library would have
hidden from us entirely.