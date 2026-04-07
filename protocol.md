# Lynk Protocol Specification

## Message Format

All messages are newline-delimited JSON (`\n` terminated).
```json
{
  "type":    "<MESSAGE_TYPE>",
  "sender":  "<device_id>",
  "room":    "<room_name or null>",
  "target":  "<device_id or null>",
  "payload": "<string or base64 bytes>"
}
```

## Message Types

| TYPE | room | target | Description |
|------|------|--------|-------------|
| `BROADCAST` | null | null | Send to all connected devices |
| `ROOM` | required | null | Send to all devices in a room |
| `DIRECT` | null | required | Send to one specific device |
| `JOIN` | required | null | Join a room |
| `LEAVE` | required | null | Leave a room |
| `FILE_HEADER` | optional | optional | Announce incoming file transfer |
| `FILE_DATA` | null | null | Raw file bytes (follows FILE_HEADER) |
| `DISCOVER` | null | null | UDP beacon — server announces its IP |
| `ACK` | null | null | Confirm receipt |
| `ERROR` | null | null | Server reports an error to client |

## Examples

**Broadcast clipboard text:**
```json
{"type": "BROADCAST", "sender": "abc123", "room": null, "target": null, "payload": "Hello everyone"}
```

**Send to a room:**
```json
{"type": "ROOM", "sender": "abc123", "room": "team-a", "target": null, "payload": "Sync this text"}
```

**Direct message:**
```json
{"type": "DIRECT", "sender": "abc123", "room": null, "target": "xyz789", "payload": "Just for you"}
```

**Join a room:**
```json
{"type": "JOIN", "sender": "abc123", "room": "team-a", "target": null, "payload": null}
```

**File transfer header:**
```json
{"type": "FILE_HEADER", "sender": "abc123", "room": "team-a", "target": null, "payload": {"filename": "notes.txt", "size": 2048}}
```

## Device ID
Generated on first connect using `hashlib.md5(hostname + timestamp)`.
Persisted for the session only (no disk storage).

## UDP Discovery
Server broadcasts a UDP beacon every 2 seconds on port `55000`:
```json
{"type": "DISCOVER", "sender": "server", "room": null, "target": null, "payload": {"ip": "192.168.x.x", "port": 9000}}
```
Clients listen on startup and auto-connect to the first beacon received.

## Ports
| Service | Protocol | Port |
|---------|----------|------|
| TCP Relay | TCP | 9000 |
| UDP Discovery | UDP | 55000 |